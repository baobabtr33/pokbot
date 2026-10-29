"""Open tables: humans can sit down any time, outside the bot-only tournaments.

Each table has 6 seats. Seats nobody is using hold house bots ([BOT] ...). When a real player
joins, a house bot is evicted between hands and the player takes its seat; when a player
leaves, a house bot takes the seat back. A table with no real players left shuts down.
Open tables never touch the leaderboard.
"""

from __future__ import annotations

import asyncio
import itertools
import logging

from pokbot_core.match import Match, MatchConfig
from pokbot_core.players import HouseBot, Player
from pokbot_core.tournament import HOUSE_NAMES

from .config import settings
from .hub import Conn, Hub, RemotePlayer

log = logging.getLogger("pokbot.tables")

SEATS = 6
STYLES = ("tight", "caller", "tight", "random", "tight", "caller")
_ids = itertools.count(1)


def house_bot(seat: int) -> HouseBot:
    return HouseBot(HOUSE_NAMES[seat % len(HOUSE_NAMES)], STYLES[seat % len(STYLES)])


class OpenTable:
    def __init__(self, tables: OpenTables) -> None:
        self.tables = tables
        self.id = f"open{next(_ids)}"
        self.seats: list[Player] = [house_bot(i) for i in range(SEATS)]  # what will be seated next hand
        self.conns: dict[int, Conn] = {}  # seat -> real player
        self.match: Match | None = None  # created when the first player sits down
        self.task: asyncio.Task | None = None

    def free_seat(self) -> int | None:
        return next((i for i, p in enumerate(self.seats) if p.kind == "house"), None)

    def sit(self, conn: Conn, seat: int) -> None:
        player = RemotePlayer(conn)
        self.seats[seat] = player
        self.conns[seat] = conn
        if self.match is None:  # brand-new table: start dealing with this player seated
            cfg = MatchConfig(hands=10**9, time_ms=settings.time_ms, timebank_ms=settings.timebank_ms, duplicate=False)
            self.match = Match(list(self.seats), cfg, on_event=self.on_event, match_id=self.id)
            self.task = asyncio.create_task(self._run())
        else:
            self.match.swap(seat, player)  # evicts the house bot after the current hand
        self.broadcast_info(f"[bold]{conn.name}[/] sat down at seat {seat + 1}")

    def stand(self, conn: Conn) -> None:
        seat = next((s for s, c in self.conns.items() if c is conn), None)
        if seat is None:
            return
        del self.conns[seat]
        self.seats[seat] = house_bot(seat)
        if self.conns:
            self.match.swap(seat, self.seats[seat])
            self.broadcast_info(f"{conn.name} left; a house bot takes seat {seat + 1}")
        else:
            self.match.stop()

    async def _run(self) -> None:
        try:
            await self.match.run()
        except Exception:
            log.exception("open table %s crashed", self.id)
        finally:
            self.tables.closed(self)

    def broadcast_info(self, text: str) -> None:
        for c in self.conns.values():
            c.push_event({"type": "info", "message": text})

    async def on_event(self, ev: dict) -> None:
        for conn in list(self.conns.values()):
            out = ev
            if ev["type"] == "hand_start":  # each player sees only their own cards until showdown
                holes = [h if name == conn.name else [None] * len(h) for name, h in zip(ev["players"], ev["holes"])]
                out = {**ev, "holes": holes}
            conn.push_event({"type": "event", "event": {"type": "table", "table": self.id, "event": out}})


class OpenTables:
    def __init__(self, hub: Hub) -> None:
        self.hub = hub
        self.tables: list[OpenTable] = []
        self.where: dict[str, OpenTable] = {}  # account -> table

    def join(self, conn: Conn) -> OpenTable:
        user = conn.user or conn.name
        if user in self.where or user in self.hub.games:
            raise ValueError("you are already playing somewhere; close that first")
        if sum(len(t.conns) for t in self.tables) >= settings.max_human_games:
            raise ValueError("all tables are busy; try again in a minute")
        # Fill tables that already have people first, so players meet each other.
        candidates = [t for t in self.tables if t.free_seat() is not None]
        candidates.sort(key=lambda t: -len(t.conns))
        table = candidates[0] if candidates else self._new()
        table.sit(conn, table.free_seat())
        self.where[user] = table
        self.hub.games[user] = conn
        self.hub.broadcast_presence()
        return table

    def leave(self, conn: Conn) -> None:
        user = conn.user or conn.name
        table = self.where.pop(user, None)
        if table is not None:
            table.stand(conn)
        self.hub.games.pop(user, None)
        self.hub.broadcast_presence()

    def _new(self) -> OpenTable:
        table = OpenTable(self)
        self.tables.append(table)
        return table

    def closed(self, table: OpenTable) -> None:
        if table in self.tables:
            self.tables.remove(table)
