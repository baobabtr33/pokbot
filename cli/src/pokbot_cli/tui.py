"""Live terminal view for pokbot: table, standings, chip chart, log, human action bar and the
champion celebration. Fed by tournament/match events; renders at most ~10 times a second."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from pokbot_core.display import ACTION_WORDS, is_red, pretty_card, pretty_cards
from pokbot_core.players import Player
from rich.align import Align
from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, ProgressBar, RichLog, Sparkline, Static

Source = Callable[["PokbotApp"], Awaitable[None]]

GAME_NAMES = {"holdem": "Texas Hold'em", "omaha": "Omaha", "stud": "Seven-Card Stud", "draw": "Five-Card Draw"}


def card_text(card: str | None) -> Text:
    if card is None:
        return Text(" ░░ ", style="grey50 on grey15")
    style = "bold red on grey93" if is_red(card) else "bold black on grey93"
    return Text(f" {pretty_card(card):>3}", style=style) + Text(" ", style=style)


def cards_text(cards: list[str | None]) -> Text:
    out = Text()
    for i, c in enumerate(cards):
        if i:
            out.append(" ")
        out.append_text(card_text(c))
    return out


class HumanPlayer(Player):
    """A person at the keyboard. `act` waits for the action bar in the TUI."""

    kind = "human"

    def __init__(self, name: str, app: PokbotApp) -> None:
        self.name = name
        self.app = app

    async def act(self, msg: dict) -> str:
        return await self.app.ask(msg)


class TableState:
    def __init__(self) -> None:
        self.table_id = ""
        self.players: list[str] = []
        self.kinds: dict[str, str] = {}
        self.holes: list[list[str]] = []
        self.board: list[str] = []
        self.stacks: list[int] = []
        self.bets: list[int] = []
        self.folded: list[bool] = []
        self.last: list[str] = []
        self.button = 0
        self.to_act: int | None = None
        self.pot = 0
        self.hand_no = 0
        self.hands_total = 0
        self.street = "preflop"
        self.showdown: dict[str, dict] = {}
        self.deltas: list[int] = []
        self.totals: dict[str, int] = {}
        self.stats: dict[str, dict] = {}


class WinnerScreen(ModalScreen[None]):
    """Fireworks + banner for the tournament champion."""

    BINDINGS = [Binding("escape,enter,space,q", "dismiss", "Close")]
    BANNER = r"""
 __        __ ___  _   _  _   _  _____  ____
 \ \      / /|_ _|| \ | || \ | || ____||  _ \
  \ \ /\ / /  | | |  \| ||  \| ||  _|  | |_) |
   \ V  V /   | | | |\  || |\  || |___ |  _ <
    \_/\_/   |___||_| \_||_| \_||_____||_| \_\
