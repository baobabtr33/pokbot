"""Terminal animations: the BOT Arena splash, a showdown hand, and the "entered" flourish.

Every frame is a character grid, and web/anim.js draws the same scenes on the landing page;
keep the two in step when changing a scene. Animations only play on an interactive terminal
wide enough for them; POKBOT_NO_ANIM=1, NO_COLOR or TERM=dumb turn them off.
"""

from __future__ import annotations

import math
import os
import random
import shutil
import sys
import time
from collections.abc import Iterator

W = 60

STYLES = {
    "gold": "1;38;2;232;217;160",
    "shine": "1;38;2;255;250;225",
    "ink": "38;2;233;239;233",
    "muted": "38;2;159;191;168",
    "dim": "38;2;44;110;73",
    "red": "1;38;2;229;72;77",
    "card": "1;38;2;242;242;238",
    "back": "38;2;61;140;95",
    "win": "1;38;2;80;220;130",
}
SPARK_STYLES = ("gold", "red", "win", "shine")

LOGO_FONT = {
    "B": ["████ ", "█   █", "████ ", "█   █", "████ "],
    "O": [" ███ ", "█   █", "█   █", "█   █", " ███ "],
    "T": ["█████", "  █  ", "  █  ", "  █  ", "  █  "],
    "A": [" ███ ", "█   █", "█████", "█   █", "█   █"],
    "R": ["████ ", "█   █", "████ ", "█  █ ", "█   █"],
    "E": ["█████", "█    ", "████ ", "█    ", "█████"],
    "N": ["█   █", "██  █", "█ █ █", "█  ██", "█   █"],
    " ": ["   "] * 5,
}
SUITS = "♠♥♦♣"
TAGLINE = "submit your poker bot · it plays every 30 minutes"


