"""Human-friendly card and action text. Wire format stays "Ah"; people see "A♥"."""

from __future__ import annotations

SUIT_SYMBOLS = {"c": "♣", "d": "♦", "h": "♥", "s": "♠"}
RED_SUITS = {"d", "h"}
ACTION_WORDS = {"F": "Fold", "X": "Check", "C": "Call", "R": "Raise", "SB": "Small blind", "BB": "Big blind"}


def pretty_card(card: str) -> str:
    rank = "10" if card[0] == "T" else card[0]
    return rank + SUIT_SYMBOLS[card[1]]


def pretty_cards(cards: list[str]) -> str:
    return " ".join(pretty_card(c) for c in cards)


def is_red(card: str) -> bool:
    return card[1] in RED_SUITS


def pretty_action(entry: dict) -> str:
    """History entry -> e.g. 'R 24', 'C 4', 'F'."""
    act = entry["action"]
    amount = entry.get("amount", 0)
    return f"{act} {amount}" if amount else act
