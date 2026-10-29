"""Server settings, all from POKBOT_* environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


@dataclass(frozen=True)
class Settings:
    interval_s: int = field(default_factory=lambda: _int("POKBOT_INTERVAL", 1800))  # :00 and :30
    table_size: int = field(default_factory=lambda: _int("POKBOT_TABLE_SIZE", 6))
    bot_hands: int = field(default_factory=lambda: _int("POKBOT_BOT_HANDS", 10_000))
    human_hands: int = field(default_factory=lambda: _int("POKBOT_HUMAN_HANDS", 30))
    # Bot decision cap: a few ms of compute per action. Live websocket bots also get a network
    # allowance, since round-trip latency is not compute.
    time_ms: int = field(default_factory=lambda: _int("POKBOT_TIME_MS", 5))
    live_network_ms: int = field(default_factory=lambda: _int("POKBOT_LIVE_NETWORK_MS", 150))
    timebank_ms: int = field(default_factory=lambda: _int("POKBOT_TIMEBANK_MS", 1000))
    # Wall-clock budget per tournament; tables stop early so the next slot starts on time.
    budget_s: int = field(default_factory=lambda: _int("POKBOT_TOURNAMENT_BUDGET_S", 1500))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("POKBOT_DATA", REPO_ROOT / "data")))
    web_dir: Path = field(default_factory=lambda: Path(os.environ.get("POKBOT_WEB_DIR", REPO_ROOT / "web")))
    # "docker" (default) runs uploaded bots in locked-down containers. "unsafe-local" runs them
    # as plain subprocesses and must only be used for development with your own code.
    sandbox: str = field(default_factory=lambda: os.environ.get("POKBOT_SANDBOX", "docker"))
    # GitHub OAuth app (device flow enabled) for `pokbot login`. No client secret is needed.
    github_client_id: str = field(default_factory=lambda: os.environ.get("POKBOT_GITHUB_CLIENT_ID", ""))
    # Name-only sign-up without GitHub. Tests and local development only.
    dev_login: bool = field(default_factory=lambda: os.environ.get("POKBOT_DEV_LOGIN") == "1")
    # Public URL baked into install.sh; defaults to the URL the request came in on.
    public_url: str = field(default_factory=lambda: os.environ.get("POKBOT_PUBLIC_URL", ""))
    core_src: Path = REPO_ROOT / "core" / "src"
    spectator_fps: int = 10


settings = Settings()
