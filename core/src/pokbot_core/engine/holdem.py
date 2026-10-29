"""No-Limit Texas Hold'em hand state machine (2-9 seats).

Pure and deterministic: a hand is a function of (seats, button, deck seed, actions).
Drive it with:

    hand = HoldemHand(stacks, button, blinds, seed)
    while not hand.is_over:
        seat = hand.to_act
        hand.apply(seat, decide(hand.request()))
    hand.result
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .cards import Card, Deck, cards_str
from .evaluator import HandValue, evaluate, hand_name

Street = Literal["preflop", "flop", "turn", "river", "showdown"]
STREETS: list[Street] = ["preflop", "flop", "turn", "river"]
BOARD_CARDS = {"flop": 3, "turn": 1, "river": 1}


class IllegalAction(ValueError):
    pass


@dataclass
class Action:
    kind: Literal["F", "X", "C", "R"]  # Fold, check (X), Call, Raise
    amount: int = 0  # for R: total street commitment to raise *to*


@dataclass
class SeatState:
    stack: int
    hole: list[Card] = field(default_factory=list)
    street_bet: int = 0
    total_bet: int = 0
    folded: bool = False

    @property
    def all_in(self) -> bool:
        return self.stack == 0 and not self.folded


@dataclass
class HandResult:
    deltas: list[int]
    winners: list[int]
    pots: list[dict]  # [{"amount": int, "winners": [seat, ...]}]
    showdown: dict[int, dict]  # seat -> {"hole": [...], "hand": "Flush"}
    board: list[str]


class HoldemHand:
    variant = "holdem"
    hole_cards = 2

    def __init__(
        self,
        stacks: list[int],
        button: int,
        blinds: tuple[int, int],
        seed: int,
    ) -> None:
        if not 2 <= len(stacks) <= 9:
            raise ValueError("holdem needs 2-9 seats")
        self.n = len(stacks)
        self.button = button
        self.sb, self.bb = blinds
        self.starting = list(stacks)
        self.seats = [SeatState(stack=s) for s in stacks]
        self.deck = Deck(seed)
        self.board: list[Card] = []
        self.street: Street = "preflop"
        self.history: list[dict] = []
        self.result: HandResult | None = None

        for seat in self.seats:
            seat.hole = self.deck.deal(self.hole_cards)

        if self.n == 2:  # heads-up: button posts SB and acts first preflop
            sb_seat, bb_seat = button, self._next(button)
        else:
            sb_seat = self._next(button)
            bb_seat = self._next(sb_seat)
        self.sb_seat, self.bb_seat = sb_seat, bb_seat
        self._post(sb_seat, self.sb, "SB")
        self._post(bb_seat, self.bb, "BB")

        self.current_bet = self.bb
        self.min_raise = self.bb  # size of the last full raise
        self._start_round(first=self._next_active(bb_seat))

    # -- helpers ---------------------------------------------------------------------------

    def _next(self, seat: int) -> int:
        return (seat + 1) % self.n

    def _can_act(self, seat: int) -> bool:
        s = self.seats[seat]
        return not s.folded and s.stack > 0

    def _next_active(self, seat: int) -> int:
        """First seat after `seat` (exclusive) that can act; -1 if none."""
        for i in range(1, self.n + 1):
            cand = (seat + i) % self.n
            if self._can_act(cand):
                return cand
        return -1

    def _post(self, seat: int, amount: int, kind: str) -> None:
        s = self.seats[seat]
        paid = min(amount, s.stack)
        s.stack -= paid
        s.street_bet += paid
        s.total_bet += paid
        self.history.append({"street": "preflop", "seat": seat, "action": kind, "amount": paid})

    def _start_round(self, first: int) -> None:
        self.pending = {i for i in range(self.n) if self._can_act(i)}
        self.may_raise = set(self.pending)
        self.to_act = first if first in self.pending else self._next_active(first - 1)
        self._maybe_advance()

    @property
    def is_over(self) -> bool:
        return self.result is not None

    @property
    def pot(self) -> int:
        return sum(s.total_bet for s in self.seats)

    def live_seats(self) -> list[int]:
        return [i for i, s in enumerate(self.seats) if not s.folded]

    # -- decision interface ----------------------------------------------------------------

    def request(self) -> dict:
        """Everything a player needs to decide; serialised straight into `action_request`."""
        seat = self.to_act
        s = self.seats[seat]
        to_call = min(self.current_bet - s.street_bet, s.stack)
        legal = ["F", "C"] if to_call > 0 else ["X"]
        min_to = max_to = 0
        if seat in self.may_raise and s.stack > to_call and self._others_can_respond(seat):
            max_to = s.street_bet + s.stack
            min_to = min(self.current_bet + self.min_raise, max_to)
            legal.append("R")
        return {
            "seat": seat,
            "street": self.street,
            "hole": cards_str(s.hole),
            "board": cards_str(self.board),
            "pot": self.pot,
            "to_call": to_call,
            "min_raise": min_to,
            "max_raise": max_to,
            "legal": legal,
            "stacks": [x.stack for x in self.seats],
            "bets": [x.street_bet for x in self.seats],
            "folded": [x.folded for x in self.seats],
            "button": self.button,
            "history": list(self.history),
        }

    def _others_can_respond(self, seat: int) -> bool:
        return any(self._can_act(i) for i in range(self.n) if i != seat) or any(
            not x.folded and x.street_bet > self.seats[seat].street_bet + self.seats[seat].stack
            for x in self.seats
        )

    def coerce(self, seat: int, action: Action) -> Action:
        """Map an illegal action to the protocol fallback: check if free, else fold."""
        try:
            self._validate(seat, action)
            return action
        except IllegalAction:
            req = self.request()
            return Action("X") if "X" in req["legal"] else Action("F")

    def _validate(self, seat: int, action: Action) -> None:
        if self.is_over or seat != self.to_act:
            raise IllegalAction("not your turn")
        req = self.request()
        if action.kind not in req["legal"]:
            raise IllegalAction(f"{action.kind} not legal, legal: {req['legal']}")
        if action.kind == "R" and not req["min_raise"] <= action.amount <= req["max_raise"]:
            raise IllegalAction(
                f"raise to {action.amount} outside [{req['min_raise']}, {req['max_raise']}]"
            )

    def apply(self, seat: int, action: Action) -> None:
        self._validate(seat, action)
        s = self.seats[seat]
        record = {"street": self.street, "seat": seat, "action": action.kind, "amount": 0}

        if action.kind == "F":
            s.folded = True
        elif action.kind == "C":
            paid = min(self.current_bet - s.street_bet, s.stack)
            s.stack -= paid
            s.street_bet += paid
            s.total_bet += paid
            record["amount"] = paid
        elif action.kind == "R":
            paid = action.amount - s.street_bet
            s.stack -= paid
            s.street_bet = action.amount
            s.total_bet += paid
            raise_size = action.amount - self.current_bet
            self.current_bet = action.amount
            record["amount"] = action.amount
            if raise_size >= self.min_raise:  # full raise reopens betting for everyone
                self.min_raise = raise_size
                self.may_raise = {i for i in range(self.n) if self._can_act(i) and i != seat}
            # Everyone still able to act now faces a bet again.
            self.pending = {
                i
                for i in range(self.n)
                if i != seat and self._can_act(i) and self.seats[i].street_bet < self.current_bet
            }

        self.history.append(record)
        self.pending.discard(seat)
        self.may_raise.discard(seat)
        self.to_act = self._next_active(seat)
        self._maybe_advance()

    # -- flow ------------------------------------------------------------------------------

    def _maybe_advance(self) -> None:
        while not self.is_over:
            live = self.live_seats()
            if len(live) == 1:
                self._finish(showdown=False)
                return
            if self.pending:
                if self.to_act not in self.pending:
                    self.to_act = next(
                        (self.to_act + i) % self.n
                        for i in range(self.n)
                        if (self.to_act + i) % self.n in self.pending
                    )
                return
            # Betting round closed.
            if self.street == "river":
                self._finish(showdown=True)
                return
            self.street = STREETS[STREETS.index(self.street) + 1]
            self.board += self.deck.deal(BOARD_CARDS[self.street])
            for x in self.seats:
                x.street_bet = 0
            self.current_bet = 0
            self.min_raise = self.bb
            actors = [i for i in live if self._can_act(i)]
            if len(actors) <= 1:
                # Nobody left to bet against: run the board out.
                self.pending = set()
                continue
            self.pending = set(actors)
            self.may_raise = set(actors)
            self.to_act = self._next_active(self.button)

    def best(self, seat: int) -> HandValue:
        return evaluate(self.seats[seat].hole + self.board)

    def _finish(self, showdown: bool) -> None:
        self.street = "showdown"
        payout = [0] * self.n
        pots: list[dict] = []
        live = self.live_seats()
        values = {i: self.best(i) for i in live} if showdown else {}

        # Side pots: peel off contribution levels from smallest to largest.
        levels = sorted({s.total_bet for s in self.seats if s.total_bet > 0})
        prev = 0
        for level in levels:
            contributors = [i for i, s in enumerate(self.seats) if s.total_bet >= level]
            amount = (level - prev) * len(contributors)
            prev = level
            eligible = [i for i in contributors if not self.seats[i].folded]
            if not eligible:  # everyone at this level folded; give to remaining live seats
                eligible = live
            if showdown:
                top = max(values[i] for i in eligible)
                winners = [i for i in eligible if values[i] == top]
            else:
                winners = eligible
            share, odd = divmod(amount, len(winners))
            # Odd chips go to the first winner left of the button.
            order = sorted(winners, key=lambda i: (i - self.button - 1) % self.n)
            for k, w in enumerate(order):
                payout[w] += share + (1 if k < odd else 0)
            if pots and pots[-1]["winners"] == order:
                pots[-1]["amount"] += amount
            else:
                pots.append({"amount": amount, "winners": order})

        deltas = [payout[i] - self.seats[i].total_bet for i in range(self.n)]
        for i, s in enumerate(self.seats):
            s.stack += payout[i]
        shown = {
            i: {"hole": cards_str(self.seats[i].hole), "hand": hand_name(values[i])}
            for i in live
            if showdown
        }
        self.result = HandResult(
            deltas=deltas,
            winners=sorted({w for p in pots for w in p["winners"]}),
            pots=pots,
            showdown=shown,
            board=cards_str(self.board),
        )
