"""Knockout tournament: seat entrants at tables, the top 2 entrants of each table advance,
repeat until one table is left; that final table's top entrant is the champion.

House bots ([BOT]) only fill empty seats. They play every hand but never advance, so the
tournament always shrinks and always ends with exactly one real winner.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import random
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from .match import EventSink, Match, MatchConfig
from .players import HouseBot, Player

ADVANCE_PER_TABLE = 2


@dataclass
class TournamentConfig:
    table_size: int = 6
    bot_hands: int = 10_000  # hands per table when every seat is a bot
    human_hands: int = 30  # hands per table when a human is seated
    match: MatchConfig = field(default_factory=MatchConfig)
    seed: int | None = None

    def __post_init__(self) -> None:
        if self.table_size <= ADVANCE_PER_TABLE:
            raise ValueError(f"table_size must be > {ADVANCE_PER_TABLE} so the field shrinks")


def next_start(now: datetime, interval_s: int) -> datetime:
    """Next wall-clock boundary: interval 1800 -> :00 and :30 of every hour (UTC epoch aligned)."""
    ts = now.timestamp()
    return datetime.fromtimestamp((math.floor(ts / interval_s) + 1) * interval_s, tz=UTC)


def seat_tables(entrants: list[Player], table_size: int, rng: random.Random) -> list[list[Player]]:
    """Spread entrants as evenly as possible over the fewest tables."""
    pool = list(entrants)
    rng.shuffle(pool)
    count = max(1, math.ceil(len(pool) / table_size))
    return [pool[i::count] for i in range(count)]


def rank_table(summary: dict, seated: list[Player]) -> list[int]:
    """Indexes into `seated`, best first: chips won, then hands won, then seat order."""
    stats = summary["players"]
    return sorted(range(len(seated)), key=lambda i: (-stats[i]["chips"], -stats[i]["wins"], i))


class Tournament:
    def __init__(
        self,
        entrants: list[Player],
        config: TournamentConfig | None = None,
        on_event: EventSink | None = None,
        house_factory: Callable[[int, int], Player] | None = None,
        tournament_id: str | None = None,
    ) -> None:
        if not entrants:
            raise ValueError("a tournament needs at least one entrant")
        names = [p.name for p in entrants]
        if len(set(names)) != len(names):
            raise ValueError("entrant names must be unique")
        self.entrants = entrants
        self.config = config or TournamentConfig()
        self.on_event = on_event
        self.id = tournament_id or "t_" + secrets.token_hex(4)
        self.rng = random.Random(self.config.seed)
        self.house_factory = house_factory or (
            lambda round_no, k: HouseBot(
                HOUSE_NAMES[k % len(HOUSE_NAMES)], style=("tight", "caller")[k % 2], seed=self.rng.random()
            )
        )
        self.matches: dict[str, Match] = {}
        self.results: list[dict] = []
        self.champion: Player | None = None

    async def _emit(self, event: dict) -> None:
        if self.on_event is None:
            return
        out = self.on_event(event)
        if inspect.isawaitable(out):
            await out

    def _fill(self, table: list[Player], round_no: int) -> list[Player]:
        """Fill empty seats with house bots unless every seated player is human."""
        if all(p.kind == "human" for p in table) and len(table) >= 2:
            return table
        seated = list(table)
        k = 0
        while len(seated) < self.config.table_size:
            bot = self.house_factory(round_no, k)
            k += 1
            if bot.name not in {p.name for p in seated}:
                seated.append(bot)
        return seated

    async def run(self) -> dict:
        alive = list(self.entrants)
        await self._emit(
            {
                "type": "tournament_start",
                "tournament_id": self.id,
                "entrants": [{"name": p.name, "kind": p.kind} for p in alive],
                "table_size": self.config.table_size,
                "started_at": datetime.now(UTC).isoformat(),
            }
        )

        round_no = 0
        while True:
            round_no += 1
            final = len(alive) <= self.config.table_size
            tables = [alive] if final else seat_tables(alive, self.config.table_size, self.rng)
            seated_tables = [self._fill(t, round_no) for t in tables]
            await self._emit(
                {
                    "type": "round_start",
                    "round": round_no,
                    "final": final,
                    "tables": [[p.name for p in t] for t in seated_tables],
                }
            )
            results = await asyncio.gather(
                *(self._play_table(round_no, ti, t, final) for ti, t in enumerate(seated_tables))
            )
            alive = [p for advancing in results for p in advancing]
            await self._emit(
                {"type": "round_end", "round": round_no, "advancing": [p.name for p in alive]}
            )
            if final or len(alive) <= 1:
                break

        self.champion = alive[0] if alive else None
        result = {
            "type": "tournament_end",
            "tournament_id": self.id,
            "winner": self.champion.name if self.champion else None,
            "winner_kind": self.champion.kind if self.champion else None,
            "rounds": round_no,
            "tables": self.results,
        }
        await self._emit(result)
        return result

    async def _play_table(
        self, round_no: int, table_no: int, seated: list[Player], final: bool
    ) -> list[Player]:
        table_id = f"r{round_no}t{table_no + 1}"
        has_human = any(p.kind == "human" for p in seated)
        hands = self.config.human_hands if has_human else self.config.bot_hands
        cfg = replace(self.config.match, hands=hands, seed=self.rng.getrandbits(63))

        async def forward(event: dict) -> None:
            await self._emit({"type": "table", "table": table_id, "round": round_no, "event": event})

        match = Match(seated, cfg, on_event=forward, match_id=f"{self.id}-{table_id}")
        self.matches[table_id] = match
        summary = await match.run()
        order = rank_table(summary, seated)
        real = [i for i in order if seated[i].kind != "house"]
        advancing = [seated[i] for i in real[: 1 if final else ADVANCE_PER_TABLE]]
        standings = [
            {**summary["players"][i], "place": place + 1, "advances": seated[i] in advancing}
            for place, i in enumerate(order)
        ]
        record = {"table": table_id, "round": round_no, "final": final, "standings": standings}
        self.results.append(record)
        await self._emit({"type": "table_end", **record, "advancing": [p.name for p in advancing]})
        return advancing


HOUSE_NAMES = ["Ace", "Bluff", "Chip", "Deuce", "River", "Turn", "Flop", "Nuts", "Kicker"]
