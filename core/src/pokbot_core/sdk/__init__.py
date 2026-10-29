"""Bot SDK. Subclass `Bot`, implement `act`, and return one of:

    "F"  fold     "X"  check     "C"  call     "R 24"  raise to 24     "A"  all-in

    from pokbot_core.sdk import Bot

    class MyBot(Bot):
        def act(self, state):
            return "C" if "C" in state["legal"] else "X"
"""

from __future__ import annotations

import json
import sys


class Bot:
    def on_match_start(self, msg: dict) -> None:
        pass

    def on_hand_start(self, msg: dict) -> None:
        pass

    def on_hand_end(self, msg: dict) -> None:
        pass

    def on_match_end(self, msg: dict) -> None:
        pass

    def act(self, state: dict) -> str:
        raise NotImplementedError


def run(bot: Bot, stdin=None, stdout=None) -> None:
    """Protocol loop: read JSON lines, answer every action_request with one line."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    handlers = {
        "match_start": bot.on_match_start,
        "hand_start": bot.on_hand_start,
        "hand_end": bot.on_hand_end,
        "match_end": bot.on_match_end,
    }
    for line in stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        kind = msg.get("type")
        if kind == "action_request":
            reply = bot.act(msg)
            if isinstance(reply, dict):
                reply = json.dumps(reply)
            stdout.write(str(reply).strip() + "\n")
            stdout.flush()
        elif kind in handlers:
            handlers[kind](msg)
            if kind == "match_end":
                return
