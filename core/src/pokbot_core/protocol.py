"""Bot protocol v1: newline-delimited JSON from server to bot, one-line replies from bot.

Server -> bot messages: match_start, hand_start, action_request, hand_end, match_end.
Every message carries "v" and "type".

Bot -> server replies (to action_request only). Either a plain text line:

    F        fold
    X        check
    C        call
    R 24     raise to 24 (total for the street)
    A        all-in

or JSON: {"action": "R", "amount": 24}.
"""

from __future__ import annotations

import json

from .engine.holdem import Action

VERSION = 1
ACTION_LETTERS = {"F": "fold", "X": "check", "C": "call", "R": "raise", "A": "all-in"}


class ProtocolError(ValueError):
    pass


def message(type_: str, **fields) -> dict:
    return {"v": VERSION, "type": type_, **fields}


def encode(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


def parse_reply(raw: str | dict, request: dict) -> Action:
    """Turn a bot reply into an engine Action. Raises ProtocolError on garbage."""
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{"):
            try:
                raw = json.loads(text)
            except json.JSONDecodeError as e:
                raise ProtocolError(f"bad JSON: {e}") from None
        else:
            parts = text.split()
            if not parts:
                raise ProtocolError("empty reply")
            raw = {"action": parts[0], "amount": parts[1] if len(parts) > 1 else 0}

    if not isinstance(raw, dict):
        raise ProtocolError("reply must be a line of text or a JSON object")
    letter = str(raw.get("action", "")).strip().upper()[:1]
    if letter not in ACTION_LETTERS:
        raise ProtocolError(f"unknown action {raw.get('action')!r}; use one of F X C R A")
    try:
        amount = int(raw.get("amount") or 0)
    except (TypeError, ValueError):
        raise ProtocolError(f"amount must be an integer, got {raw.get('amount')!r}") from None

    if letter == "A":
        if "R" in request["legal"]:
            return Action("R", request["max_raise"])
        return Action("C") if "C" in request["legal"] else Action("X")
    if letter == "R":
        return Action("R", amount)
    return Action(letter)  # type: ignore[arg-type]


def format_action(action: Action) -> str:
    return f"R {action.amount}" if action.kind == "R" else action.kind