"""
    COLORS = ["red", "yellow", "green", "cyan", "magenta", "bright_white", "orange1", "deep_pink2"]
    SPARKS = "*+x.oO@#"

    def __init__(self, winner: str, detail: str) -> None:
        super().__init__()
        self.winner = winner
        self.detail = detail
        self.frame = 0
        self.bursts: list[tuple[int, int, int, str]] = []  # x, y, age, color

    def compose(self) -> ComposeResult:
        yield Static(id="fireworks")

    def on_mount(self) -> None:
        self.set_interval(1 / 15, self._tick)

    def _tick(self) -> None:
        w = max(self.size.width - 2, 40)
        h = max(self.size.height - 2, 20)
        self.frame += 1
        if self.frame % 2 == 0 or not self.bursts:
            for _ in range(random.randint(1, 3)):  # bang
                self.bursts.append((random.randrange(w), random.randrange(h), 0, random.choice(self.COLORS)))
        self.bursts = [(x, y, a + 1, c) for x, y, a, c in self.bursts if a < 12][-30:]

        grid: list[list[tuple[str, str]]] = [[(" ", "")] * w for _ in range(h)]
        for x, y, age, color in self.bursts:
            radius = age
            for k in range(12):
                dx = round(radius * 2 * [1, 0.87, 0.5, 0, -0.5, -0.87, -1, -0.87, -0.5, 0, 0.5, 0.87][k])
                dy = round(radius * [0, 0.5, 0.87, 1, 0.87, 0.5, 0, -0.5, -0.87, -1, -0.87, -0.5][k])
                px, py = x + dx, y + dy
                if 0 <= px < w and 0 <= py < h:
                    grid[py][px] = (self.SPARKS[min(age, len(self.SPARKS) - 1)], color)

        banner_lines = self.BANNER.strip("\n").splitlines() + [
            "",
            f"🏆  ★ {self.winner} ★  🏆",
            self.detail,
            "",
            "press enter to continue",
        ]
        top = max(0, h // 2 - len(banner_lines) // 2)
        pulse = self.COLORS[self.frame // 3 % len(self.COLORS)]
        text = Text()
        for row in range(h):
            idx = row - top
            if 0 <= idx < len(banner_lines):
                line = banner_lines[idx]
                pad = max(0, (w - len(line)) // 2)
                style = f"bold {pulse}" if idx < 6 else "bold bright_white"
                text.append(" " * pad)
                text.append(line, style=style)
                text.append("\n")
                continue
            for ch, color in grid[row]:
                text.append(ch, style=f"bold {color}" if color else "")
            text.append("\n")
        self.query_one("#fireworks", Static).update(text)


class PokbotApp(App[None]):
    TITLE = "pokbot"
    CSS = """
    Screen { background: #0b1d13; }
    #top { height: 1; background: #123524; color: #e8d9a0; padding: 0 1; }
    #main { height: 1fr; }
    #table { width: 3fr; padding: 0 1; }
    #side { width: 2fr; min-width: 36; }
    #stats { height: 1fr; }
    #chat { height: 12; border-top: solid #2c6e49; display: none; }
    #chat.active { display: block; }
    #chat-input { display: none; }
    #chat-input.active { display: block; }
    #chart-label { height: 1; color: #9fbfa8; padding: 0 1; }
    #chart { height: 4; margin: 0 1; }
    #progress { height: 1; margin: 0 1; }
    #log { height: 9; border-top: solid #2c6e49; }
    #actionbar { height: 3; display: none; background: #1d4d33; padding: 0 1; }
    #actionbar.active { display: block; }
    #prompt { width: 1fr; content-align: left middle; color: #ffffff; }
    #amount { width: 16; display: none; }
    #amount.active { display: block; }
    WinnerScreen { background: #000000 85%; }
    #fireworks { width: 100%; height: 100%; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("space", "pause", "Pause"),
        Binding("w", "next_table", "Switch table"),
        Binding("t", "chat", "Chat", show=False),
        Binding("escape", "leave_chat", "", show=False),
    ]

    def __init__(self, source: Source, hero: str | None = None, hero_is_human: bool = False,
                 game: str = "holdem", title: str = "pokbot") -> None:
        super().__init__()
        self.source = source
        self.hero = hero
        self.hero_is_human = hero_is_human
        self.game = game
        self.banner = title
        self.tables: dict[str, TableState] = {}
        self.focus_table: str | None = None
        self.status = "starting…"
        self.round_no = 0
        self.lobby_until: datetime | None = None
        self.hero_curve: list[float] = [0.0]
        self.paused = asyncio.Event()
        self.paused.set()
        self._pending: asyncio.Future[str] | None = None
        self._request: dict | None = None
        self._dirty = True
        self.result: dict | None = None
        self.online: int | None = None
        self.playing: int | None = None
        self._chat_send = None

    # -- chat --------------------------------------------------------------------------------

    def set_chat_sender(self, send) -> None:
        """Called once connected to a server; enables the chat panel."""
        self._chat_send = send
        self.query_one("#chat").add_class("active")
        self.query_one("#chat-input").add_class("active")
        self.chat_line("[dim]chat is open · press [bold]t[/] to talk[/]")

    def chat_line(self, markup: str) -> None:
        self.query_one("#chat", RichLog).write(markup)

    def _chat_message(self, m: dict) -> None:
        stamp = m.get("at", "")[11:16]
        if m.get("system"):
            self.chat_line(f"[dim]{stamp}[/] [bold yellow]{escape(m['text'])}[/]")
            return
        color = "bold #ffd75f" if m["name"] == self.hero else "bold cyan"
        self.chat_line(f"[dim]{stamp}[/] [{color}]{escape(m['name'])}[/]: {escape(m['text'])}")

    def action_chat(self) -> None:
        if self._chat_send:
            self.query_one("#chat-input", Input).focus()

    def action_leave_chat(self) -> None:
        self.set_focus(None)

    # -- layout ------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static(id="top")
        with Horizontal(id="main"):
            yield Static(id="table")
            with Vertical(id="side"):
                yield DataTable(id="stats", show_cursor=False, zebra_stripes=True)
                yield Static("Your chips", id="chart-label")
                yield Sparkline([0], id="chart")
                yield ProgressBar(id="progress", show_eta=True)
                yield RichLog(id="chat", markup=True, wrap=True, max_lines=300)
                yield Input(id="chat-input", placeholder="t to chat · enter to send · esc to leave")
        yield RichLog(id="log", markup=True, max_lines=500)
        with Horizontal(id="actionbar"):
            yield Static(id="prompt")
            yield Input(id="amount", placeholder="raise to", type="integer")
        yield Footer()

    def on_mount(self) -> None:
        stats = self.query_one("#stats", DataTable)
        stats.add_columns("Player", "Chips", "bb/100", "VPIP", "PFR")
        self.set_interval(0.1, self._render)
        self.run_worker(self._run_source(), exclusive=True)

    async def _run_source(self) -> None:
        try:
            await self.source(self)
        except Exception as e:  # surface errors in the log instead of crashing the TUI
            self.log_line(f"[bold red]error:[/] {e}")
            self.status = f"error: {e}"
            self._dirty = True

    # -- event intake ------------------------------------------------------------------------

    def log_line(self, markup: str) -> None:
        self.query_one("#log", RichLog).write(markup)

    async def handle(self, event: dict) -> None:
        """Entry point for every tournament/match event."""
        await self.paused.wait()
        kind = event["type"]
        self._dirty = True
        if kind == "chat":
            self._chat_message(event)
            return
        if kind == "chat_history":
            for m in event["messages"]:
                self._chat_message(m)
            return
        if kind == "chat_error":
            self.chat_line(f"[red]{escape(event['message'])}[/]")
            return
        if kind == "presence":
            self.online, self.playing = event["online"], event["playing"]
            return
        if kind == "lobby":
            self.lobby_until = datetime.fromisoformat(event["starts_at"])
            self.status = f"waiting for next tournament · {event.get('entrants', 0)} entered"
        elif kind == "info":
            self.log_line(event["message"])
        elif kind == "tournament_start":
            self.lobby_until = None
            self.status = "playing"
            names = ", ".join(e["name"] for e in event["entrants"])
            self.log_line(f"[bold #e8d9a0]Tournament {event['tournament_id']}[/] · {len(event['entrants'])} entrants: {names}")
        elif kind == "round_start":
            self.round_no = event["round"]
            label = "FINAL TABLE" if event["final"] else f"Round {event['round']}"
            self.log_line(f"[bold cyan]{label}[/] · {len(event['tables'])} table(s)")
            self.tables.clear()
            self.focus_table = None
        elif kind == "table":
            await self._table_event(event["table"], event["event"])
        elif kind == "table_end":
            adv = ", ".join(event["advancing"]) or "nobody"
            self.log_line(f"[green]Table {event['table']} done[/] · advancing: [bold]{adv}[/]")
        elif kind == "round_end":
            self.log_line(f"Round {event['round']} complete · {len(event['advancing'])} still in")
        elif kind == "tournament_end":
            self.result = event
            winner = event["winner"] or "nobody"
            self.status = f"champion: {winner}"
            self.log_line(f"[bold yellow]🏆 Champion: {winner}[/] after {event['rounds']} round(s)")
            ranked = sorted(event.get("points", {}).items(), key=lambda kv: -kv[1])
            if ranked:
                self.log_line("Points: " + " · ".join(f"[bold]{escape(n)}[/] +{p}" for n, p in ranked))
            await self.push_screen(WinnerScreen(winner, f"won tournament {event['tournament_id']} · {event['rounds']} round(s) · +{event.get('points', {}).get(winner, 0)} points"))
        elif kind == "match_end_local":
            self.status = "match finished"

    async def _table_event(self, table_id: str, ev: dict) -> None:
        t = self.tables.setdefault(table_id, TableState())
        t.table_id = table_id
        kind = ev["type"]
        if self.focus_table is None or (self.hero and self.hero in t.players and self.focus_table != table_id
                                        and self.hero not in self.tables[self.focus_table].players):
            self.focus_table = table_id
        focused = table_id == self.focus_table
        human_here = any(k == "human" for k in t.kinds.values())

        if kind == "match_start":
            t.players = [p["name"] for p in ev["players"]]
            t.kinds = {p["name"]: p["kind"] for p in ev["players"]}
            t.hands_total = ev["hands"]
            t.totals = {n: 0 for n in t.players}
            if self.hero in t.players:
                self.focus_table = table_id
        elif kind == "hand_start":
            t.players = ev["players"]
            t.hand_no = ev["hand_no"]
            t.holes = ev["holes"]
            t.stacks, t.bets = ev["stacks"], ev["bets"]
            t.folded = [False] * len(t.players)
            t.last = [""] * len(t.players)
            t.board, t.showdown, t.deltas = [], {}, []
            t.button = ev["button"]
            t.pot = sum(ev["bets"])
            t.street = "preflop"
        elif kind == "action":
            seat = ev["seat"]
            t.last[seat] = f"{ev['action']} {ev['amount']}" if ev["amount"] else ev["action"]
            t.board, t.pot, t.stacks, t.bets, t.folded = ev["board"], ev["pot"], ev["stacks"], ev["bets"], ev["folded"]
            t.street = ev["street"]
            if focused and human_here:
                word = ACTION_WORDS.get(ev["action"], ev["action"])
                amt = f" {ev['amount']}" if ev["amount"] else ""
                self.log_line(f"  {ev['name']}: [bold]{ev['action']}[/] ({word}{amt})")
                self._dirty = True
                await asyncio.sleep(0.45)
        elif kind == "hand_end":
            t.board, t.showdown, t.deltas = ev["board"], ev["showdown"], ev["deltas"]
            t.totals = ev["totals"]
            if self.hero in t.totals and focused:
                self.hero_curve.append(t.totals[self.hero])
                del self.hero_curve[:-300]
            if focused and (human_here or ev["hand_no"] % 1000 == 999):
                self._log_hand(t, ev)
            if focused and human_here:
                self._dirty = True
                await asyncio.sleep(1.6)
        elif kind == "match_end":
            t.stats = {p["name"]: p for p in ev["players"]}
            t.totals = {p["name"]: p["chips"] for p in ev["players"]}

    def _log_hand(self, t: TableState, ev: dict) -> None:
        winners = ", ".join(ev["players"][w] for w in ev["winners"])
        pot = sum(p["amount"] for p in ev["pots"])
        hands = " ".join(f"{ev['players'][int(s)]}: {pretty_cards(v['hole'])} ({v['hand']})" for s, v in ev["showdown"].items())
        self.log_line(f"Hand {ev['hand_no'] + 1}: [bold]{winners}[/] win {pot} · {pretty_cards(ev['board'])} {hands}")

    # -- human input -------------------------------------------------------------------------

    async def ask(self, req: dict) -> str:
        self._request = req
        self._pending = asyncio.get_running_loop().create_future()
        legal = req["legal"]
        parts = []
        if "F" in legal:
            parts.append("[bold]F[/] Fold")
        if "X" in legal:
            parts.append("[bold]X[/] Check")
        if "C" in legal:
            parts.append(f"[bold]C[/] Call {req['to_call']}")
        if "R" in legal:
            parts.append(f"[bold]R[/] Raise {req['min_raise']}-{req['max_raise']}")
            parts.append("[bold]A[/] All-in")
        hole = pretty_cards(req["hole"])
        self.query_one("#prompt", Static).update(f"Your turn ({hole}) · " + "   ".join(parts))
        self.query_one("#actionbar").add_class("active")
        self._dirty = True
        try:
            return await self._pending
        finally:
            self.query_one("#actionbar").remove_class("active")
            amount = self.query_one("#amount", Input)
            amount.remove_class("active")
            amount.value = ""
            self._request = None
            self._pending = None

    def _answer(self, reply: str) -> None:
        if self._pending and not self._pending.done():
            self._pending.set_result(reply)

    def on_key(self, event) -> None:
        if self.query_one("#chat-input", Input).has_focus:
            return
        if not self._request or self.query_one("#amount", Input).has_focus:
            return
        key = event.key.upper()
        legal = self._request["legal"]
        if key in ("F", "X", "C") and key in legal:
            self._answer(key)
        elif key == "A" and "R" in legal:
            self._answer("A")
        elif key == "R" and "R" in legal:
            amount = self.query_one("#amount", Input)
            amount.add_class("active")
            amount.value = str(self._request["min_raise"])
            amount.focus()
        else:
            return
        event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "chat-input":
            text = event.value.strip()
            event.input.value = ""
            if text and self._chat_send:
                self.run_worker(self._chat_send(text), group="chat")
            return
        if self._request and event.value.strip():
            self._answer(f"R {event.value.strip()}")

    # -- actions -----------------------------------------------------------------------------

    def action_pause(self) -> None:
        if self.paused.is_set():
            self.paused.clear()
            self.status = "paused"
        else:
            self.paused.set()
            self.status = "running"
        self._dirty = True

    def action_next_table(self) -> None:
        ids = list(self.tables)
        if ids:
            i = ids.index(self.focus_table) if self.focus_table in ids else -1
            self.focus_table = ids[(i + 1) % len(ids)]
            self._dirty = True

    # -- rendering ---------------------------------------------------------------------------

    def _render(self) -> None:
        if self.lobby_until:
            self._dirty = True  # countdown ticks
        if not self._dirty:
            return
        self._dirty = False
        t = self.tables.get(self.focus_table or "")
        top = f"♠ POKBOT ♥  {GAME_NAMES.get(self.game, self.game)}"
        if self.round_no:
            top += f" · Round {self.round_no}"
        if t:
            top += f" · Table {t.table_id} · Hand {t.hand_no + 1}/{t.hands_total}"
        top += f" · {self.status}"
        if self.online is not None:
            top += f" · [green]●[/] {self.online} online · {self.playing} playing"
        self.query_one("#top", Static).update(top)

        if self.lobby_until and not t:
            self.query_one("#table", Static).update(self._lobby_panel())
            return
        if not t or not t.players:
            return
        self.query_one("#table", Static).update(self._table_panel(t))
        self._update_stats(t)
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=max(t.hands_total, 1), progress=t.hand_no + 1)
        self.query_one("#chart", Sparkline).data = list(self.hero_curve)
        label = f"{self.hero} chips: {int(self.hero_curve[-1]):+d}" if self.hero else "Chips"
        self.query_one("#chart-label", Static).update(label)

    def _lobby_panel(self) -> Panel:
        left = max(0, int((self.lobby_until - datetime.now(UTC)).total_seconds()))
        mins, secs = divmod(left, 60)
        clock = Text(f"{mins:02d}:{secs:02d}", style="bold #e8d9a0")
        body = Group(
            Align.center(Text("Next tournament starts in", style="#9fbfa8")),
            Align.center(Text(" ".join(clock.plain), style="bold #e8d9a0")),
            Align.center(Text(f"at {self.lobby_until.astimezone():%H:%M} · tournaments run every 30 minutes", style="#9fbfa8")),
        )
        return Panel(Align.center(body, vertical="middle"), border_style="#2c6e49", title="Lobby")

    def _table_panel(self, t: TableState) -> Panel:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(width=2)
        grid.add_column(min_width=14)
        grid.add_column(justify="right")
        grid.add_column(justify="right")
        grid.add_column()
        grid.add_column(min_width=6)
        grid.add_column(justify="right")
        for seat, name in enumerate(t.players):
            marker = Text("D", style="bold black on #e8d9a0") if seat == t.button else Text("")
            is_hero = name == self.hero
            name_style = "bold #ffd75f" if is_hero else ("grey62" if t.folded[seat] else "bright_white")
            hole = t.holes[seat] if seat < len(t.holes) else []
            visible = not self.hero_is_human or is_hero or str(seat) in t.showdown
            shown = cards_text(hole if visible else [None] * len(hole))
            if t.folded[seat]:
                shown = Text("folded", style="grey50")
            last = t.last[seat] if seat < len(t.last) else ""
            delta = Text("")
            if t.deltas:
                d = t.deltas[seat]
                delta = Text(f"{d:+d}", style="bold green" if d > 0 else ("red" if d < 0 else "grey50"))
            grid.add_row(
                marker,
                Text(name, style=name_style),
                Text(str(t.stacks[seat] if seat < len(t.stacks) else ""), style="#e8d9a0"),
                Text(str(t.bets[seat]) if seat < len(t.bets) and t.bets[seat] else "", style="cyan"),
                shown,
                Text(last, style="bold magenta"),
                delta,
            )
        board = cards_text(t.board + [None] * (5 - len(t.board))) if self.game == "holdem" else Text("")
        info = Text(f"Pot {t.pot}  ·  {t.street.title()}", style="bold #e8d9a0")
        if t.showdown:
            info.append("  ·  " + "  ".join(f"{t.players[int(s)]}: {v['hand']}" for s, v in t.showdown.items()), style="bright_white")
        body = Group(Align.center(board), Align.center(info), Text(""), grid)
        return Panel(body, title=f"[bold]Table {t.table_id}[/]", border_style="#2c6e49", style="on #0f2a1c")

    def _update_stats(self, t: TableState) -> None:
        stats = self.query_one("#stats", DataTable)
        stats.clear()
        rows = sorted(t.totals.items(), key=lambda kv: -kv[1])
        hands = max(t.hand_no + 1, 1)
        for name, chips in rows:
            s = t.stats.get(name, {})
            bb100 = s.get("bb_per_100", chips / 2 / hands * 100)
            vpip = f"{s['vpip'] / s['hands']:.0%}" if s.get("hands") else "-"
            pfr = f"{s['pfr'] / s['hands']:.0%}" if s.get("hands") else "-"
            style = "bold #ffd75f" if name == self.hero else ""
            stats.add_row(Text(name, style=style), f"{chips:+d}", f"{bb100:+.1f}", vpip, pfr)
