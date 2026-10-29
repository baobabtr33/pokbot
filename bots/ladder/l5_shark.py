"""Level 5 · shark. Monte Carlo equity against the players left, pot odds, sized value bets,
semi-bluffs, and a bluff rate that adapts to how often the table folds to its bets.

Each decision simulates for about 1.2 ms (well inside the 5 ms limit).
"""

import random
import time

from pokbot_core.engine.cards import parse_card
from pokbot_core.engine.evaluator import evaluate
from pokbot_core.sdk import Bot

BUDGET_S = 0.0012
DECK = list(range(52))


def equity(hole, board, opponents, rng, budget_s=BUDGET_S):
    """Share of the pot we expect to win against `opponents` random hands (ties split)."""
    known = set(hole) | set(board)
    deck = [c for c in DECK if c not in known]
    need = 5 - len(board)
    draw = need + 2 * opponents
    won = trials = 0.0
    deadline = time.perf_counter() + budget_s
    while trials < 40 or (time.perf_counter() < deadline and trials < 2000):
        sample = rng.sample(deck, draw)
        full = board + sample[:need]
        ours = evaluate(hole + full)
        best, ties = True, 1
        for i in range(opponents):
            theirs = evaluate(sample[need + 2 * i : need + 2 * i + 2] + full)
            if theirs > ours:
                best = False
                break
            if theirs == ours:
                ties += 1
        if best:
            won += 1 / ties
        trials += 1
    return won / trials


class Shark(Bot):
    def __init__(self):
        self.rng = random.Random()
        self.preflop_cache = {}
        self.aggressed = False  # we bet or raised this hand
        self.bets = 1  # hands where we were aggressive (smoothed)
        self.folds = 0.5  # ...that everyone folded to (smoothed)

    def on_hand_start(self, msg):
        self.aggressed = False

    def on_hand_end(self, msg):
        if self.aggressed:
            self.bets += 1
            if not msg["showdown"] and msg["seat"] in msg["winners"]:
                self.folds += 1

    def bluff_rate(self):
        fold_rate = self.folds / self.bets
        return max(0.0, min(0.35, (fold_rate - 0.3) * 0.8))

    def preflop_equity(self, hole, opponents):
        r = sorted((c[0] for c in hole), key="23456789TJQKA".index, reverse=True)
        key = (r[0], r[1], hole[0][1] == hole[1][1], opponents)
        if key not in self.preflop_cache:
            cards = [parse_card(c) for c in hole]
            self.preflop_cache[key] = equity(cards, [], opponents, self.rng, budget_s=BUDGET_S)
        return self.preflop_cache[key]

    def act(self, state):
        legal, pot, to_call = state["legal"], state["pot"], state["to_call"]
        seat, board = state["seat"], state["board"]
        opponents = max(1, sum(1 for i, f in enumerate(state["folded"]) if not f and i != seat))

        if board:
            eq = equity([parse_card(c) for c in state["hole"]], [parse_card(c) for c in board], opponents, self.rng)
        else:
            eq = self.preflop_equity(state["hole"], opponents)

        fair = 1 / (opponents + 1)  # equity of an average hand
        odds = to_call / (pot + to_call) if to_call else 0.0

        def bet(frac):
            self.aggressed = True
            target = state["bets"][seat] + to_call + int(frac * (pot + to_call))
            return f"R {max(state['min_raise'], min(target, state['max_raise']))}"

        # Value: well ahead of the field. Bigger edge, bigger bet.
        if "R" in legal and eq > fair + 0.25:
            return bet(1.0 if eq > fair + 0.4 else 0.66)
        if "R" in legal and eq > fair + 0.12 and to_call == 0:
            return bet(0.5)

        # Semi-bluff or bluff when checked to, mostly heads-up, adapting to fold rate.
        if "R" in legal and to_call == 0 and opponents <= 2 and len(board) in (3, 4):
            if eq > 0.3 and self.rng.random() < 0.5:
                return bet(0.5)
            if self.rng.random() < self.bluff_rate():
                return bet(0.6)

        if "X" in legal:
            return "X"
        # Call when the price is right; a little extra margin preflop for implied odds.
        margin = -0.03 if not board else 0.02
        if eq >= odds + margin:
            return "C"
        return "F"