class Grid:
    def __init__(self, h: int, w: int = W) -> None:
        self.w, self.h = w, h
        self.cells = [[(" ", "")] * w for _ in range(h)]

    def put(self, x: int, y: int, text: str, style: str = "") -> None:
        for i, ch in enumerate(text):
            if 0 <= x + i < self.w and 0 <= y < self.h:
                self.cells[y][x + i] = (ch, style)

    def center(self, y: int, text: str, style: str = "") -> None:
        self.put((self.w - len(text)) // 2, y, text, style)

    def ansi(self) -> list[str]:
        lines = []
        for row in self.cells:
            out, current = [], None
            for ch, style in row:
                if style != current:
                    out.append(f"\x1b[0;{STYLES[style]}m" if style else "\x1b[0m")
                    current = style
                out.append(ch)
            lines.append("".join(out).rstrip() + "\x1b[0m")
        return lines

    def text(self) -> str:
        return "\n".join("".join(ch for ch, _ in row).rstrip() for row in self.cells)


def suit_style(label: str) -> str:
    return "red" if label[-1] in "♥♦" else "card"


# --- splash -------------------------------------------------------------------------------


def logo_rows(word: str = "BOT ARENA") -> list[str]:
    return ["".join(LOGO_FONT[c][r] + " " for c in word).rstrip() for r in range(5)]


def splash(seed: int = 7) -> Iterator[Grid]:
    """Suits shuffle into the BOT ARENA logo, a shine sweeps it, the tagline types out."""
    rng = random.Random(seed)
    rows = logo_rows()
    left = (W - len(rows[0])) // 2
    cells = [(x, y) for y, row in enumerate(rows) for x, ch in enumerate(row) if ch != " "]
    reveal = {c: int(c[0] * 0.55) + rng.randrange(8) for c in cells}
    settle = max(reveal.values()) + 1
    total = settle + 14 + len(TAGLINE) // 2 + 14
    for f in range(total):
        g = Grid(9)
        shine = f - settle  # column of the light sweep
        for (x, y), at in reveal.items():
            if f < at - 6:
                continue
            if f < at:
                ch = rng.choice(SUITS)
                g.put(left + x, y + 1, ch, "red" if ch in "♥♦" else "muted")
            else:
                lit = 0 <= shine * 4 - x < 6
                g.put(left + x, y + 1, "█", "shine" if lit else "gold")
        typed = max(0, (f - settle - 10) * 2)
        if typed:
            line = TAGLINE[:typed]
            g.center(7, line + ("▌" if typed < len(TAGLINE) and f % 2 else " "), "ink")
        if f >= settle:
            suits = "  ".join(SUITS)
            g.center(8, suits, "dim")
            k = (f // 3) % 4
            g.put((W - len(suits)) // 2 + k * 3, 8, SUITS[k], "red" if SUITS[k] in "♥♦" else "gold")
        yield g


# --- showdown -----------------------------------------------------------------------------


class Table:
    """A heads-up hand the animation steps through: pairs (K♠ K♦) against l5-shark (A♥ Q♥)."""

    BOARD = ["K♣", "7♦", "2♠", "A♠", "9♥"]
    SEATS = {"pairs": 8, "l5-shark": 10}
    HOLES = {"pairs": ["K♠", "K♦"], "l5-shark": ["A♥", "Q♥"]}
    CARD_X = [13, 20, 27, 34, 41]
    HEADER = "pokbot play pairs --vs l5-shark · hand 642 of 1,000"

    def __init__(self, seed: int = 7) -> None:
        self.rng = random.Random(seed)
        self.board = ["slot"] * 5  # slot | back | flip | face
        self.shown = {"pairs": True, "l5-shark": False}
        self.flip = {"pairs": 0, "l5-shark": 0}
        self.stack = {"pairs": 200, "l5-shark": 200}
        self.acts = {"pairs": "", "l5-shark": ""}
        self.pot = 0
        self.pot_shown = 0.0
        self.ticker = ("muted", "heads-up · blinds 1/2 · 5 ms per decision")
        self.chips: list[list[float]] = []  # x, y, tx, ty, t
        self.sparks: list[list] = []
        self.winner = ""
        self.frame = 0

    # drawing
    def card(self, g: Grid, x: int, state: str, label: str, hot: bool) -> None:
        if state == "slot":
            for dy, row in enumerate(["╭┈┈┈┈╮", "┊    ┊", "╰┈┈┈┈╯"]):
                g.put(x, 2 + dy, row, "dim")
        elif state == "back":
            for dy, row in enumerate(["╭────╮", "│░░░░│", "╰────╯"]):
                g.put(x, 2 + dy, row, "back")
        elif state == "flip":
            for dy, row in enumerate([" ╭──╮ ", " │▒▒│ ", " ╰──╯ "]):
                g.put(x, 2 + dy, row, "back")
        else:
            border = "win" if hot else "muted"
            g.put(x, 2, "╭────╮", border)
            g.put(x, 3, "│", border)
            g.put(x + 1, 3, f"{label:<4}", "win" if hot else suit_style(label))
            g.put(x + 5, 3, "│", border)
            g.put(x, 4, "╰────╯", border)

    def draw(self) -> Grid:
        g = Grid(13)
        for x, y, age, style, ch in self.sparks:  # drawn first, so the table covers them
            g.put(x, y, ch, style)
        g.put(2, 0, self.HEADER, "muted")
        for i, x in enumerate(self.CARD_X):
            label = self.BOARD[i]
            self.card(g, x, self.board[i], label, bool(self.winner) and label[0] == "K")
        for name, y in self.SEATS.items():
            mine = self.winner == name
            g.put(2, y, ("▶ " if mine else "  ") + name, "gold" if mine else "ink")
            for k, label in enumerate(self.HOLES[name]):
                x = 15 + k * 5
                if self.flip[name] == 1:
                    g.put(x, y, "[▒▒]", "back")
                elif self.shown[name]:
                    hot = bool(self.winner) and mine
                    g.put(x, y, "[", "muted")
                    g.put(x + 1, y, label, "win" if hot else suit_style(label))
                    g.put(x + 3, y, "]", "muted")
                else:
                    g.put(x, y, "[░░]", "back")
            g.put(27, y, f"stack {self.stack[name]:>3}", "muted")
            g.put(40, y, self.acts[name], "gold" if mine else "ink")
        for x, y, *_ in self.chips:
            g.put(round(x), round(y), "●", "gold")
        g.center(6, f"pot {round(self.pot_shown)}", "gold")
        style, text = self.ticker
        g.put(2, 12, text, style)
        return g

    # motion
    def tick(self) -> None:
        self.frame += 1
        self.pot_shown += (self.pot - self.pot_shown) * 0.35
        if abs(self.pot - self.pot_shown) < 0.5:
            self.pot_shown = self.pot
        for chip in self.chips:
            x0, y0, tx, ty, t = chip
            t = min(1.0, t + 0.2)
            ease = 1 - (1 - max(0.0, t)) ** 2
            chip[:] = [x0 + (tx - x0) * ease, y0 + (ty - y0) * ease, tx, ty, t]
        self.chips = [c for c in self.chips if c[4] < 1]
        if self.winner:
            for _ in range(3):
                a = self.rng.random() * math.tau
                r = 4 + self.rng.random() * 14
                x, y = round(W / 2 + math.cos(a) * r * 1.8), round(6 + math.sin(a) * r * 0.35)
                if 1 <= y < 12:
                    self.sparks.append([x, y, 0, self.rng.choice(SPARK_STYLES), "*"])
            for s in self.sparks:
                s[2] += 1
                s[4] = "*+·."[min(s[2] // 2, 3)]
            self.sparks = [s for s in self.sparks if s[2] < 8]

    def hold(self, n: int) -> Iterator[Grid]:
        for _ in range(n):
            self.tick()
            yield self.draw()

    def bet(self, name: str, act: str, words: str, amount: int) -> Iterator[Grid]:
        self.acts[name] = act
        self.ticker = ("ink", f"{name} {words}")
        if amount:
            self.stack[name] -= amount
            self.pot += amount
            for k in range(min(3, 1 + amount // 20)):
                self.chips.append([44.0 + k * 2, float(self.SEATS[name]), W / 2 - 1 + k, 6.0, -0.2 * k])
        yield from self.hold(9)

    def deal(self, *idx: int) -> Iterator[Grid]:
        self.acts = {n: "" for n in self.acts}
        for i in idx:
            self.board[i] = "back"
        yield from self.hold(3)
        for f in range(len(idx) * 2 + 2):
            for k, i in enumerate(idx):
                if f == k * 2:
                    self.board[i] = "flip"
                elif f == k * 2 + 1:
                    self.board[i] = "face"
            yield from self.hold(1)
        yield from self.hold(6)


def showdown(seed: int = 7) -> Iterator[Grid]:
    """One heads-up hand: the ten-line pairs bot flops a set of kings against the Monte Carlo shark."""
    t = Table(seed)
    yield from t.hold(10)
    yield from t.bet("pairs", "R 6", "raises to 6", 6)
    yield from t.bet("l5-shark", "C", "calls", 6)
    yield from t.deal(0, 1, 2)
    yield from t.bet("l5-shark", "R 8", "bets 8", 8)
    yield from t.bet("pairs", "C", "calls", 8)
    yield from t.deal(3)
    yield from t.bet("l5-shark", "R 20", "bets 20 (68% equity, it thinks)", 20)
    yield from t.bet("pairs", "R 60", "raises to 60", 60)
    yield from t.bet("l5-shark", "C", "calls", 40)
    yield from t.deal(4)
    yield from t.bet("l5-shark", "X", "checks", 0)
    yield from t.bet("pairs", "X", "checks", 0)
    t.ticker = ("muted", "showdown")
    t.flip["l5-shark"] = 1
    yield from t.hold(3)
    t.flip["l5-shark"] = 0
    t.shown["l5-shark"] = True
    yield from t.hold(8)
    t.winner = "pairs"
    t.stack["pairs"] += t.pot
    t.acts = {"pairs": f"+{t.pot}", "l5-shark": "pair of aces"}
    t.ticker = ("gold", f"pairs wins {t.pot} · three kings beat the shark's aces")
    yield from t.hold(36)


# --- submit -------------------------------------------------------------------------------


def entered(bot: str, when: str, seed: int = 7) -> Iterator[Grid]:
    """A card with the bot's name slides onto the felt, chips rain, and it's stamped ENTERED."""
    rng = random.Random(seed)
    label = f" {bot} "
    width = max(len(label) + 2, 14)
    target = (W - width) // 2
    chips: list[list] = []
    for f in range(34):
        g = Grid(6)
        t = min(1.0, f / 10)
        x = round(-width + (target + width) * (1 - (1 - t) ** 3))
        g.put(x, 0, "╭" + "─" * (width - 2) + "╮", "gold")
        g.put(x, 1, "│" + label.center(width - 2) + "│", "gold")
        g.put(x, 2, "╰" + "─" * (width - 2) + "╯", "gold")
        if 10 <= f < 24:
            for _ in range(2):
                chips.append([rng.randrange(W), 0, rng.choice(SPARK_STYLES)])
        for c in chips:
            c[1] += 1
        chips = [c for c in chips if c[1] < 5]
        for cx, cy, style in chips:
            if not (cy < 3 and x <= cx < x + width):
                g.put(cx, cy, "●" if style == "gold" else "♦♥♠♣"[cx % 4], style)
        if f >= 18:
            stamp = "✓ ENTERED"
            g.center(4, stamp[: (f - 17) * 2], "win")
        if f >= 24:
            g.center(5, f"first tournament {when}", "muted")
        yield g


# --- player -------------------------------------------------------------------------------


def enabled(stream=None, width: int = W) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("POKBOT_NO_ANIM", "") not in ("", "0") or os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb" or not stream.isatty():
        return False
    return shutil.get_terminal_size().columns > width


def play(frames: Iterator[Grid], fps: float = 20, stream=None) -> None:
    """Draw frames in place; Ctrl-C skips to the end, leaving the last frame on screen."""
    stream = stream or sys.stdout
    height = 0
    stream.write("\x1b[?25l")
    try:
        for grid in frames:
            if height:
                stream.write(f"\x1b[{height}F")
            lines = grid.ansi()
            stream.write("".join(f"\x1b[2K{line}\n" for line in lines))
            stream.flush()
            height = len(lines)
            time.sleep(1 / fps)
    except KeyboardInterrupt:
        pass
    finally:
        stream.write("\x1b[0m\x1b[?25h")
        stream.flush()
