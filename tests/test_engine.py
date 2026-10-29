import random

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pokbot_core.engine import Action, HoldemHand, IllegalAction, evaluate, parse_card


def cards(*names):
    return [parse_card(n) for n in names]


# -- evaluator -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "better, worse",
    [
        (("As", "Ks", "Qs", "Js", "Ts"), ("9h", "9d", "9c", "9s", "2h")),  # royal > quads
        (("9h", "9d", "9c", "9s", "2h"), ("Kh", "Kd", "Kc", "2s", "2h")),  # quads > boat
        (("Kh", "Kd", "Kc", "2s", "2h"), ("Ah", "Jh", "8h", "4h", "2h")),  # boat > flush
        (("Ah", "Jh", "8h", "4h", "2h"), ("6c", "5d", "4h", "3s", "2c")),  # flush > straight
        (("6c", "5d", "4h", "3s", "2c"), ("5c", "4d", "3h", "2s", "Ac")),  # 6-high > wheel
        (("5c", "4d", "3h", "2s", "Ac"), ("Ac", "Ad", "Ah", "Ks", "Qc")),  # wheel > trips
        (("Ac", "Ad", "Kh", "Ks", "2c"), ("Ac", "Ad", "Qh", "Qs", "Kc")),  # two pair kicker order
        (("Ac", "Ad", "Kh", "Js", "9c"), ("Ac", "Ad", "Kh", "Js", "8c")),  # pair, last kicker
        (("Ac", "Qd", "Th", "8s", "6c"), ("Kc", "Qd", "Th", "8s", "6c")),  # high card
    ],
)
def test_evaluator_ordering(better, worse):
    assert evaluate(cards(*better)) > evaluate(cards(*worse))


def test_seven_card_picks_best_five():
    # Board plays a straight; hole pair is irrelevant.
    assert evaluate(cards("2c", "2d", "5h", "6s", "7c", "8d", "9h"))[0] == 4
    # Two trips make a full house with the higher trips on top.
    assert evaluate(cards("Kc", "Kd", "Kh", "3s", "3c", "3d", "2h")) == (6, 11, 1)
    # Flush and straight flush detection across 7 cards.
    assert evaluate(cards("2h", "3h", "4h", "5h", "Ah", "Kd", "Qc")) == (8, 3)


# -- betting rules ---------------------------------------------------------------------------


def test_heads_up_button_posts_small_blind_and_acts_first():
    hand = HoldemHand([200, 200], button=0, blinds=(1, 2), seed=1)
    assert hand.seats[0].street_bet == 1 and hand.seats[1].street_bet == 2
    assert hand.to_act == 0
    hand.apply(0, Action("C"))
    assert hand.to_act == 1  # big blind gets the option
    assert "R" in hand.request()["legal"]
    hand.apply(1, Action("X"))
    assert hand.street == "flop"
    assert hand.to_act == 1  # postflop the non-button acts first


def test_min_raise_and_reraise():
    hand = HoldemHand([200, 200, 200], button=0, blinds=(1, 2), seed=2)
    # 3-handed: seat 1 SB, seat 2 BB, seat 0 (button) first preflop.
    assert hand.to_act == 0
    assert hand.request()["min_raise"] == 4
    with pytest.raises(IllegalAction):
        hand.apply(0, Action("R", 3))
    hand.apply(0, Action("R", 10))  # raise of 8
    assert hand.request()["min_raise"] == 18


def test_short_all_in_does_not_reopen_betting():
    hand = HoldemHand([200, 200, 25], button=0, blinds=(1, 2), seed=3)
    hand.apply(0, Action("R", 20))  # full raise of 18
    hand.apply(1, Action("C"))
    hand.apply(2, Action("R", 25))  # all-in, only 5 more: incomplete raise
    assert hand.to_act == 0
    req = hand.request()
    assert req["legal"] == ["F", "C"]  # seat 0 already acted on the full raise
    hand.apply(0, Action("C"))
    assert hand.request()["legal"] == ["F", "C"]


def test_illegal_actions_are_coerced():
    hand = HoldemHand([200, 200], button=0, blinds=(1, 2), seed=4)
    assert hand.coerce(0, Action("X")).kind == "F"  # facing a bet, check -> fold
    hand.apply(0, Action("C"))
    assert hand.coerce(1, Action("F")).kind == "X"  # folding for free is illegal -> check
    assert hand.coerce(1, Action("C")).kind == "X"  # nothing to call -> check


def test_side_pots_and_showdown():
    # Rig a deck by searching seeds for a known outcome is brittle; instead check conservation
    # and that the short stack can only win what it covered.
    for seed in range(200):
        hand = HoldemHand([50, 200, 200], button=0, blinds=(1, 2), seed=seed)
        hand.apply(0, Action("R", 50))
        hand.apply(1, Action("R", 200))
        hand.apply(2, Action("C"))
        assert hand.is_over
        res = hand.result
        assert sum(res.deltas) == 0
        assert res.deltas[0] <= 100  # 50 from each opponent at most
        assert sum(p["amount"] for p in res.pots) == 450
        side_winners = res.pots[-1]["winners"]
        assert 0 not in side_winners or len(res.pots) == 1 and res.deltas[0] <= 100


# -- invariants ------------------------------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(
    seats=st.integers(2, 9),
    seed=st.integers(0, 2**32),
    stacks=st.lists(st.integers(1, 400), min_size=9, max_size=9),
)
def test_random_play_conserves_chips_and_terminates(seats, seed, stacks):
    rng = random.Random(seed)
    stacks = stacks[:seats]
    hand = HoldemHand(stacks, button=seed % seats, blinds=(1, 2), seed=seed)
    for _ in range(500):
        if hand.is_over:
            break
        req = hand.request()
        kind = rng.choice(req["legal"])
        amount = rng.randint(req["min_raise"], req["max_raise"]) if kind == "R" else 0
        hand.apply(req["seat"], Action(kind, amount))
    assert hand.is_over
    assert sum(hand.result.deltas) == 0
    assert all(s.stack >= 0 for s in hand.seats)
    assert [s.stack for s in hand.seats] == [a + d for a, d in zip(stacks, hand.result.deltas)]


def test_same_seed_same_actions_same_result():
    def play(seed):
        hand = HoldemHand([200] * 4, button=1, blinds=(1, 2), seed=seed)
        while not hand.is_over:
            req = hand.request()
            hand.apply(req["seat"], Action("C") if "C" in req["legal"] else Action("X"))
        return hand.result

    assert play(42) == play(42)
