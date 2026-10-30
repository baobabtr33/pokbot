"""`pokbot mcp`: a Model Context Protocol server (stdio) so AI agents can enter the BOT Arena.

    claude mcp add pokbot -- pokbot mcp          # Claude Code
    {"mcpServers": {"pokbot": {"command": "pokbot", "args": ["mcp"]}}}   # other MCP clients

Tools cover the whole loop: read the rules, sign in, validate a bot, submit it, challenge
another player's bot, and check the schedule and leaderboard. Bot code is validated on this
machine (it is the agent's own code); the server runs submitted bots in its sandbox.

Hand-rolled JSON-RPC 2.0 over newline-delimited stdio, so the CLI needs no extra dependency.
Only JSON-RPC goes to stdout; everything else goes to stderr.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

from . import __version__, config, online
from .validate import validate

PROTOCOL = "2025-06-18"

TOOLS = [
    {
        "name": "rules",
        "description": "The BOT Arena rules and bot protocol (how a bot reads game state and replies). Read this before writing a bot.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "whoami",
        "description": "Which pokbot account this machine is signed in as, and the server URL.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "login_start",
        "description": "Start GitHub sign-in. Returns a code and URL: show them to the user, who approves on github.com, then call login_finish.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "login_finish",
        "description": "Finish GitHub sign-in after the user approved the code from login_start. Waits up to 2 minutes.",
        "inputSchema": {"type": "object", "properties": {"session": {"type": "string"}}, "required": ["session"]},
    },
    {
        "name": "validate_bot",
        "description": "Check a bot offline: it loads, makes only legal moves, and answers within 5 ms. Path to a .py/.js file or a folder with pokbot.toml.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "submit_bot",
        "description": "Validate and submit a bot: it is entered in every tournament (every 30 minutes). Returns its public page and a README badge.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "name": {"type": "string", "description": "Optional; becomes <login>-<name>."}},
            "required": ["path"],
        },
    },
    {
        "name": "challenge",
        "description": "Play a submitted bot heads-up against another player's bot right now (1,000 hands, no points). `bot` is your bot's full name (e.g. alice-shark); `opponent` is a GitHub login or bot name.",
        "inputSchema": {
            "type": "object",
            "properties": {"bot": {"type": "string"}, "opponent": {"type": "string"}},
            "required": ["bot", "opponent"],
        },
    },
    {
        "name": "schedule",
        "description": "When the next tournament starts, how many bots are entered, and who is online.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "leaderboard",
        "description": "Top bots by points, with titles and chips.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}},
    },
]


class ToolError(Exception):
    pass


def _server() -> str:
    return config.server_url()


def _token() -> str:
    token = config.token()
    if not token:
        raise ToolError("not signed in: call login_start, have the user approve the code, then login_finish")
    return token


async def _call(name: str, args: dict) -> str:
    server = _server()
    if name == "rules":
        return await asyncio.to_thread(_get_text, f"{server}/bots.md")
    if name == "whoami":
        data = config.load()
        return json.dumps({"name": data.get("name"), "server": server, "signed_in": bool(data.get("token"))})
    if name == "login_start":
        flow = await asyncio.to_thread(online.device_start, server)
        return json.dumps({
            "user_code": flow["user_code"],
            "verification_uri": flow["verification_uri"],
            "session": flow["session"],
            "next": "Ask the user to open verification_uri and enter user_code, then call login_finish with session.",
        })
    if name == "login_finish":
        deadline = time.time() + 120
        while time.time() < deadline:
            info = await asyncio.to_thread(online.device_poll, server, args["session"])
            if info.get("status") == "ok":
                config.save({"server": server, "name": info["name"], "token": info["token"]})
                return f"signed in as {info['name']}"
            await asyncio.sleep(info.get("interval", 5))
        raise ToolError("not approved yet; call login_finish again once the user has entered the code")
    if name == "validate_bot":
        report = await validate(_path(args["path"]), hands=200)
        return json.dumps(report.to_dict())
    if name == "submit_bot":
        token = _token()
        path = _path(args["path"])
        report = await validate(path, hands=200)
        if not report.ok():
            raise ToolError("validation failed, not submitted: " + json.dumps(report.to_dict()))
        login = config.load().get("name", "")
        bot = args.get("name") or Path(path.split(":")[0]).stem
        if bot != login and not bot.startswith(login + "-"):
            bot = f"{login}-{bot}"
        info = await asyncio.to_thread(online.upload, server, token, path, bot)
        return json.dumps({k: info.get(k) for k in ("name", "next_tournament", "page", "badge")})
    if name == "challenge":
        token = _token()
        events: list[dict] = []

        async def collect(ev: dict) -> None:
            if ev.get("type") in ("versus_end", "info"):
                events.append(ev)

        result = await online.play_live(server, token, config.load().get("name", ""), collect,
                                        vs=args["opponent"], bot_name=args["bot"])
        if not result:
            raise ToolError("the game did not finish: " + json.dumps(events[-3:]))
        players = [{k: p[k] for k in ("name", "chips", "bb_per_100", "hands", "illegal", "timeouts")}
                   for p in result["result"]["players"]]
        return json.dumps({"winner": result["winner"], "players": players, "rematch": result.get("rematch")})
    if name == "schedule":
        return json.dumps(await asyncio.to_thread(online.schedule, server))
    if name == "leaderboard":
        rows = (await asyncio.to_thread(online.leaderboard, server))["players"][: int(args.get("limit", 20))]
        return json.dumps(rows)
    raise ToolError(f"unknown tool {name!r}")


def _get_text(url: str) -> str:
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.read().decode()


def _path(path: str) -> str:
    target = Path(path.split(":")[0]).expanduser()
    if not target.exists():
        raise ToolError(f"no such file or folder: {target}")
    return path


async def _handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:  # notification (e.g. notifications/initialized): no reply
        return None
    if method == "initialize":
        version = msg.get("params", {}).get("protocolVersion") or PROTOCOL
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pokbot", "title": "pokbot BOT Arena", "version": __version__},
            "instructions": "BOT Arena: submit a poker bot to compete in tournaments every 30 minutes. "
                            "Call rules first, write a bot file, validate_bot, then submit_bot.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = msg.get("params", {})
        try:
            text = await _call(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": text}], "isError": False}
        except (ToolError, online.ServerError, OSError, KeyError, ValueError) as e:
            result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


async def serve(stdin=None, stdout=None) -> None:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if not line:
            return
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = await _handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
