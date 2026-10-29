"""Where the CLI keeps its server URL and API key."""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_DIR = Path(
    os.environ.get("POKBOT_CONFIG_DIR")
    or Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "pokbot"
)
CREDENTIALS = CONFIG_DIR / "credentials"
DEFAULT_SERVER = "http://localhost:8000"


def load() -> dict:
    try:
        return json.loads(CREDENTIALS.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(json.dumps(data, indent=2))
    CREDENTIALS.chmod(0o600)


def server_url(override: str | None = None) -> str:
    return (override or os.environ.get("POKBOT_SERVER") or load().get("server") or DEFAULT_SERVER).rstrip("/")


def ws_url(http_url: str, path: str) -> str:
    if http_url.startswith("https://"):
        return "wss://" + http_url[len("https://") :] + path
    return "ws://" + http_url.removeprefix("http://") + path


def token() -> str | None:
    return os.environ.get("POKBOT_TOKEN") or load().get("token")
