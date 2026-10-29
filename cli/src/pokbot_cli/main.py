"""`pokbot` command line.

    pokbot play [FILE ...]        enter the next tournament (no FILE = play yourself)
    pokbot validate FILE          check a bot offline
"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from pokbot_core.engine.variants import VARIANTS, get_variant
from pokbot_core.match import MatchConfig
from pokbot_core.players import SubprocessBot
from pokbot_core.tournament import Tournament, TournamentConfig
from rich.console import Console

from . import config, online
from .plain import PlainPrinter
from .validate import Report
from .validate import validate as run_validate

EXIT_OK, EXIT_INVALID, EXIT_USAGE, EXIT_SERVER = 0, 1, 2, 3

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    rich_markup_mode="rich",
    help="[bold]pokbot[/] · write poker bots, enter them in tournaments every 30 minutes.",
)
console = Console(highlight=False)
err = Console(stderr=True, highlight=False)

GameOpt = Annotated[str, typer.Option("--game", "-g", help="holdem | omaha | stud | draw")]
ServerOpt = Annotated[str | None, typer.Option("--server", help="Server URL (default from login).")]


def _check_game(game: str) -> None:
    try:
        get_variant(game)
    except ValueError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_USAGE) from None


def _use_tui(plain: bool, as_json: bool) -> bool:
    return not (plain or as_json) and sys.stdout.isatty() and sys.stdin.isatty()


def _print_report(report: Report) -> None:
    icons = {"pass": "[green]✔[/]", "warn": "[yellow]![/]", "fail": "[red]✘[/]"}
    console.print(f"[bold]validate[/] {report.target}")
    for c in report.checks:
        console.print(f"  {icons[c.status]} {c.name:<14} [grey62]{c.detail}[/]")


def _validate_or_exit(target: str, hands: int, game: str, quiet_json: bool = False) -> None:
    report = asyncio.run(run_validate(target, hands=hands, game=game))
    if not quiet_json:
        _print_report(report)
    if not report.ok():
        err.print("[red]bot failed validation; fix it before playing[/]")
        raise typer.Exit(EXIT_INVALID)


@app.command()
def play(
    files: Annotated[list[str] | None, typer.Argument(help="Bot file(s): mybot.py, mybot.py:MyBot, ./mybot, bot.js or a folder with pokbot.toml. Omit to play yourself.")] = None,
    local: Annotated[bool, typer.Option("--local", help="Run the tournament on this machine against [BOT]s; no server.")] = False,
    upload: Annotated[bool, typer.Option("--upload", help="Upload the bot; the server enters it in every tournament.")] = False,
    name: Annotated[str | None, typer.Option("--name", "-n", help="Display name.")] = None,
    game: GameOpt = "holdem",
    hands: Annotated[int | None, typer.Option("--hands", help="Hands per table (--local only). Default 10,000 for bots, 30 with a human.")] = None,
    table_size: Annotated[int, typer.Option("--table-size", help="Seats per table (--local only).")] = 6,
    seed: Annotated[int | None, typer.Option("--seed", help="Deterministic seed (--local only).")] = None,
    skip_validate: Annotated[bool, typer.Option("--skip-validate", help="Don't validate first.")] = False,
    plain: Annotated[bool, typer.Option("--plain", help="Line output instead of the live view.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable results on stdout.")] = False,
    server: ServerOpt = None,
) -> None:
    """Enter a tournament. Tables of up to 6; the top 2 at each table advance until one champion is left.
    Empty seats are filled by [BOT] house players unless every seat is human."""
    _check_game(game)
    files = files or []
    if (hands or seed is not None) and not local:
        err.print("[red]--hands and --seed only apply with --local[/]")
        raise typer.Exit(EXIT_USAGE)
    if upload and (local or len(files) != 1):
        err.print("[red]--upload takes exactly one FILE and no --local[/]")
        raise typer.Exit(EXIT_USAGE)
    if not local and len(files) > 1:
        err.print("[red]online play takes one FILE; use --local to run several bots against each other[/]")
        raise typer.Exit(EXIT_USAGE)

    if not skip_validate:
        for f in files:
            _validate_or_exit(f, hands=200, game=game, quiet_json=as_json)

    if upload:
        _do_upload(files[0], name, server)
        return

    human = not files
    hero = name or (getpass.getuser() if human else Path(files[0].split(":")[0]).stem)

    if local:
        code = _play_local(files, hero, human, game, hands, table_size, seed, plain, as_json)
    else:
        code = _play_online(files[0] if files else None, hero, game, plain, as_json, server)
    raise typer.Exit(code)


def _play_local(files, hero, human, game, hands, table_size, seed, plain, as_json) -> int:
    bot_hands = hands or 10_000
    human_hands = hands or 30
    tcfg = TournamentConfig(
        table_size=table_size, bot_hands=bot_hands, human_hands=human_hands,
        match=MatchConfig(game=game), seed=seed,
    )

    def entrants(app=None):
        players = []
        if human:
            from .tui import HumanPlayer

            players.append(HumanPlayer(hero, app))
        for i, f in enumerate(files):
            stem = Path(f.split(":")[0]).stem
            players.append(SubprocessBot.from_file(f, name=hero if i == 0 and len(files) == 1 else stem))
        return players

    if _use_tui(plain, as_json):
        from .tui import PokbotApp

        async def source(app: PokbotApp) -> None:
            await Tournament(entrants(app), tcfg, on_event=app.handle).run()

        tui = PokbotApp(source, hero=hero, hero_is_human=human, game=game)
        tui.run()
        return EXIT_OK

    if human:
        err.print("[red]playing yourself needs an interactive terminal[/]")
        return EXIT_USAGE
    printer = PlainPrinter(hero=hero, as_json=as_json)
    asyncio.run(Tournament(entrants(), tcfg, on_event=printer.handle).run())
    return EXIT_OK


def _play_online(target, hero, game, plain, as_json, server) -> int:
    url = config.server_url(server)
    token = config.token()
    if not token:
        err.print("[red]sign in with GitHub first: pokbot login[/]")
        return EXIT_USAGE
    hero = config.load().get("name") or hero  # the server seats you under your account name
    bot = SubprocessBot.from_file(target, name=hero) if target else None

    try:
        if _use_tui(plain, as_json):
            from .tui import PokbotApp

            async def source(app: PokbotApp) -> None:
                await app.handle({"type": "info", "message": f"connecting to {url} as [bold]{hero}[/]…"})
                await online.play_live(url, token, hero, app.handle, bot=bot,
                                       ask=None if bot else app.ask, game=game,
                                       on_ready=app.set_chat_sender)

            PokbotApp(source, hero=hero, hero_is_human=bot is None, game=game).run()
            return EXIT_OK
        if bot is None:
            err.print("[red]playing yourself needs an interactive terminal[/]")
            return EXIT_USAGE
        printer = PlainPrinter(hero=hero, as_json=as_json)
        asyncio.run(online.play_live(url, token, hero, printer.handle, bot=bot, game=game))
        return EXIT_OK
    except (online.ServerError, OSError) as e:
        err.print(f"[red]server error:[/] {e}")
        return EXIT_SERVER


def _do_upload(target: str, name: str | None, server: str | None) -> None:
    url = config.server_url(server)
    token = config.token()
    login_name = config.load().get("name")
    if not token or not login_name:
        err.print("[red]sign in first: pokbot login[/]")
        raise typer.Exit(EXIT_USAGE)
    # Bot names are namespaced by account: `alice` or `alice-<suffix>`.
    if name and name != login_name and not name.startswith(login_name + "-"):
        name = f"{login_name}-{name}"
    try:
        info = online.upload(url, token, target, name or login_name)
    except (online.ServerError, ValueError) as e:
        err.print(f"[red]upload failed:[/] {e}")
        raise typer.Exit(EXIT_SERVER) from None
    console.print(f"[green]uploaded[/] [bold]{info['name']}[/] · entered in every tournament")
    console.print(f"next tournament starts at [bold]{info['next_tournament']}[/]")


@app.command()
def validate(
    file: Annotated[str, typer.Argument(help="Bot file or folder to check.")],
    hands: Annotated[int, typer.Option("--hands", help="Hands to play against [BOT]s.")] = 500,
    time_ms: Annotated[int, typer.Option("--time-ms", help="Per-decision compute cap (server default 5 ms).")] = 5,
    strict: Annotated[bool, typer.Option("--strict", help="Treat warnings as failures.")] = False,
    game: GameOpt = "holdem",
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check a bot offline: loads, speaks the protocol, makes only legal moves, answers in time."""
    _check_game(game)
    report = asyncio.run(run_validate(file, hands=hands, time_ms=time_ms, game=game))
    if as_json:
        sys.stdout.write(json.dumps(report.to_dict()) + "\n")
    else:
        _print_report(report)
        console.print("[green]valid[/]" if report.ok(strict) else "[red]invalid[/]")
    raise typer.Exit(EXIT_OK if report.ok(strict) else EXIT_INVALID)


