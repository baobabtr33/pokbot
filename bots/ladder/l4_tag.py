"""Level 4 · tag (tight-aggressive). Position-aware starting hands, pot odds, draws, c-bets.

Beat it by finding spots where it gives up: it folds weak made hands to big pressure.
"""

from pokbot_core.engine.cards import parse_card
from pokbot_core.engine.evaluator import evaluate
from pokbot_core.sdk import Bot

RANKS = "23456789TJQKA"


def preflop_score(hole):
    """Chen-style score: high cards, pairs, suited and connected hands score more."""
    hi, lo = sorted((RANKS.index(c[0]) for c in hole), reverse=True)
    score = hi + lo
    if hi == lo:
        score += 12 + hi
    if hole[0][1] == hole[1][1]:
        score += 3
    if 0 < hi - lo <= 2:
        score += 2
    return score


def draw_outs(hole, board):
    """Outs for a flush draw (9) or open-ended straight draw (8); 0 if neither."""
    cards = hole + board
    suits = [c[1] for c in cards]
    if any(suits.count(s) == 4 for s in "cdhs"):
        return 9
    ranks = {RANKS.index(c[0]) for c in cards}
    if 12 in ranks:
        ranks.add(-1)
    for low in range(-1, 9):
        if all(r in ranks for r in range(low, low + 4)) and low - 1 not in ranks and low + 4 <= 12:
            return 8
    return 0


def players_after(state):
    """How many opponents still act after us this street (0 = we're last: best position)."""
    seat, button, folded = state["seat"], state["button"], state["folded"]
    n = len(folded)
    after, s = 0, seat
    while s != button:
        s = (s + 1) % n
        if not folded[s] and s != seat:
            after += 1
    return after


class Tag(Bot):
    def __init__(self):
        self.raised_preflop = False

    def on_hand_start(self, msg):
        self.raised_preflop = False

    def act(self, state):
        legal, pot, to_call = state["legal"], state["pot"], state["to_call"]
        hole, board = state["hole"], state["board"]
        opponents = sum(1 for i, f in enumerate(state["folded"]) if not f and i != state["seat"])

        def bet(frac):
            target = state["bets"][state["seat"]] + to_call + int(frac * (pot + to_call))
            return f"R {max(state['min_raise'], min(target, state['max_raise']))}"

        if not board:
            score = preflop_score(hole)
            late = players_after(state) <= 1
            open_bar = 24 if late else 27
            if score >= 32 and "R" in legal:
                self.raised_preflop = True
                return bet(1.0)
            if score >= open_bar and "R" in legal and to_call <= 2:
                self.raised_preflop = True
                return bet(0.75)
            if score >= open_bar - 4 and to_call <= 6:
                return "C" if "C" in legal else "X"
            return "X" if "X" in legal else "F"

        value = evaluate([parse_card(c) for c in hole + board])
        category = value[0]
        top_board = max(RANKS.index(c[0]) for c in board)
        top_pair = category == 1 and value[1] >= top_board
        outs = draw_outs(hole, board) if len(board) < 5 else 0
        odds = to_call / (pot + to_call) if to_call else 0.0
        draw_equity = outs * (4 if len(board) == 3 else 2) / 100

        if category >= 2 or (top_pair and opponents <= 2):
            return bet(0.66) if "R" in legal else "C"
        if top_pair or category == 1:
            if "X" in legal:
                return "X"
            return "C" if odds <= 0.3 else "F"
        if outs and "X" in legal and "R" in legal and opponents <= 2:
            return bet(0.5)  # semi-bluff
        if outs and to_call and draw_equity >= odds:
            return "C"
        if "X" in legal:
            if self.raised_preflop and opponents == 1 and len(board) == 3 and "R" in legal:
                return bet(0.5)  # continuation bet
            return "X"
        return "F"
