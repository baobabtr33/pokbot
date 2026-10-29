"""Line-oriented output for non-TTY use (`--plain`, CI, pipes). Same events as the TUI."""

from __future__ import annotations

import json
import sys

from pokbot_core.display import pretty_cards
from rich.console import Console
from rich.markup import escape
from rich.table import Table

console = Console(highlight=False)

WINNER = r"""
  *  .  *    W I N N E R    *  .  *
"""


class PlainPrinter:
    def __init__(self, hero: str | None = None, as_json: bool = False) -> None:
        self.hero = hero
        self.as_json = as_json
        self.result: dict | None = None

    async def handle(self, event: dict) -> None:
        if self.as_json:
            if event["type"] in ("tournament_end", "table_end", "validate"):
                sys.stdout.write(json.dumps(event) + "\n")
            if event["type"] == "tournament_end":
                self.result = event
            return
        kind = event["type"]
        if kind == "lobby":
            console.print(f"[#9fbfa8]next tournament at {event['starts_at']} · {event.get('entrants', 0)} entered · waiting…[/]")
        elif kind == "chat":
            who = "[yellow]pokbot[/]" if event.get("system") else f"[cyan]{event['name']}[/]"
            console.print(f"{who}: {escape(event['text'])}")
        elif kind == "presence":
            pass
        elif kind == "info":
            console.print(event["message"])
        elif kind == "tournament_start":
            console.print(f"[bold]Tournament {event['tournament_id']}[/] · {len(event['entrants'])} entrants")
        elif kind == "round_start":
            label = "FINAL TABLE" if event["final"] else f"Round {event['round']}"
            console.print(f"[bold cyan]{label}[/] · " + " | ".join(", ".join(t) for t in event["tables"]))
        elif kind == "table":
            ev = event["event"]
            if ev["type"] == "hand_end" and (ev["hands_played"] % 1000 == 0):
                me = f" · {self.hero} {ev['totals'].get(self.hero, 0):+d}" if self.hero in ev["totals"] else ""
                console.print(f"  {event['table']}: {ev['hands_played']} hands ({ev['hands_played'] / max(ev['elapsed'], 1e-9):,.0f}/s){me}")
            elif ev["type"] == "hand_end" and ev.get("human"):
                winners = ", ".join(ev["players"][w] for w in ev["winners"])
                console.print(f"  hand {ev['hand_no'] + 1}: {winners} · board {pretty_cards(ev['board'])}")
        elif kind == "table_end":
            table = Table(title=f"Table {event['table']}", show_edge=False)
            for col in ("#", "Player", "Chips", "bb/100", "Illegal", "Timeouts", ""):
                table.add_column(col)
            for s in event["standings"]:
                table.add_row(str(s["place"]), s["name"], f"{s['chips']:+d}", f"{s['bb_per_100']:+.1f}",
                              str(s["illegal"]), str(s["timeouts"]), "advances" if s["advances"] else "")
            console.print(table)
        elif kind == "tournament_end":
            self.result = event
            console.print(f"[bold yellow]{WINNER}[/]")
            console.print(f"[bold yellow]  🏆  {event['winner']}  🏆[/]  ({event['rounds']} rounds)")
            ranked = sorted(event.get("points", {}).items(), key=lambda kv: -kv[1])
            if ranked:
                console.print("  points: " + " · ".join(f"{n} +{p}" for n, p in ranked) + "\n")
