"""Global lobby chat: sanitising, per-connection rate limits, and a short history for newcomers."""

from __future__ import annotations

import re
import time
from collections import deque
from datetime import UTC, datetime

MAX_LEN = 280
HISTORY = 50
RATE_COUNT = 5  # messages
RATE_WINDOW_S = 10.0  # per this many seconds, per connection
CONTROL = re.compile(r"[\x00-\x1f\x7f​-‏‪-‮]")


class ChatError(ValueError):
    pass


def clean(text: str) -> str:
    text = CONTROL.sub("", str(text)).strip()
    if not text:
        raise ChatError("empty message")
    if len(text) > MAX_LEN:
        raise ChatError(f"messages are at most {MAX_LEN} characters")
    return text


class RateLimiter:
    def __init__(self, count: int = RATE_COUNT, window_s: float = RATE_WINDOW_S) -> None:
        self.count = count
        self.window_s = window_s
        self.sent: deque[float] = deque()

    def allow(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        while self.sent and now - self.sent[0] > self.window_s:
            self.sent.popleft()
        if len(self.sent) >= self.count:
            return False
        self.sent.append(now)
        return True


class ChatRoom:
    def __init__(self) -> None:
        self.history: deque[dict] = deque(maxlen=HISTORY)

    def message(self, name: str, text: str, system: bool = False) -> dict:
        msg = {
            "type": "chat",
            "name": name,
            "text": text,
            "system": system,
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        self.history.append(msg)
        return msg
