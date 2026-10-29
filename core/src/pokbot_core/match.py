"""Runs a match: many hands between a fixed set of players, with timeouts, illegal-action
handling, duplicate seating for heads-up, and an event stream for UIs and spectators."""

from __future__ import annotations

import asyncio
import inspect
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field

from .engine.cards import cards_str, hand_seed
from .engine.holdem import Action
from .engine.variants import get_variant
from .players import Player
from .protocol import ProtocolError, message, parse_reply

EventSink = Callable[[dict], Awaitable[None] | None]


@dataclass
class MatchConfig:
    game: str = "holdem"
    hands: int = 10_000
    stack: int = 200
    blinds: tuple[int, int] = (1, 2)
    seed: int | None = None
    time_ms: int = 5  # per-decision compute cap for bots, in milliseconds
    timebank_ms: int = 1000  # per-match reserve for GC pauses and cold starts
    human_time_ms: int = 60_000
    duplicate: bool = True  # heads-up only: replay each deck with seats swapped
    # Split the match into equal sets of hands; whoever wins the most chips in a set wins it.
    # Tables rank by sets won first (see tournament.rank_table). 1 = the whole match is one set.
    sets: int = 1


@dataclass
class PlayerStats:
    name: str
    kind: str
    hands: int = 0
    chips: int = 0
    vpip: int = 0
    pfr: int = 0
    wins: int = 0
    sets_won: int = 0
    illegal: int = 0
    timeouts: int = 0
    errors: int = 0
    think_ms: list[float] = field(default_factory=list, repr=False)

    def bb_per_100(self, bb: int) -> float:
        return self.chips / bb / self.hands * 100 if self.hands else 0.0

    def think_percentile(self, q: float) -> float:
        if not self.think_ms:
            return 0.0
        data = sorted(self.think_ms)
        return data[min(len(data) - 1, int(q * len(data)))]

    def to_dict(self, bb: int) -> dict:
        d = asdict(self)
        d.pop("think_ms")
        d["bb_per_100"] = round(self.bb_per_100(bb), 2)
        d["think_p50_ms"] = round(self.think_percentile(0.5), 2)
        d["think_p99_ms"] = round(self.think_percentile(0.99), 2)
        return d


