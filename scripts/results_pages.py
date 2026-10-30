"""Static results pages for the BOT Arena site: leaderboard, every bot, every tournament.

Used by build_pages.py. Each page is plain HTML (crawlable, no JavaScript) with its own title,
description and canonical URL, so tournaments and bots become searchable pages. Stdlib only.
"""

from __future__ import annotations

import html
import json
import urllib.parse
import urllib.request
from pathlib import Path


def fetch(api: str) -> tuple[list[dict], list[dict]]:
    """(leaderboard rows, recent tournaments) from the game server's public API."""

    def get(path: str) -> dict:
        with urllib.request.urlopen(f"{api}{path}", timeout=20) as r:
            return json.load(r)

    return get("/api/leaderboard")["players"], get("/api/tournaments")["tournaments"]


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _slug(name: str) -> str:
    return urllib.parse.quote(name, safe="-_.")


def _page(site: str, path: str, title: str, description: str, body: str, api: str) -> str:
    url = f"{site}{path}"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<meta name="description" content="{_e(description)}">
<link rel="canonical" href="{_e(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="pokbot BOT Arena">
<meta property="og:title" content="{_e(title)}">
<meta property="og:description" content="{_e(description)}">
<meta property="og:url" content="{_e(url)}">
<meta property="og:image" content="{_e(site)}/og.png">
<meta name="twitter:card" content="summary_large_image">
<link rel="stylesheet" href="{_e(site)}/style.css">
</head>
<body>
<main class="wrap">
<p><a href="{_e(site)}/">BOT Arena</a> · <a href="{_e(site)}/leaderboard/">Leaderboard</a> · <a href="{_e(site)}/tournaments/">Tournaments</a></p>
{body}
<section>
<h2>Submit your bot to compete</h2>
<pre class="terminal"><code><span class="p">$</span> curl -fsSL {_e(api)}/install.sh | sh
<span class="p">$</span> pokbot login && pokbot init mybot
<span class="p">$</span> pokbot submit mybot</code></pre>
<p>Tournaments start every 30 minutes. <a href="{_e(site)}/bots.md">Bot author guide</a> · <a href="{_e(site)}/llms.txt">llms.txt</a></p>
</section>
</main>
<footer class="wrap"><p>pokbot BOT Arena · poker bots compete every 30 minutes</p></footer>
</body>
</html>
"""


def _table(head: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in head)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="table-wrap"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def _bot_link(site: str, name: str) -> str:
    if name.startswith("[BOT]"):
        return _e(name)
    return f'<a href="{_e(site)}/bots/{_slug(name)}/">{_e(name)}</a>'


def build(site: str, api: str, out: Path, board: list[dict], tournaments: list[dict]) -> list[str]:
    """Write the pages under `out`; returns their paths (for the sitemap)."""
    paths: list[str] = []

    def write(path: str, title: str, description: str, body: str) -> None:
        target = out / path.strip("/") / "index.html"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_page(site, path, title, description, body, api))
        paths.append(path)

    rows = [
        [str(i + 1), _bot_link(site, r["name"]), str(r.get("points", 0)), str(r["titles"]),
         str(r["tournaments"]), f"{r['chips']:+,}"]
        for i, r in enumerate(board)
    ]
    leader = f"{board[0]['name']} leads with {board[0].get('points', 0)} points. " if board else ""
    write(
        "/leaderboard/",
        "Poker bot leaderboard · BOT Arena",
        f"Live ranking of poker bots in the BOT Arena. {leader}Submit your bot to compete every 30 minutes.",
        "<h1>Poker bot leaderboard</h1>"
        + (_table(["#", "Bot", "Points", "Titles", "Tournaments", "Chips"], rows) if rows else "<p>No results yet.</p>"),
    )

    t_rows = []
    history: dict[str, list[tuple[dict, dict]]] = {}
    for t in tournaments:
        tid = t["tournament_id"]
        t_rows.append([f'<a href="{_e(site)}/tournaments/{_slug(tid)}/">{_e(tid)}</a>', _e(t["finished"][:16].replace("T", " ")),
                       _bot_link(site, t["winner"]) if t.get("winner") else "nobody", str(t["entrants"])])
        sections = []
        for table in t.get("tables", []):
            label = "Final table" if table["final"] else f"Round {table['round']} · table {_e(table['table'])}"
            body = [[str(s["place"]), _bot_link(site, s["name"]), str(s.get("sets_won") or "-"), f"{s['chips']:+,}",
                     str(s["hands"]), "advances" if s.get("advances") else ""] for s in table["standings"]]
            sections.append(f"<h3>{label}</h3>" + _table(["#", "Bot", "Sets", "Chips", "Hands", ""], body))
            for s in table["standings"]:
                if not s["name"].startswith("[BOT]"):
                    history.setdefault(s["name"], []).append((t, s))
        points = ", ".join(f"{_bot_link(site, n)} +{p}" for n, p in sorted(t.get("points", {}).items(), key=lambda kv: -kv[1]))
        write(
            f"/tournaments/{_slug(tid)}/",
            f"Tournament {tid}: {t.get('winner') or 'no winner'} wins · BOT Arena",
            f"Poker bot tournament {tid}: {t['entrants']} bots, won by {t.get('winner') or 'nobody'}. Every table, set and score.",
            f"<h1>Tournament {_e(tid)}</h1><p>🏆 Champion: <b>{_bot_link(site, t['winner']) if t.get('winner') else 'nobody'}</b>"
            f" · {t['entrants']} bots · {t['rounds']} round(s) · finished {_e(t['finished'][:16].replace('T', ' '))} UTC</p>"
            + (f"<p>Points: {points}</p>" if points else "")
            + "".join(sections),
        )
    write(
        "/tournaments/",
        "Poker bot tournament results · BOT Arena",
        "Results of every recent BOT Arena poker bot tournament: champions, tables and points.",
        "<h1>Tournament results</h1>"
        + (_table(["Tournament", "Finished (UTC)", "Champion", "Bots"], t_rows) if t_rows else "<p>No tournaments yet.</p>"),
    )

    for rank, r in enumerate(board):
        name = r["name"]
        played = [[f'<a href="{_e(site)}/tournaments/{_slug(t["tournament_id"])}/">{_e(t["tournament_id"])}</a>', str(s["place"]),
                   str(s.get("sets_won") or "-"), f"{s['chips']:+,}", "🏆" if t.get("winner") == name else ""]
                  for t, s in history.get(name, [])]
        badge = f"[![BOT Arena](https://img.shields.io/endpoint?url={api}/api/badge/{name})]({site}/bots/{_slug(name)}/)"
        write(
            f"/bots/{_slug(name)}/",
            f"{name} · poker bot #{rank + 1} · BOT Arena",
            f"{name} is ranked #{rank + 1} in the BOT Arena with {r.get('points', 0)} points and {r['titles']} title(s). "
            f"Challenge it: pokbot play mybot --vs {name}",
            f"<h1>{_e(name)}</h1><p>Rank <b>#{rank + 1}</b> · {r.get('points', 0)} points · {r['titles']} title(s) · "
            f"{r['tournaments']} tournament(s) · {r['chips']:+,} chips over {r['hands']:,} hands</p>"
            f"<h2>Challenge it</h2><pre class=\"terminal\"><code><span class=\"p\">$</span> pokbot play mybot --vs {_e(name)}"
            f"\n<span class=\"p\">$</span> pokbot play --vs {_e(name)}   <span class=\"c\"># or play it by hand</span></code></pre>"
            f"<h2>Badge</h2><p>Put this in your README:</p><pre class=\"terminal\"><code>{_e(badge)}</code></pre>"
            + ("<h2>Recent tournaments</h2>" + _table(["Tournament", "Place", "Sets", "Chips", ""], played) if played else ""),
        )
    return paths
