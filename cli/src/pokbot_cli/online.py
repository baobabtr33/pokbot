"""Talking to a pokbot server: register, upload, and play live over a websocket."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable

import websockets
from pokbot_core.bundle import pack
from pokbot_core.players import SubprocessBot

from . import config

PROTOCOL_TYPES = {"match_start", "hand_start", "action_request", "hand_end", "match_end"}


class ServerError(RuntimeError):
    pass


def _request(method: str, url: str, body: bytes | None = None, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except json.JSONDecodeError:
            pass
        raise ServerError(f"{e.code}: {detail}") from None
    except urllib.error.URLError as e:
        raise ServerError(f"cannot reach {url}: {e.reason}") from None


def device_start(server: str) -> dict:
    return _request("POST", f"{server}/api/auth/device", b"{}", {"Content-Type": "application/json"})


def device_poll(server: str, session: str) -> dict:
    body = json.dumps({"session": session}).encode()
    return _request("POST", f"{server}/api/auth/poll", body, {"Content-Type": "application/json"})


def register(server: str, name: str) -> dict:
    body = json.dumps({"name": name}).encode()
    return _request("POST", f"{server}/api/register", body, {"Content-Type": "application/json"})


def upload(server: str, token: str, target: str, name: str | None) -> dict:
    data, manifest = pack(target)
    query = urllib.parse.urlencode({"name": name or manifest["name"]})
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/zip"}
    return _request("POST", f"{server}/api/bots?{query}", data, headers)


def schedule(server: str) -> dict:
    return _request("GET", f"{server}/api/schedule")


def leaderboard(server: str) -> dict:
    return _request("GET", f"{server}/api/leaderboard")


SOCIAL_TYPES = {"lobby", "info", "chat", "chat_history", "chat_error", "presence"}
ChatSender = Callable[[str], Awaitable[None]]


def _chat_sender(ws) -> ChatSender:
    async def send(text: str) -> None:
        await ws.send(json.dumps({"type": "chat", "text": text}))

    return send


async def play_live(
    server: str,
    token: str | None,
    name: str,
    on_event: Callable[[dict], Awaitable[None]],
    bot: SubprocessBot | None = None,
    ask: Callable[[dict], Awaitable[str]] | None = None,
    game: str = "holdem",
    on_ready: Callable[[ChatSender], None] | None = None,
    vs: str | None = None,
    bot_name: str | None = None,
) -> dict | None:
    """Join the next tournament as a live bot (`bot`), or with `vs` start a heads-up game now
    against that player's uploaded bot: as a human (`ask`) or with your uploaded bot `bot_name`
    (the server runs both bots). Returns the tournament_end / versus_end event.
    `on_ready` receives a function for sending chat messages once connected."""
    url = config.ws_url(server, "/ws/play")
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    kind = "human" if ask else "bot"
    result = None
    pending: set[asyncio.Task] = set()

    async with websockets.connect(url, max_size=2**22) as ws:
        hello = {"type": "hello", "name": name, "kind": kind, "game": game}
        if vs:
            hello.update(vs=vs, bot=bot_name)
        await ws.send(json.dumps(hello))
        if on_ready:
            on_ready(_chat_sender(ws))

        async def answer_human(msg: dict) -> None:
            reply = await ask(msg)
            await ws.send(json.dumps({"type": "action", "reply": reply, "req": msg.get("req")}))

        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype == "error":
                raise ServerError(msg.get("message", "server error"))
            if mtype in SOCIAL_TYPES:
                await on_event(msg)
            elif mtype == "event":
                await on_event(msg["event"])
                if msg["event"]["type"] in ("tournament_end", "versus_end"):
                    result = msg["event"]
                    break
            elif mtype in PROTOCOL_TYPES:
                if bot is not None:
                    if mtype == "match_start" and not bot.alive:
                        await bot.start()
                    if mtype == "action_request":
                        try:
                            reply = await bot.act(msg)
                        except ConnectionError as e:
                            await on_event({"type": "info", "message": f"[red]bot error:[/] {e}"})
                            reply = "F"
                        await ws.send(json.dumps({"type": "action", "reply": reply, "req": msg.get("req")}))
                    else:
                        await bot.notify(msg)
                elif ask is not None and mtype == "action_request":
                    # Answer in the background so chat keeps flowing while the human thinks.
                    task = asyncio.create_task(answer_human(msg))
                    pending.add(task)
                    task.add_done_callback(pending.discard)
    for task in pending:
        task.cancel()
    if bot is not None:
        await bot.close()
    return result


async def watch(
    server: str,
    token: str | None,
    on_event: Callable[[dict], Awaitable[None]],
    on_ready: Callable[[ChatSender], None] | None = None,
) -> None:
    """Spectate tournaments and chat until the connection closes."""
    url = config.ws_url(server, "/ws/watch")
    if token:
        url += "?" + urllib.parse.urlencode({"token": token})
    async with websockets.connect(url, max_size=2**22) as ws:
        if on_ready:
            on_ready(_chat_sender(ws))
        async for raw in ws:
            msg = json.loads(raw)
            mtype = msg.get("type")
            if mtype in SOCIAL_TYPES:
                await on_event(msg)
            elif mtype == "event":
                await on_event(msg["event"])