TEMPLATE_PY = '''from pokbot_core.sdk import Bot


class {cls}(Bot):
    """Reply with one capital letter: F fold, X check, C call, R <amount> raise to, A all-in."""

    def act(self, state):
        legal = state["legal"]          # e.g. ["F", "C", "R"]
        hole = state["hole"]            # e.g. ["Ah", "Kd"]
        if "R" in legal and hole[0][0] == hole[1][0]:   # raise pocket pairs
            return f"R {{state['min_raise']}}"
        if "X" in legal:
            return "X"
        return "C" if state["to_call"] <= state["pot"] // 3 else "F"
'''

TEMPLATE_JS = '''// Reply with one line per action_request: F, X, C, "R <amount>" or A.
const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  const msg = JSON.parse(line);
  if (msg.type !== "action_request") return;
  const reply = msg.legal.includes("X") ? "X" : "C";
  process.stdout.write(reply + "\\n");
});
'''


@app.command()
def init(
    name: Annotated[str, typer.Argument(help="Folder to create.")],
    lang: Annotated[str, typer.Option("--lang", help="py | js")] = "py",
) -> None:
    """Scaffold a new bot folder with pokbot.toml."""
    root = Path(name)
    if root.exists():
        err.print(f"[red]{root} already exists[/]")
        raise typer.Exit(EXIT_USAGE)
    if lang not in ("py", "js"):
        err.print("[red]--lang must be py or js[/]")
        raise typer.Exit(EXIT_USAGE)
    root.mkdir(parents=True)
    entry = "bot.py" if lang == "py" else "bot.js"
    cls = "".join(part.title() for part in root.name.replace("-", "_").split("_")) or "MyBot"
    (root / entry).write_text(TEMPLATE_PY.format(cls=cls) if lang == "py" else TEMPLATE_JS)
    runtime = "python" if lang == "py" else "node"
    (root / "pokbot.toml").write_text(f'name = "{root.name}"\nentrypoint = "{entry}"\nruntime = "{runtime}"\n')
    console.print(f"[green]created[/] {root}/ · try: [bold]pokbot validate {root}[/] then [bold]pokbot play {root} --local[/]")


