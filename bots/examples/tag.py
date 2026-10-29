"""Tight-aggressive: plays strong starting hands, bets made hands, gives up without one."""

from pokbot_core.engine.cards import parse_card
from pokbot_core.engine.evaluator import evaluate
from pokbot_core.sdk import Bot

RANKS = "23456789TJQKA"


def preflop_score(hole: list[str]) -> int:
    """Rough Chen-style score: higher ranks, pairs, suited and connected cards score more."""
    hi, lo = sorted((RANKS.index(c[0]) for c in hole), reverse=True)
    score = hi + lo
    if hi == lo:
        score += 12 + hi
    if hole[0][1] == hole[1][1]:
        score += 3
    if 0 < hi - lo <= 2:
        score += 2
    return score


class Tag(Bot):
    def act(self, state):
        legal, pot, to_call = state["legal"], state["pot"], state["to_call"]
        raise_to = min(state["min_raise"] + pot // 2, state["max_raise"])

        if not state["board"]:
            score = preflop_score(state["hole"])
            if score >= 28 and "R" in legal:  # big pairs, AK, AQs
                return f"R {raise_to}"
            if score >= 20:
                return "C" if "C" in legal else "X"
            return "X" if "X" in legal else "F"

        category = evaluate([parse_card(c) for c in state["hole"] + state["board"]])[0]
        if category >= 2 and "R" in legal:  # two pair or better: build the pot
            return f"R {raise_to}"
        if category == 1:  # one pair: keep going unless the bet is big
            if "X" in legal:
                return "X"
            return "C" if to_call <= pot // 2 else "F"
        return "X" if "X" in legal else "F"
