"""Running uploaded (untrusted) bots. Default: one locked-down Docker container per match."""

from __future__ import annotations

import shutil
from pathlib import Path

from pokbot_core.players import SubprocessBot, bot_command

from .config import settings

IMAGES = {"python": "python:3.12-slim", "node": "node:20-slim"}


class SandboxUnavailable(RuntimeError):
    pass


def available() -> bool:
    if settings.sandbox == "unsafe-local":
        return True
    return settings.sandbox == "docker" and shutil.which("docker") is not None


def docker_argv(bot_dir: Path, record: dict) -> list[str]:
    entry = f"/bot/{record['entrypoint']}"
    if record["runtime"] == "python":
        cmd = ["python", "-u", "-m", "pokbot_core.sdk.runner", entry]
    else:
        cmd = ["node", entry]
    return [
        "docker", "run", "--rm", "-i",
        "--network=none",
        "--read-only",
        "--tmpfs", "/tmp:rw,size=64m",
        "--memory=512m", "--memory-swap=512m",
        "--cpus=1",
        "--pids-limit=64",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
        "-v", f"{bot_dir.resolve()}:/bot:ro",
        "-v", f"{settings.core_src.resolve()}:/pokbot:ro",
        "-e", "PYTHONPATH=/pokbot",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-w", "/bot",
        IMAGES[record["runtime"]],
        *cmd,
    ]


def make_player(bot_dir: Path, record: dict) -> SubprocessBot:
    if settings.sandbox == "unsafe-local":
        return SubprocessBot(record["name"], bot_command(str(bot_dir)), cwd=str(bot_dir))
    if not available():
        raise SandboxUnavailable("docker is not installed; uploaded bots cannot run")
    return SubprocessBot(record["name"], docker_argv(bot_dir, record))