def _copy_to_clipboard(text: str) -> bool:
    import shutil
    import subprocess

    for cmd in (["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"], ["clip.exe"]):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=3)
                return True
            except (subprocess.SubprocessError, OSError):
                continue
    return False


@app.command()
def login(
    name: Annotated[str | None, typer.Option("--name", "-n", hidden=True, help="Dev servers (POKBOT_DEV_LOGIN=1) only.")] = None,
    server: ServerOpt = None,
) -> None:
    """Sign in with GitHub (device code). Stores your API key in ~/.config/pokbot/credentials."""
    import time

    from rich.panel import Panel

    url = config.server_url(server)
    try:
        if name:
            info = online.register(url, name)
        else:
            try:
                flow = online.device_start(url)
            except online.ServerError as e:
                if str(e).startswith("501"):
                    err.print("[yellow]this server has no GitHub login; use[/] pokbot login --name NAME")
                    raise typer.Exit(EXIT_USAGE) from None
                raise
            code = flow["user_code"]
            console.print(Panel(f"[dim]your code[/]\n[bold]{code}[/]", expand=False, padding=(0, 3), title_align="center"), justify="left")
            console.print(" ")
            if _copy_to_clipboard(code):
                console.print("[green]✓[/] copied to your clipboard")
            console.print(f"› paste it at [bold underline]{flow['verification_uri']}[/]")
            interval = flow["interval"]
            deadline = time.time() + flow["expires_in"]
            with console.status("[dim]waiting for GitHub…[/]", spinner="dots"):
                while True:
                    if time.time() > deadline:
                        err.print("[red]code expired; run `pokbot login` again[/]")
                        raise typer.Exit(EXIT_SERVER)
                    time.sleep(interval)
                    info = online.device_poll(url, flow["session"])
                    if info["status"] == "ok":
                        break
                    interval = info.get("interval", interval)
    except online.ServerError as e:
        err.print(f"[red]✗ login failed:[/] {e}")
        raise typer.Exit(EXIT_SERVER) from None
    config.save({"server": url, "name": info["name"], "token": info["token"]})
    console.print(f"[green]✓[/] signed in as [bold]{info['name']}[/]")


