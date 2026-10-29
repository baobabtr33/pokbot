"""Level 1 · fish. Calls almost anything and rarely raises; folds only to huge bets with junk.

Beat it by betting your good hands for value. Never bluff it: it calls.
"""

import random

from pokbot_core.sdk import Bot


class Fish(Bot):
    def __init__(self):
        self.rng = random.Random()

    def act(self, state):
        legal = state["legal"]
        if "R" in legal and self.rng.random() < 0.05:
            return f"R {state['min_raise']}"
        if "X" in legal:
            return "X"
        hole_ranks = {c[0] for c in state["hole"]}
        junk = not hole_ranks & set("AKQJT") and state["hole"][0][0] != state["hole"][1][0]
        if junk and state["to_call"] > state["pot"]:
            return "F"
        return "C"
