"""The four classic poker variants. Only Hold'em is playable today; the rest are registered
so the CLI/server can list them and reject them with a clear message."""

from __future__ import annotations

from dataclasses import dataclass

from .holdem import HoldemHand


@dataclass(frozen=True)
class Variant:
    key: str
    name: str
    description: str
    hand_cls: type | None  # None = not implemented yet
    max_seats: int

    @property
    def available(self) -> bool:
        return self.hand_cls is not None


VARIANTS: dict[str, Variant] = {
    v.key: v
    for v in [
        Variant("holdem", "Texas Hold'em", "2 hole cards, 5 community cards, no-limit.", HoldemHand, 9),
        Variant("omaha", "Omaha", "4 hole cards, must use exactly 2 + 3 board.", None, 9),
        Variant("stud", "Seven-Card Stud", "7 cards each, 3 down + 4 up, no board.", None, 7),
        Variant("draw", "Five-Card Draw", "5 hole cards, one draw round.", None, 6),
    ]
}


def get_variant(key: str) -> Variant:
    try:
        variant = VARIANTS[key]
    except KeyError:
        raise ValueError(f"unknown game {key!r}; choose from {', '.join(VARIANTS)}") from None
    if not variant.available:
        raise ValueError(f"{variant.name} is coming soon; only holdem is playable right now")
    return variant
