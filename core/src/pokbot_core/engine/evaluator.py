"""Best-5-of-N hand evaluator (N = 5..7).

`evaluate` returns a tuple that compares correctly with `<`/`>`: the first element is the
category (8 = straight flush .. 0 = high card), the rest are tiebreak ranks, high first.
"""

from __future__ import annotations

from itertools import combinations

from .cards import Card

HandValue = tuple[int, ...]

CATEGORY_NAMES = [
    "High Card",
    "Pair",
    "Two Pair",
    "Three of a Kind",
    "Straight",
    "Flush",
    "Full House",
    "Four of a Kind",
    "Straight Flush",
]


def _straight_high(ranks: set[int]) -> int:
    """Highest straight rank in `ranks`, or -1. The wheel (A-5) returns 3 (the five)."""
    mask = 0
    for r in ranks:
        mask |= 1 << (r + 1)
    if mask & (1 << 13):  # ace also plays low
        mask |= 1
    for top in range(13, 3, -1):
        window = 0b11111 << (top - 4)
        if mask & window == window:
            return top - 1
    return -1


def evaluate(cards: list[Card]) -> HandValue:
    if not 5 <= len(cards) <= 7:
        raise ValueError("evaluate needs 5 to 7 cards")

    counts = [0] * 13
    by_suit: list[list[int]] = [[], [], [], []]
    for c in cards:
        counts[c // 4] += 1
        by_suit[c % 4].append(c // 4)

    flush_ranks: list[int] | None = None
    for ranks in by_suit:
        if len(ranks) >= 5:
            flush_ranks = sorted(ranks, reverse=True)
            sf = _straight_high(set(ranks))
            if sf >= 0:
                return (8, sf)

    # (count, rank) groups, biggest groups first, then higher rank first.
    groups = sorted(((n, r) for r, n in enumerate(counts) if n), reverse=True)

    if groups[0][0] == 4:
        quad = groups[0][1]
        kicker = max(r for r, n in enumerate(counts) if n and r != quad)
        return (7, quad, kicker)

    trips = [r for n, r in groups if n == 3]
    pairs = [r for n, r in groups if n == 2]

    if trips and (len(trips) > 1 or pairs):
        top = trips[0]
        second = max(trips[1:] + pairs)
        return (6, top, second)

    if flush_ranks is not None:
        return (5, *flush_ranks[:5])

    straight = _straight_high({r for r, n in enumerate(counts) if n})
    if straight >= 0:
        return (4, straight)

    singles = sorted((r for r, n in enumerate(counts) if n == 1), reverse=True)

    if trips:
        return (3, trips[0], *singles[:2])

    if len(pairs) >= 2:
        hi, lo = pairs[0], pairs[1]
        kicker = max([r for r in singles] + pairs[2:])
        return (2, hi, lo, kicker)

    if pairs:
        return (1, pairs[0], *singles[:3])

    return (0, *singles[:5])


def evaluate_omaha(hole: list[Card], board: list[Card]) -> HandValue:
    """Omaha rule: exactly two hole cards and exactly three board cards."""
    return max(evaluate(list(h) + list(b)) for h in combinations(hole, 2) for b in combinations(board, 3))


def hand_name(value: HandValue) -> str:
    return CATEGORY_NAMES[value[0]]
