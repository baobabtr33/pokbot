"""On-demand heads-up games outside the tournament schedule.

- bot vs bot: one of your uploaded bots against another player's uploaded bot, both sandboxed.
- human vs bot: you, over the websocket, against another player's uploaded bot.

Games never touch the leaderboard. Tournaments stay bot-only.
"""

from __future__ import annotations

import asyncio
import time

from pokbot_core.match import Match, MatchConfig

from . import sandbox
from .config import settings
from .hub import Conn, Hub, RemotePlayer
from .store import Store

TABLE = "vs"


class GameError(ValueError):
    pass


def resolve_bot(store: Store, ref: str) -> dict:
    """`alice-tag` names one bot; `alice` means alice's bot named `alice`, else her newest one."""
    bots = {b["name"]: b for b in store.bots()}
    if ref in bots:
        return bots[ref]
    owned = sorted((b for b in bots.values() if b["owner"] == ref), key=lambda b: b["uploaded"])
    if not owned:
        raise GameError(f"{ref} has no uploaded bot (they can add one with `pokbot submit mybot`)")
    return owned[-1]


class Games:
    def __init__(self, store: Store, hub: Hub) -> None:
        self.store = store
        self.hub = hub
        self.bot_slots = asyncio.Semaphore(settings.max_bot_games)
        self.active: set[str] = set()  # one game per account at a time

    def _check_free(self, user: str, human: bool) -> None:
        if user in self.active:
            raise GameError("you already have a game running")
        if human and len(self.active) >= settings.max_human_games:
            raise GameError("all tables are busy; try again in a minute")
        if not human and self.bot_slots.locked():
            raise GameError("the bot-vs-bot tables are busy; try again in a minute")

    async def play(self, conn: Conn, hello: dict) -> None:
        """Run one game for `conn` and stream it. Raises GameError for bad requests."""
        user = conn.user or ""
        human = hello.get("kind") == "human"
        theirs = resolve_bot(self.store, str(hello.get("vs", "")))
        if human:
            self._check_free(user, human=True)
            players = [RemotePlayer(conn), self._sandboxed(theirs)]
            cfg = MatchConfig(hands=settings.human_hands, time_ms=settings.time_ms, timebank_ms=settings.timebank_ms)
            await self._run(conn, players, cfg, deadline_s=settings.human_game_budget_s)
            return
        mine = self.store.bots_by_name().get(str(hello.get("bot", "")))
        if not mine or mine["owner"] != user:
            raise GameError("upload your bot first: `pokbot play mybot --vs <user>` does it for you")
        self._check_free(user, human=False)
        async with self.bot_slots:
            players = [self._sandboxed(mine), self._sandboxed(theirs)]
            cfg = MatchConfig(hands=settings.versus_hands, time_ms=settings.time_ms, timebank_ms=settings.timebank_ms)
            await self._run(conn, players, cfg, deadline_s=settings.versus_budget_s)

    def _sandboxed(self, record: dict):
        try:
            return sandbox.make_player(self.store.bot_dir(record["name"]), record)
        except sandbox.SandboxUnavailable as e:
            raise GameError(f"bots can't run on this server right now: {e}") from None

    async def _run(self, conn: Conn, players: list, cfg: MatchConfig, deadline_s: int) -> None:
        user = conn.user or ""
        mine = players[0].name
        names = [p.name for p in players]
        live = players[0].kind == "human"
        last = 0.0
        sampled = True

        async def on_event(ev: dict) -> None:
            nonlocal last, sampled
            kind = ev["type"]
            if kind == "hand_start":
                now = time.monotonic()
                sampled = live or now - last >= 1 / settings.spectator_fps
                if sampled:
                    last = now
                # Only your own seat's cards until showdown.
                ev = {**ev, "holes": [h if n == mine else [None] * len(h) for n, h in zip(ev["players"], ev["holes"])]}
            if kind in ("hand_start", "action", "hand_end") and not sampled:
                return
            conn.push_event({"type": "event", "event": {"type": "table", "table": TABLE, "event": ev}})

        self.active.add(user)
        self.hub.games[user] = conn
        self.hub.broadcast_presence()
        try:
            conn.push_event({"type": "event", "event": {"type": "versus_start", "players": names, "hands": cfg.hands}})
            match = Match(players, cfg, on_event=on_event, deadline=time.monotonic() + deadline_s)
            summary = await match.run()
            ranked = sorted(summary["players"], key=lambda p: (-p["chips"], -p["wins"]))
            winner = ranked[0]["name"] if ranked[0]["chips"] > 0 else None
            # A line anyone can paste to play the same opponent.
            rematch = f"pokbot play mybot --vs {names[1]}" if not live else f"pokbot play --vs {names[1]}"
            end = {"type": "versus_end", "winner": winner, "result": summary, "rematch": rematch}
            await conn.push({"type": "event", "event": end})
        finally:
            self.active.discard(user)
            self.hub.games.pop(user, None)
            self.hub.broadcast_presence()
