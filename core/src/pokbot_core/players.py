"""Seat occupants. Every player answers `act(request_msg)` with a raw reply (text or dict) that
`protocol.parse_reply` understands, so house bots, subprocess bots, websocket bots and humans
all go through the same validation path."""

from __future__ import annotations

import asyncio
import os
import random
import shlex
import sys
from abc import ABC, abstractmethod
from pathlib import Path

from .engine.cards import parse_card
from .engine.evaluator import evaluate
from .protocol import encode

HOUSE_PREFIX = "[BOT] "


class Player(ABC):
    name: str
    kind: str = "bot"  # "bot" | "human" | "house"
    time_ms: int | None = None  # per-decision override (humans get longer)

    async def start(self) -> None:
        pass

    async def notify(self, msg: dict) -> None:
        pass

    @abstractmethod
    async def act(self, msg: dict) -> str | dict: ...

    async def close(self) -> None:
        pass


# -- house bots ------------------------------------------------------------------------------


def _preflop_strength(hole: list[str]) -> float:
    """0..1 rough Chen-style score for two hole cards."""
    a, b = parse_card(hole[0]), parse_card(hole[1])
    ra, rb = sorted((a // 4, b // 4), reverse=True)
    score = ra / 12 * 0.6 + rb / 12 * 0.25
    if ra == rb:
        score += 0.35
    if a % 4 == b % 4:
        score += 0.07
    if ra - rb == 1:
        score += 0.04
    return min(score, 1.0)


def _postflop_strength(hole: list[str], board: list[str]) -> float:
    category = evaluate([parse_card(c) for c in hole + board])[0]
    return min(category / 4, 1.0)


class HouseBot(Player):
    """Server-provided opponents that fill empty seats. Names always start with [BOT]."""

    kind = "house"
    STYLES = ("random", "caller", "tight")

    def __init__(self, name: str, style: str = "tight", seed: int | None = None) -> None:
        self.name = name if name.startswith(HOUSE_PREFIX) else HOUSE_PREFIX + name
        self.style = style
        self.rng = random.Random(seed)

    async def act(self, msg: dict) -> str:
        legal = msg["legal"]
        if self.style == "caller":
            return "C" if "C" in legal else "X"
        if self.style == "random":
            choice = self.rng.choice(legal)
            if choice == "R":
                return f"R {self.rng.randint(msg['min_raise'], msg['max_raise'])}"
            return choice

        # "tight": raise good hands, call medium ones, give up on the rest.
        if msg["street"] == "preflop":
            strength = _preflop_strength(msg["hole"])
        else:
            strength = _postflop_strength(msg["hole"], msg["board"])
        strength += self.rng.uniform(-0.05, 0.05)
        pot = max(msg["pot"], 1)
        if strength > 0.7 and "R" in legal:
            target = msg["min_raise"] + pot // 2
            return f"R {max(msg['min_raise'], min(target, msg['max_raise']))}"
        if strength > 0.45 or msg["to_call"] <= pot // 6:
            return "C" if "C" in legal else "X"
        return "X" if "X" in legal else "F"


# -- user code run as a local subprocess -----------------------------------------------------


def bot_command(target: str) -> list[str]:
    """Turn a FILE argument (`mybot.py`, `mybot.py:MyBot`, `./mybot`, `bot.js`) into argv."""
    path_part, _, cls = target.partition(":")
    path = Path(path_part)
    if not path.exists():
        raise FileNotFoundError(f"bot file not found: {path}")
    if path.is_dir():
        from .bundle import load_manifest

        manifest = load_manifest(path)
        return bot_command(str(path / manifest["entrypoint"]))
    if path.suffix == ".py":
        cmd = [sys.executable, "-u", "-m", "pokbot_core.sdk.runner", str(path)]
        return cmd + ([cls] if cls else [])
    if path.suffix in (".js", ".mjs"):
        return ["node", str(path)]
    if os.access(path, os.X_OK):
        return [str(path.resolve())]
    raise ValueError(f"don't know how to run {path}; use .py, .js or an executable")


class SubprocessBot(Player):
    """Talks the JSON-lines protocol to a child process over stdin/stdout."""

    MAX_LINE = 1 << 20

    def __init__(self, name: str, argv: list[str], cwd: str | None = None) -> None:
        self.name = name
        self.argv = argv
        self.cwd = cwd
        self.proc: asyncio.subprocess.Process | None = None
        self.stderr_tail: list[str] = []
        self._stderr_task: asyncio.Task | None = None
        self._stale = 0  # replies still owed for requests that timed out

    @classmethod
    def from_file(cls, target: str, name: str | None = None) -> SubprocessBot:
        return cls(name or Path(target.split(":")[0]).stem, bot_command(target))

    async def start(self) -> None:
        self.proc = await asyncio.create_subprocess_exec(
            *self.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            limit=self.MAX_LINE,
        )
        self._stale = 0
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc and self.proc.stderr
        async for line in self.proc.stderr:
            self.stderr_tail.append(line.decode(errors="replace").rstrip())
            del self.stderr_tail[:-50]

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def notify(self, msg: dict) -> None:
        if not self.alive:
            return
        assert self.proc and self.proc.stdin
        try:
            self.proc.stdin.write((encode(msg) + "\n").encode())
            await self.proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass

    async def act(self, msg: dict) -> str:
        await self.notify(msg)
        if not self.alive:
            raise ConnectionError(f"bot process exited ({self.proc and self.proc.returncode})")
        assert self.proc and self.proc.stdout
        try:
            # Replies come back in order, so first skip answers to requests that timed out.
            while self._stale:
                if not await self.proc.stdout.readline():
                    raise ConnectionError("bot closed stdout")
                self._stale -= 1
            line = await self.proc.stdout.readline()
        except asyncio.CancelledError:
            self._stale += 1
            raise
        if not line:
            raise ConnectionError("bot closed stdout")
        return line.decode(errors="replace")

    async def close(self) -> None:
        if self.alive:
            assert self.proc
            if self.proc.stdin:
                self.proc.stdin.close()
            try:
                await asyncio.wait_for(self.proc.wait(), 2)
            except TimeoutError:
                self.proc.kill()
                await self.proc.wait()
        if self._stderr_task:
            self._stderr_task.cancel()


def describe_argv(argv: list[str]) -> str:
    return shlex.join(argv)
