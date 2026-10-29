"""Tournament points. Every real entrant scores; house [BOT]s never do.

    entry                     +1
    each table advanced from  +10
    final table place         1st 100 · 2nd 60 · 3rd 40 · 4th 30 · 5th 20 · 6th 10 · rest 5

Places count real entrants only, so a [BOT] finishing above you does not cost you points.
"""

from __future__ import annotations

ENTRY = 1
ADVANCE = 10
FINAL_PLACES = [100, 60, 40, 30, 20, 10]
FINAL_REST = 5


def award_points(result: dict) -> dict[str, int]:
    """Points per entrant name from a `tournament_end` result (its `tables` standings)."""
    points: dict[str, int] = {}
    for table in result["tables"]:
        real = [s for s in table["standings"] if s["kind"] != "house"]  # already in place order
        for s in real:
            points.setdefault(s["name"], ENTRY)
        if table["final"]:
            for place, s in enumerate(real):
                points[s["name"]] += FINAL_PLACES[place] if place < len(FINAL_PLACES) else FINAL_REST
        else:
            for s in real:
                if s["advances"]:
                    points[s["name"]] += ADVANCE
    return points
