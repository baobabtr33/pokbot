"""Level 2 · maniac. Raises most hands and bets every street; folds only rags to big bets.

Beat it by calling down with decent hands and letting it bluff into you.
"""

import random

from pokbot_core.sdk import Bot

HIGH = set("AKQJT9")


class Maniac(Bot):
    def __init__(self):
        self.rng = random.Random()

    def act(self, state):
        legal, pot = state["legal"], state["pot"]
        hole = state["hole"]
        playable = hole[0][0] == hole[1][0] or bool({c[0] for c in hole} & HIGH) or hole[0][1] == hole[1][1]
        if "R" in legal and (playable or self.rng.random() < 0.4):
            size = state["min_raise"] + pot
            return f"R {max(state['min_raise'], min(size, state['max_raise']))}"
        if "X" in legal:
            return "X"
        if not playable and state["to_call"] > pot // 2:
            return "F"
        return "C"