class Match:
    def __init__(
        self,
        players: list[Player],
        config: MatchConfig | None = None,
        on_event: EventSink | None = None,
        match_id: str | None = None,
        deadline: float | None = None,
    ) -> None:
        self.config = config or MatchConfig()
        self.variant = get_variant(self.config.game)
        if not 2 <= len(players) <= self.variant.max_seats:
            raise ValueError(f"{self.variant.name} needs 2-{self.variant.max_seats} players")
        self.players = players
        self.on_event = on_event
        self.match_id = match_id or "m_" + secrets.token_hex(4)
        self.seed = self.config.seed if self.config.seed is not None else secrets.randbits(63)
        self.stats = [PlayerStats(p.name, p.kind) for p in players]
        self.timebank = [self.config.timebank_ms for _ in players]
        self.hands_played = 0
        self.started_at = 0.0
        self._stop = False
        self.deadline = deadline  # time.monotonic() after which no new hand starts
        self.sets_played = 0
        self._set_base: list[int] = []  # each player's chips when the current set began
        self._set_hands = 0
        self.stopped_early = False

    def stop(self) -> None:
        self._stop = True

    async def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        result = self.on_event(event)
        if inspect.isawaitable(result):
            await result

    def _names(self, order: list[int]) -> list[str]:
        return [self.players[i].name for i in order]

    async def run(self) -> dict:
        cfg = self.config
        n = len(self.players)
        duplicate = cfg.duplicate and n == 2 and cfg.hands >= 2
        half = cfg.hands // 2 if duplicate else cfg.hands
        self.started_at = time.perf_counter()

        await asyncio.gather(*(p.start() for p in self.players))
        try:
            for i, p in enumerate(self.players):
                await p.notify(
                    message(
                        "match_start",
                        match_id=self.match_id,
                        game=cfg.game,
                        seat=i,
                        players=self._names(list(range(n))),
                        hands=cfg.hands,
                        starting_stack=cfg.stack,
                        blinds=list(cfg.blinds),
                    )
                )
            await self._emit(
                {
                    "type": "match_start",
                    "match_id": self.match_id,
                    "game": cfg.game,
                    "players": [{"name": p.name, "kind": p.kind} for p in self.players],
                    "hands": cfg.hands,
                    "sets": max(1, cfg.sets),
                    "blinds": list(cfg.blinds),
                    "seed": self.seed,
                }
            )
            sets = max(1, cfg.sets)
            set_size = -(-cfg.hands // sets)  # ceil: the last set may be shorter
            self._set_base = [0] * n
            # With a deadline, each set also gets an equal slice of the time left, so every set is
            # played even when the table can't fit all its hands: a set ends at `set_size` hands or
            # at its slice, whichever comes first.
            began = time.monotonic()
            slice_s = (self.deadline - began) / sets if self.deadline is not None else None
            for hand_no in range(cfg.hands):
                if self._stop:
                    break
                if self.deadline is not None and time.monotonic() >= self.deadline:
                    self.stopped_early = True
                    break
                if duplicate and hand_no >= half:
                    base, order = hand_no - half, list(reversed(range(n)))
                else:
                    base, order = hand_no, list(range(n))
                await self._play_hand(hand_no, base, order)
                self._set_hands += 1
                out_of_time = slice_s is not None and time.monotonic() >= began + slice_s * (self.sets_played + 1)
                if self._set_hands == set_size or (out_of_time and self.sets_played < sets - 1):
                    await self._end_set()
            if self._set_hands:  # stopped early or a short last set: it still counts
                await self._end_set()
        finally:
            summary = self.summary()
            for p in self.players:
                await p.notify(message("match_end", result=summary))
            await self._emit({"type": "match_end", **summary})
            await asyncio.gather(*(p.close() for p in self.players), return_exceptions=True)
        return summary

    async def _end_set(self) -> None:
        """Close the current set: the most chips won in it wins (ties: every leader gets it)."""
        won = [s.chips - b for s, b in zip(self.stats, self._set_base)]
        best = max(won)
        winners = [i for i, w in enumerate(won) if w == best]
        for i in winners:
            self.stats[i].sets_won += 1
        self.sets_played += 1
        self._set_base = [s.chips for s in self.stats]
        hands, self._set_hands = self._set_hands, 0
        await self._emit(
            {
                "type": "set_end",
                "set": self.sets_played,
                "sets": max(1, self.config.sets),
                "hands": hands,
                "winners": [self.stats[i].name for i in winners],
                "chips": {s.name: w for s, w in zip(self.stats, won)},
                "sets_won": {s.name: s.sets_won for s in self.stats},
            }
        )

    async def _play_hand(self, hand_no: int, base: int, order: list[int]) -> None:
        cfg = self.config
        n = len(order)
        button = base % n
        hand = self.variant.hand_cls([cfg.stack] * n, button, cfg.blinds, hand_seed(self.seed, base))
        names = self._names(order)

        for seat, pi in enumerate(order):
            await self.players[pi].notify(
                message(
                    "hand_start",
                    hand_no=hand_no,
                    seat=seat,
                    hole=cards_str(hand.seats[seat].hole),
                    button=button,
                    players=names,
                    stacks=[s.stack for s in hand.seats],
                )
            )
        await self._emit(
            {
                "type": "hand_start",
                "hand_no": hand_no,
                "players": names,
                "order": order,
                "button": button,
                "holes": [cards_str(s.hole) for s in hand.seats],
                "stacks": [s.stack for s in hand.seats],
                "bets": [s.street_bet for s in hand.seats],
            }
        )

        while not hand.is_over:
            seat = hand.to_act
            pi = order[seat]
            action = await self._decide(hand, hand_no, seat, pi)
            hand.apply(seat, action)
            await self._emit(
                {
                    "type": "action",
                    "hand_no": hand_no,
                    "seat": seat,
                    "name": names[seat],
                    "action": action.kind,
                    "amount": hand.history[-1]["amount"],
                    "street": hand.history[-1]["street"],
                    "board": cards_str(hand.board),
                    "pot": hand.pot,
                    "stacks": [s.stack for s in hand.seats],
                    "bets": [s.street_bet for s in hand.seats],
                    "folded": [s.folded for s in hand.seats],
                }
            )

        res = hand.result
        assert res is not None
        self.hands_played += 1
        for seat, pi in enumerate(order):
            st = self.stats[pi]
            st.hands += 1
            st.chips += res.deltas[seat]
            if seat in res.winners:
                st.wins += 1
            pre = [h for h in hand.history if h["seat"] == seat and h["street"] == "preflop"]
            if any(h["action"] in ("C", "R") for h in pre):
                st.vpip += 1
            if any(h["action"] == "R" for h in pre):
                st.pfr += 1

        end = {
            "hand_no": hand_no,
            "players": names,
            "deltas": res.deltas,
            "winners": res.winners,
            "pots": res.pots,
            "board": res.board,
            "showdown": {str(k): v for k, v in res.showdown.items()},
        }
        for seat, pi in enumerate(order):
            await self.players[pi].notify(message("hand_end", seat=seat, **end))
        await self._emit(
            {
                "type": "hand_end",
                **end,
                "order": order,
                "history": hand.history,
                "totals": {s.name: s.chips for s in self.stats},
                "hands_played": self.hands_played,
                "elapsed": time.perf_counter() - self.started_at,
            }
        )
        await asyncio.sleep(0)  # let UIs and other tables run between hands

    async def _decide(self, hand, hand_no: int, seat: int, pi: int) -> Action:
        player = self.players[pi]
        st = self.stats[pi]
        req = hand.request()
        per_action = player.time_ms or (
            self.config.human_time_ms if player.kind == "human" else self.config.time_ms
        )
        limit_ms = per_action + (0 if player.kind == "human" else self.timebank[pi])
        msg = message("action_request", hand_no=hand_no, time_ms=per_action, **req)

        start = time.perf_counter()
        try:
            raw = await asyncio.wait_for(player.act(msg), timeout=limit_ms / 1000)
            action = parse_reply(raw, req)
        except TimeoutError:
            st.timeouts += 1
            action = None
        except ProtocolError:
            st.illegal += 1
            action = None
        except (ConnectionError, OSError):
            st.errors += 1
            action = None
        elapsed_ms = (time.perf_counter() - start) * 1000
        st.think_ms.append(elapsed_ms)
        if elapsed_ms > per_action and player.kind != "human":
            self.timebank[pi] = max(0, self.timebank[pi] - int(elapsed_ms - per_action))

        if action is None:
            return Action("X") if "X" in req["legal"] else Action("F")
        safe = hand.coerce(seat, action)
        if safe is not action:
            st.illegal += 1
        return safe

    def summary(self) -> dict:
        bb = self.config.blinds[1]
        elapsed = time.perf_counter() - self.started_at if self.started_at else 0.0
        return {
            "match_id": self.match_id,
            "game": self.config.game,
            "seed": self.seed,
            "hands": self.hands_played,
            "stopped_early": self.stopped_early,
            "sets": self.sets_played,
            "elapsed": round(elapsed, 3),
            "players": [s.to_dict(bb) for s in self.stats],
        }
