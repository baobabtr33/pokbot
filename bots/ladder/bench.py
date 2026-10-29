"""Rank the ladder: heads-up round robin (duplicate seating) plus one 6-max table.

    uv run python bots/ladder/bench.py [--hands 2000] [--seed 7]

Prints bb/100 for every pairing and each bot's average; higher levels should score higher.
"""

from __future__ import annotations

import argparse
import asyncio
from itertools import combinations
from pathlib import Path

from pokbot_core.match import Match, MatchConfig
from pokbot_core.players import HouseBot, SubprocessBot, bot_command

HERE = Path(__file__).parent
BOTS = sorted(HERE.glob("l[0-9]_*.py"))


def player(path: Path) -> SubprocessBot:
    return SubprocessBot(path.stem, bot_command(str(path)))


async def play(players, hands: int, seed: int) -> dict[str, float]:
    cfg = MatchConfig(hands=hands, seed=seed, time_ms=5, timebank_ms=1000)
    result = await Match(players, cfg).run()
    return {p["name"]: p["bb_per_100"] for p in result["players"]}


async def main(hands: int, seed: int) -> None:
    names = [b.stem for b in BOTS]
    totals: dict[str, list[float]] = {n: [] for n in names}
    print(f"heads-up, {hands} hands each (bb/100 for the row bot)")
    for a, b in combinations(BOTS, 2):
        res = await play([player(a), player(b)], hands, seed)
        totals[a.stem].append(res[a.stem])
        totals[b.stem].append(res[b.stem])
        print(f"  {a.stem:12} vs {b.stem:12} {res[a.stem]:+9.1f}")
    six = await play([player(b) for b in BOTS] + [HouseBot("tight", "tight", seed)], hands, seed)
    print(f"\n6-max with one [BOT] tight, {hands} hands")
    for name, bb in sorted(six.items(), key=lambda kv: -kv[1]):
        print(f"  {name:14} {bb:+9.1f}")
    print("\nheads-up average")
    for name in sorted(names, key=lambda n: -sum(totals[n]) / len(totals[n])):
        print(f"  {name:12} {sum(totals[name]) / len(totals[name]):+9.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hands", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(main(args.hands, args.seed))