@app.command()
def logout() -> None:
    """Forget the stored API key."""
    data = config.load()
    data.pop("token", None)
    data.pop("name", None)
    config.save(data)
    console.print("[green]✓[/] signed out")


@app.command()
def whoami() -> None:
    """Show who you are signed in as."""
    data = config.load()
    if not data.get("token"):
        console.print("not signed in · run [bold]pokbot login[/]")
        raise typer.Exit(EXIT_USAGE)
    console.print(f"[bold]{data.get('name')}[/] on {data.get('server', config.DEFAULT_SERVER)}")


@app.command()
def chat(server: ServerOpt = None) -> None:
    """Join the lobby chat, see who is online, and watch the running tournament (alias: watch)."""
    url = config.server_url(server)
    token = config.token()
    name = config.load().get("name")
    if not sys.stdout.isatty():
        err.print("[red]chat needs an interactive terminal[/]")
        raise typer.Exit(EXIT_USAGE)
    from .tui import PokbotApp

    async def source(app: PokbotApp) -> None:
        note = f"signed in as [bold]{name}[/]" if token else "not signed in: read-only (run [bold]pokbot login[/] to talk)"
        await app.handle({"type": "info", "message": f"connected to {url} · {note}"})
        await online.watch(url, token, app.handle, on_ready=app.set_chat_sender)

    try:
        PokbotApp(source, hero=name, title="pokbot chat").run()
    except OSError as e:
        err.print(f"[red]server error:[/] {e}")
        raise typer.Exit(EXIT_SERVER) from None


app.command(name="watch", help="Alias for chat: spectate the running tournament.")(chat)


@app.command()
def games() -> None:
    """List the poker variants."""
    for v in VARIANTS.values():
        state = "[green]playable[/]" if v.available else "[grey50]coming soon[/]"
        console.print(f"  [bold]{v.key:<7}[/] {v.name:<16} {state}  [grey62]{v.description}[/]")


@app.command()
def schedule(server: ServerOpt = None) -> None:
    """When the next tournament starts (every 30 minutes, on the clock)."""
    try:
        info = online.schedule(config.server_url(server))
    except online.ServerError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_SERVER) from None
    console.print(f"next tournament: [bold]{info['next_tournament']}[/] · {info['entrants']} entered so far"
                  f" · [green]●[/] {info.get('online', 0)} online")
    if info.get("running"):
        console.print(f"running now: {info['running']}")


@app.command()
def leaderboard(server: ServerOpt = None) -> None:
    """Tournament champions and totals."""
    try:
        info = online.leaderboard(config.server_url(server))
    except online.ServerError as e:
        err.print(f"[red]{e}[/]")
        raise typer.Exit(EXIT_SERVER) from None
    from rich.table import Table

    table = Table(title="pokbot leaderboard")
    for col in ("#", "Player", "Points", "Titles", "Tournaments", "Chips", "Hands"):
        table.add_column(col)
    for i, row in enumerate(info["players"], 1):
        table.add_row(str(i), row["name"], f"[bold]{row.get('points', 0)}[/]", str(row["titles"]),
                      str(row["tournaments"]), f"{row['chips']:+d}", str(row["hands"]))
    console.print(table)


if __name__ == "__main__":
    app()
