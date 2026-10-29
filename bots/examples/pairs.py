"""Raises pocket pairs and big cards, folds junk to bets."""

from pokbot_core.sdk import Bot

STRONG = set("AKQJT")


class Pairs(Bot):
    def act(self, state):
        legal = state["legal"]
        a, b = state["hole"][0][0], state["hole"][1][0]
        strong = a == b or (a in STRONG and b in STRONG)
        if strong and "R" in legal:
            return f"R {min(state['min_raise'] + state['pot'], state['max_raise'])}"
        if "X" in legal:
            return "X"
        return "C" if strong or state["to_call"] <= 2 else "F"
