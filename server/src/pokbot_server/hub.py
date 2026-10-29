"""The tournament hub: lobby of live connections, the every-30-minutes scheduler, and fan-out of
tournament events to players and spectators (hole cards hidden, bot tables down-sampled)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime

from fastapi import WebSocket
from pokbot_core.match import MatchConfig
from pokbot_core.players import Player
from pokbot_core.tournament import Tournament, TournamentConfig, next_start

from . import sandbox
from .chat import ChatError, ChatRoom, RateLimiter, clean
from .config import settings
from .store import Store

log = logging.getLogger("pokbot.hub")


class Conn:
    """One websocket. A single ordered outbox; display events are dropped if the client lags,
    protocol messages never are."""

    def __init__(self, ws: WebSocket, name: str, kind: str, user: str | None = None) -> None:
        self.ws = ws
        self.name = name
        self.kind = kind
        self.user = user  # signed-in account name; required to chat
        self.rate = RateLimiter()
        self.outbox: asyncio.Queue[dict] = asyncio.Queue(maxsize=2000)
        self.replies: asyncio.Queue[dict] = asyncio.Queue()
        self.closed = asyncio.Event()

    def push_event(self, msg: dict) -> None:
        if not self.closed.is_set():
            with contextlib.suppress(asyncio.QueueFull):
                self.outbox.put_nowait(msg)

    async def push(self, msg: dict) -> None:
        if not self.closed.is_set():
            await self.outbox.put(msg)

    async def writer(self) -> None:
        try:
            while True:
                msg = await self.outbox.get()
                await self.ws.send_text(json.dumps(msg, separators=(",", ":")))
        except Exception:
            self.closed.set()


class RemotePlayer(Player):
    """A bot or human playing through a live websocket."""

    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.name = conn.name
        self.kind = conn.kind
        self._req = 0
        if self.kind == "bot":
            self.time_ms = settings.time_ms + settings.live_network_ms

    async def notify(self, msg: dict) -> None:
        await self.conn.push(msg)

    async def act(self, msg: dict) -> str | dict:
        if self.conn.closed.is_set():
            raise ConnectionError("disconnected")
        self._req += 1
        await self.conn.push({**msg, "req": self._req})
        while True:
            get = asyncio.create_task(self.conn.replies.get())
            closed = asyncio.create_task(self.conn.closed.wait())
            try:
                done, _ = await asyncio.wait({get, closed}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                get.cancel()
                closed.cancel()
            if get not in done:
                raise ConnectionError("disconnected")
            reply = get.result()
            if reply.get("req") in (None, self._req):  # drop late answers to timed-out requests
                return reply.get("reply", reply)


class Hub:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.lobby: dict[str, Conn] = {}
        self.conns: set[Conn] = set()  # every open websocket: players and spectators
        self.chat = ChatRoom()
        self.spectators: set[Conn] = set()
        self.next_at: datetime = next_start(datetime.now(UTC), settings.interval_s)
        self.running: Tournament | None = None
        self.participants: dict[str, Conn] = {}
        self.games: dict[str, Conn] = {}  # on-demand games by account (see games.py)
        self._table_seats: dict[str, list[str]] = {}
        self._table_human: dict[str, bool] = {}
        self._table_last: dict[str, float] = {}
        self._table_sampled: dict[str, bool] = {}
        self._final_table: str | None = None
        self.last_result: dict | None = None

    # -- lobby -----------------------------------------------------------------------------

    # -- presence + chat ---------------------------------------------------------------------

    def connect(self, conn: Conn) -> None:
        self.conns.add(conn)
        conn.push_event({"type": "chat_history", "messages": list(self.chat.history)})
        self.broadcast_presence()

    def disconnect(self, conn: Conn) -> None:
        self.conns.discard(conn)
        self.broadcast_presence()

    def presence(self) -> dict:
        """Online = signed-in users counted once + each anonymous connection."""
        users = {c.user for c in self.conns if c.user}
        anonymous = sum(1 for c in self.conns if not c.user)
        playing = {c.name for c in self.lobby.values()} | set(self.participants) | set(self.games)
        return {
            "type": "presence",
            "online": len(users) + anonymous,
            "playing": len(playing),
            "spectators": len(self.spectators),
            "names": sorted(users)[:50],
        }

    def broadcast_presence(self) -> None:
        msg = self.presence()
        for c in list(self.conns):
            c.push_event(msg)

    def say(self, conn: Conn, text: str) -> None:
        if not conn.user:
            raise ChatError("sign in to chat: pokbot login")
        if not conn.rate.allow():
            raise ChatError("slow down: at most 5 messages per 10 seconds")
        self._broadcast_chat(self.chat.message(conn.user, clean(text)))

    def announce(self, text: str) -> None:
        self._broadcast_chat(self.chat.message("pokbot", text, system=True))

    def _broadcast_chat(self, msg: dict) -> None:
        for c in list(self.conns):
            c.push_event(msg)

    # -- lobby -----------------------------------------------------------------------------

    def join(self, conn: Conn) -> None:
        if conn.name in self.lobby or conn.name in self.participants:
            raise ValueError(f"{conn.name} is already connected")
        self.lobby[conn.name] = conn
        self.broadcast_lobby()
        self.broadcast_presence()

    def leave(self, conn: Conn) -> None:
        if self.lobby.get(conn.name) is conn:
            del self.lobby[conn.name]
            self.broadcast_lobby()
            self.broadcast_presence()
        self.spectators.discard(conn)

    def lobby_message(self) -> dict:
        return {
            "type": "lobby",
            "starts_at": self.next_at.isoformat(),
            "seconds": max(0, int((self.next_at - datetime.now(UTC)).total_seconds())),
            "entrants": len(self.lobby) + len(self.store.bots()),
            "running": self.running.id if self.running else None,
        }

    def broadcast_lobby(self) -> None:
        msg = self.lobby_message()
        for c in list(self.lobby.values()) + list(self.spectators):
            c.push_event(msg)

    # -- scheduler -------------------------------------------------------------------------

    async def run_forever(self) -> None:
        while True:
            self.next_at = next_start(datetime.now(UTC), settings.interval_s)
            self.broadcast_lobby()
            while (left := (self.next_at - datetime.now(UTC)).total_seconds()) > 0:
                await asyncio.sleep(min(left, 10))
                self.broadcast_lobby()
            try:
                await self.run_tournament()
            except Exception:
                log.exception("tournament crashed")

    def _entrants(self) -> list[Player]:
        players: list[Player] = [RemotePlayer(c) for c in self.lobby.values() if not c.closed.is_set()]
        taken = {p.name for p in players}
        for record in self.store.bots():
            if record["name"] in taken:
                continue
            try:
                players.append(sandbox.make_player(self.store.bot_dir(record["name"]), record))
            except sandbox.SandboxUnavailable as e:
                log.warning("skipping uploaded bot %s: %s", record["name"], e)
        return players

    async def run_tournament(self) -> dict | None:
        entrants = self._entrants()
        if not entrants:
            log.info("no entrants at %s; skipping", self.next_at)
            return None
        self.participants = {c.name: c for c in self.lobby.values()}
        self.lobby = {}
        self.broadcast_presence()
        cfg = TournamentConfig(
            table_size=settings.table_size,
            bot_hands=settings.bot_hands,
            human_hands=settings.human_hands,
            sets=settings.sets,
            match=MatchConfig(time_ms=settings.time_ms, timebank_ms=settings.timebank_ms),
            budget_s=settings.budget_s,
            max_parallel_tables=settings.max_parallel_tables,
        )
        self.running = Tournament(entrants, cfg, on_event=self.on_event)
        log.info("tournament %s starting with %d entrants", self.running.id, len(entrants))
        try:
            result = await self.running.run()
            self.store.record_tournament(result)
            self.last_result = result
            return result
        finally:
            self.running = None
            self.participants = {}
            self.broadcast_presence()
            self._table_seats.clear()
            self._final_table = None

    # -- event fan-out ---------------------------------------------------------------------

    def _audience(self) -> list[Conn]:
        return list(self.participants.values()) + list(self.spectators)

    async def on_event(self, event: dict) -> None:
        if event["type"] == "tournament_start":
            self.announce(f"tournament {event['tournament_id']} started with {len(event['entrants'])} entrants")
        elif event["type"] == "tournament_end" and event.get("winner"):
            self.announce(f"🏆 {event['winner']} won tournament {event['tournament_id']}!")
        if event["type"] == "round_start" and event["final"]:
            self._final_table = f"r{event['round']}t1"
        if event["type"] != "table":
            for c in self._audience():
                c.push_event({"type": "event", "event": event})
            return

        table, ev = event["table"], event["event"]
        kind = ev["type"]
        if kind == "match_start":
            self._table_seats[table] = [p["name"] for p in ev["players"]]
            self._table_human[table] = any(p["kind"] == "human" for p in ev["players"])
            self._table_last[table] = 0.0
        if kind == "hand_start":
            now = time.monotonic()
            sampled = self._table_human.get(table, False) or now - self._table_last.get(table, 0) >= 1 / settings.spectator_fps
            self._table_sampled[table] = sampled
            if sampled:
                self._table_last[table] = now
        if kind in ("hand_start", "action", "hand_end") and not self._table_sampled.get(table, True):
            return

        seated = set(self._table_seats.get(table, []))
        for c in self._audience():
            watching = c.name in seated or c in self.spectators or table == self._final_table
            if not watching:
                continue
            out = ev
            if kind == "hand_start":
                holes = [h if name == c.name else [None] * len(h) for name, h in zip(ev["players"], ev["holes"])]
                out = {**ev, "holes": holes}
            c.push_event({"type": "event", "event": {**event, "event": out}})
