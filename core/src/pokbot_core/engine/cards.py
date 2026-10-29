"""Cards are ints 0..51: rank = card // 4 (0 = deuce .. 12 = ace), suit = card % 4.

On the wire cards are two-char strings: rank from RANKS + suit from SUITS, e.g. "Ah", "Td".
"""

from __future__ import annotations

import hashlib
import random

RANKS = "23456789TJQKA"
SUITS = "cdhs"

Card = int


def rank(card: Card) -> int:
    return card // 4


def suit(card: Card) -> int:
    return card % 4


def card_str(card: Card) -> str:
    return RANKS[card // 4] + SUITS[card % 4]


def parse_card(text: str) -> Card:
    if len(text) != 2 or text[0] not in RANKS or text[1] not in SUITS:
        raise ValueError(f"invalid card: {text!r}")
    return RANKS.index(text[0]) * 4 + SUITS.index(text[1])


def cards_str(cards: list[Card]) -> list[str]:
    return [card_str(c) for c in cards]


def hand_seed(master_seed: int, hand_index: int) -> int:
    """Deterministic per-hand seed so any hand can be replayed from (master_seed, index)."""
    digest = hashlib.sha256(f"{master_seed}:{hand_index}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


class Deck:
    def __init__(self, seed: int) -> None:
        self._cards = list(range(52))
        random.Random(seed).shuffle(self._cards)
        self._pos = 0

    def deal(self, n: int = 1) -> list[Card]:
        out = self._cards[self._pos : self._pos + n]
        if len(out) < n:
            raise RuntimeError("deck exhausted")
        self._pos += n
        return out

    @property
    def remaining(self) -> int:
        return 52 - self._pos
