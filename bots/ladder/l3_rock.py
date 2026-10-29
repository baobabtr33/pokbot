"""Level 3 · rock. Plays only premium starting hands, then bets top pair or better hard.

Beat it by stealing its blinds and folding when it finally bets.
"""

from pokbot_core.engine.cards import parse_card
from pokbot_core.engine.evaluator import evaluate
from pokbot_core.sdk import Bot

RANKS = "23456789TJQKA"
PREMIUM_PAIRS = set("TJQKA")
PREMIUM_BROADWAY = {frozenset("AK"), frozenset("AQ")}


def premium(hole):
    a, b = hole[0][0], hole[1][0]
    return (a == b and a in PREMIUM_PAIRS) or frozenset((a, b)) in PREMIUM_BROADWAY


class Rock(Bot):
    def act(self, state):
        legal, pot = state["legal"], state["pot"]
        hole, board = state["hole"], state["board"]
        raise_to = max(state["min_raise"], min(state["min_raise"] + pot, state["max_raise"]))

        if not board:
            if premium(hole):
                return f"R {raise_to}" if "R" in legal else "C"
            return "X" if "X" in legal else "F"

        value = evaluate([parse_card(c) for c in hole + board])
        top_board = max(RANKS.index(c[0]) for c in board)
        strong = value[0] >= 2 or (value[0] == 1 and value[1] >= top_board)
        if strong:
            return f"R {raise_to}" if "R" in legal else "C"
        return "X" if "X" in legal else "F"
