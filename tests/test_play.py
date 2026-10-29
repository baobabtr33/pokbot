import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pokbot_cli import __version__ as CLI
from pokbot_cli.validate import validate
from pokbot_core.engine import HoldemHand
from pokbot_core.match import Match, MatchConfig
from pokbot_core.players import HouseBot
from pokbot_core.protocol import ProtocolError, parse_reply
from pokbot_core.tournament import Tournament, TournamentConfig, next_start

EXAMPLES = Path(__file__).parent.parent / "bots" / "examples"


def test_parse_reply_letters_and_json():
    req = HoldemHand([200, 200], 0, (1, 2), 1).request()
    assert parse_reply("C\n", req).kind == "C"
    assert parse_reply("f", req).kind == "F"
    r = parse_reply("R 10", req)
    assert (r.kind, r.amount) == ("R", 10)
    a = parse_reply("A", req)
    assert (a.kind, a.amount) == ("R", req["max_raise"])
    assert parse_reply('{"action": "R", "amount": 8}', req).amount == 8
    with pytest.raises(ProtocolError):
        parse_reply("BET", req)
    with pytest.raises(ProtocolError):
        parse_reply("R lots", req)


async def test_duplicate_match_is_zero_sum_and_mirrored():
    players = [HouseBot("a", "caller"), HouseBot("b", "caller")]
    summary = await Match(players, MatchConfig(hands=200, seed=9)).run()
    a, b = summary["players"]
    assert a["chips"] + b["chips"] == 0
    # Two identical strategies under duplicate seating break exactly even.
    assert a["chips"] == 0


class Entrant(HouseBot):
    kind = "bot"

    def __init__(self, name):
        super().__init__(name, "tight", seed=len(name))
        self.name = name


@pytest.mark.parametrize("count", [1, 2, 5, 6, 7, 13, 40])
async def test_tournament_always_has_one_winner(count):
    entrants = [Entrant(f"bot{i}") for i in range(count)]
    events = []
    result = await Tournament(entrants, TournamentConfig(bot_hands=20, seed=1), on_event=events.append).run()
    assert result["winner"] in {e.name for e in entrants}
    rounds = [e for e in events if e["type"] == "round_start"]
    assert rounds[-1]["final"]
    for r in rounds:
        for table in r["tables"]:
            assert len(table) == 6  # empty seats filled with [BOT]s


async def test_all_human_tables_get_no_house_bots():
    class Human(HouseBot):
        kind = "human"

    humans = [Human(f"h{i}", "caller") for i in range(3)]
    for h in humans:
        h.name = h.name.removeprefix("[BOT] ")
    events = []
    await Tournament(humans, TournamentConfig(human_hands=2, seed=1), on_event=events.append).run()
    table = next(e for e in events if e["type"] == "round_start")["tables"][0]
    assert not any(n.startswith("[BOT]") for n in table)


def test_next_start_is_on_the_half_hour():
    from datetime import UTC, datetime

    t = next_start(datetime(2026, 10, 28, 12, 7, 13, tzinfo=UTC), 1800)
    assert (t.hour, t.minute, t.second) == (12, 30, 0)
    t = next_start(datetime(2026, 10, 28, 12, 30, 0, tzinfo=UTC), 1800)
    assert (t.hour, t.minute) == (13, 0)


async def test_validate_passes_good_bot_and_fails_broken_one():
    good = await validate(str(EXAMPLES / "pairs.py"), hands=40)
    assert good.ok()
    bad = await validate(str(EXAMPLES / "broken.py"), hands=20, time_ms=200)
    assert not bad.ok()
    missing = await validate("nope.py")
    assert not missing.ok()


def test_server_register_upload_schedule(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    cfg = Settings(data_dir=tmp_path, sandbox="unsafe-local", dev_login=True)
    monkeypatch.setattr(server_app, "settings", cfg)
    monkeypatch.setattr("pokbot_server.sandbox.settings", cfg)
    from pokbot_core.bundle import pack

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        r = client.post("/api/register", json={"name": "alice"})
        assert r.status_code == 200
        token = r.json()["token"]
        assert client.post("/api/register", json={"name": "alice"}).status_code == 400
        assert client.post("/api/register", json={"name": "[BOT] x"}).status_code == 400

        data, _ = pack(str(EXAMPLES / "caller.py"))
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post("/api/bots?name=callme", content=data, headers=auth).status_code == 400
        r = client.post("/api/bots?name=alice-callme", content=data, headers=auth)
        assert r.status_code == 200, r.text
        assert client.post("/api/bots?name=x2", content=data).status_code == 401
        assert [b["name"] for b in client.get("/api/bots").json()["bots"]] == ["alice-callme"]

        # Live play needs a signed-in account, and the seat name is the account name.
        with client.websocket_connect("/ws/play") as ws:
            ws.send_json({"type": "hello", "client": CLI, "name": "mallory", "kind": "bot"})
            assert "sign in" in ws.receive_json()["message"]
        with client.websocket_connect(f"/ws/play?token={token}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "name": "mallory", "kind": "bot"})
            info = ws.receive_json()
            while info["type"] != "info":
                info = ws.receive_json()
            assert "joined as [bold]alice[/]" in info["message"]
        assert client.get("/api/schedule").json()["entrants"] == 1


def test_github_device_login_and_install_script(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    import pokbot_server.github as gh
    from pokbot_server.config import Settings

    monkeypatch.setattr(server_app, "settings", Settings(data_dir=tmp_path, github_client_id="cid"))
    polls = iter([{"error": "authorization_pending"}, {"access_token": "gho_x"}])

    def fake_post(url, fields):
        if url == gh.DEVICE_URL:
            assert fields["client_id"] == "cid"
            return {"device_code": "dc", "user_code": "WXYZ-2345", "verification_uri": "https://github.com/login/device",
                    "interval": 5, "expires_in": 900}
        return next(polls)

    monkeypatch.setattr(gh, "_post_form", fake_post)
    monkeypatch.setattr(gh, "_get_user", lambda token: {"login": "muse-spark", "id": 42})

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        assert client.post("/api/register", json={"name": "bob"}).status_code == 403
        flow = client.post("/api/auth/device").json()
        assert flow["user_code"] == "WXYZ-2345"
        assert client.post("/api/auth/poll", json={"session": flow["session"]}).json()["status"] == "pending"
        done = client.post("/api/auth/poll", json={"session": flow["session"]}).json()
        assert done["name"] == "muse-spark" and done["token"].startswith("pk_")
        me = client.get("/api/whoami", headers={"Authorization": f"Bearer {done['token']}"})
        assert me.json() == {"name": "muse-spark"}

        script = client.get("/install.sh").text
        assert "__POKBOT_BASE__" not in script and 'BASE="${POKBOT_BASE:-http://testserver}"' in script


def test_chat_rules():
    from pokbot_server.chat import ChatError, RateLimiter, clean

    assert clean("  hi\x00 there\u202e ") == "hi there"
    with pytest.raises(ChatError):
        clean("   ")
    with pytest.raises(ChatError):
        clean("x" * 281)
    rl = RateLimiter(count=2, window_s=10)
    assert rl.allow(0) and rl.allow(1) and not rl.allow(2) and rl.allow(11)


def test_chat_and_presence_over_websockets(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    monkeypatch.setattr(server_app, "settings", Settings(data_dir=tmp_path, dev_login=True))

    def until(ws, kind):
        while True:
            msg = ws.receive_json()
            if msg["type"] == kind:
                return msg

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        token = client.post("/api/register", json={"name": "alice"}).json()["token"]
        with client.websocket_connect(f"/ws/watch?token={token}") as alice:
            assert until(alice, "presence")["online"] == 1
            with client.websocket_connect("/ws/watch") as anon:
                assert until(alice, "presence")["online"] == 2
                assert client.get("/api/online").json()["online"] == 2

                anon.send_json({"type": "chat", "text": "hello?"})
                assert "sign in" in until(anon, "chat_error")["message"]

                alice.send_json({"type": "chat", "text": "[bold]gg[/] all"})
                for ws in (alice, anon):
                    msg = until(ws, "chat")
                    assert (msg["name"], msg["text"]) == ("alice", "[bold]gg[/] all")

                for _ in range(5):
                    alice.send_json({"type": "chat", "text": "spam"})
                assert "slow down" in until(alice, "chat_error")["message"]
            assert until(alice, "presence")["online"] == 1

        with client.websocket_connect("/ws/watch") as late:
            history = until(late, "chat_history")["messages"]
            assert history[0]["text"] == "[bold]gg[/] all"


def test_points_system():
    from pokbot_core.points import award_points

    def row(name, kind="bot", advances=False):
        return {"name": name, "kind": kind, "advances": advances}

    result = {"tables": [
        {"final": False, "standings": [row("[BOT] Ace", "house"), row("a", advances=True), row("b", advances=True), row("c")]},
        {"final": True, "standings": [row("[BOT] Ace", "house"), row("b"), row("a")]},
    ]}
    assert award_points(result) == {"a": 1 + 10 + 60, "b": 1 + 10 + 100, "c": 1}


async def test_tournament_reports_points_and_respects_budget():
    import time

    from pokbot_core.tournament import rounds_needed

    assert rounds_needed(6, 6) == 1 and rounds_needed(7, 6) == 2 and rounds_needed(40, 6) == 3
    entrants = [Entrant(f"bot{i}") for i in range(13)]
    start = time.monotonic()
    result = await Tournament(entrants, TournamentConfig(bot_hands=10**9, seed=2, budget_s=1.5)).run()
    assert time.monotonic() - start < 5
    assert result["points"][result["winner"]] >= 100
    assert all(s["hands"] > 0 for t in result["tables"] for s in t["standings"])


async def test_parallel_table_limit_still_finishes_on_budget():
    import time

    entrants = [Entrant(f"bot{i}") for i in range(20)]  # 4 tables in round 1
    cfg = TournamentConfig(bot_hands=10**9, seed=3, budget_s=2.0, max_parallel_tables=1)
    start = time.monotonic()
    result = await Tournament(entrants, cfg).run()
    assert time.monotonic() - start < 5
    assert result["winner"]
    assert all(s["hands"] > 0 for t in result["tables"] for s in t["standings"])


def test_seo_and_agent_pages_use_public_url(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    monkeypatch.setattr(
        server_app, "settings", Settings(data_dir=tmp_path, public_url="https://pokbot.example")
    )
    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        for path in ("/", "/index.html", "/llms.txt", "/bots.md"):
            r = client.get(path)
            assert r.status_code == 200, path
            assert "__POKBOT_BASE__" not in r.text and "https://pokbot.example" in r.text, path
        page = client.get("/").text
        assert '<link rel="canonical" href="https://pokbot.example/"' in page
        assert "https://pokbot.example/og.png" in page
        assert "Sitemap: https://pokbot.example/sitemap.xml" in client.get("/robots.txt").text
        assert "<loc>https://pokbot.example/bots.md</loc>" in client.get("/sitemap.xml").text
        assert client.get("/style.css").status_code == 200
        assert client.get("/openapi.json").status_code == 200


def test_static_site_redirects_and_cors(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    site = "https://someone.github.io/pokbot"
    monkeypatch.setattr(
        server_app,
        "settings",
        Settings(data_dir=tmp_path, public_url="https://api.example", site_url=site),
    )
    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        for path, target in (("/", site + "/"), ("/llms.txt", site + "/llms.txt"), ("/sitemap.xml", site + "/sitemap.xml")):
            r = client.get(path, follow_redirects=False)
            assert r.status_code == 301 and r.headers["location"] == target, path
        script = client.get("/install.sh").text
        assert 'BASE="${POKBOT_BASE:-https://api.example}"' in script
        r = client.get("/api/schedule", headers={"Origin": "https://someone.github.io"})
        assert r.headers["access-control-allow-origin"] == "https://someone.github.io"
        r = client.get("/api/schedule", headers={"Origin": "https://evil.example"})
        assert "access-control-allow-origin" not in r.headers


def test_build_pages(tmp_path):
    import runpy

    build = runpy.run_path(str(Path(__file__).parent.parent / "scripts" / "build_pages.py"))["build"]
    build("https://someone.github.io/pokbot/", "https://api.example", tmp_path)
    page = (tmp_path / "index.html").read_text()
    assert "__POKBOT" not in page
    assert '<link rel="canonical" href="https://someone.github.io/pokbot/"' in page
    assert "curl -fsSL <span class=\"origin\">https://api.example</span>/install.sh" in page
    assert 'const API = "https://api.example".startsWith("__")' in page
    assert "https://someone.github.io/pokbot/bots.md" in (tmp_path / "llms.txt").read_text()
    assert "<loc>https://someone.github.io/pokbot/llms.txt</loc>" in (tmp_path / "sitemap.xml").read_text()
    assert (tmp_path / "og.png").stat().st_size > 1000 and (tmp_path / ".nojekyll").exists()
    assert list(tmp_path.glob("google*.html")), "Search Console verification file must be published"


def test_play_now_key_from_watch_and_after_tournament():
    from pokbot_cli.tui import PokbotApp

    async def idle(app):
        await asyncio.sleep(3600)

    async def scenario(practice, end_tournament):
        app = PokbotApp(idle, hero="me", practice=practice)
        async with app.run_test() as pilot:
            if end_tournament:
                app.result = {"winner": "me"}
            await pilot.press("p")
            await pilot.pause()
            return app.return_value if not app.is_running else "still running"

    assert asyncio.run(scenario("anytime", False)) == "practice"
    assert asyncio.run(scenario("after", False)) == "still running"  # tournament not over yet
    assert asyncio.run(scenario("after", True)) == "practice"
    assert asyncio.run(scenario(None, True)) == "still running"  # local play: no key


def test_tournaments_are_bot_only_and_versus_games_run_now(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_core.bundle import pack
    from pokbot_server.config import Settings

    cfg = Settings(data_dir=tmp_path, sandbox="unsafe-local", dev_login=True, versus_hands=40, human_hands=3)
    for mod in ("app", "sandbox", "games", "hub"):
        monkeypatch.setattr(f"pokbot_server.{mod}.settings", cfg)

    def until(ws, kind):
        while True:
            msg = ws.receive_json()
            if msg["type"] == "error":
                raise AssertionError(msg["message"])
            if msg["type"] == "event" and msg["event"]["type"] == kind:
                return msg["event"]

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        tokens = {n: client.post("/api/register", json={"name": n}).json()["token"] for n in ("alice", "bob")}
        for owner, bot in (("alice", "pairs"), ("bob", "caller")):
            data, _ = pack(str(EXAMPLES / f"{bot}.py"))
            r = client.post(f"/api/bots?name={owner}-{bot}", content=data,
                            headers={"Authorization": f"Bearer {tokens[owner]}"})
            assert r.status_code == 200, r.text

        # Humans can't take a tournament seat: they go to an open table instead.
        entrants = client.get("/api/schedule").json()["entrants"]
        with client.websocket_connect(f"/ws/play?token={tokens['alice']}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "kind": "human"})
            assert "sat down" in ws.receive_json()["message"]
            assert client.get("/api/schedule").json()["entrants"] == entrants
        # Unknown opponent fails fast.
        with client.websocket_connect(f"/ws/play?token={tokens['alice']}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "kind": "human", "vs": "nobody"})
            assert "no uploaded bot" in ws.receive_json()["message"]

        # Bot vs bot: both uploaded bots play on the server; the opponent's cards stay hidden.
        with client.websocket_connect(f"/ws/play?token={tokens['alice']}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "kind": "bot", "vs": "bob", "bot": "alice-pairs"})
            start = until(ws, "versus_start")
            assert start["players"] == ["alice-pairs", "bob-caller"]
            hidden = until(ws, "table")
            while hidden["event"]["type"] != "hand_start":
                hidden = until(ws, "table")
            holes = dict(zip(hidden["event"]["players"], hidden["event"]["holes"]))
            assert None not in holes["alice-pairs"] and holes["bob-caller"] == [None, None]
            end = until(ws, "versus_end")
            assert end["result"]["hands"] == 40
            assert sum(p["chips"] for p in end["result"]["players"]) == 0

        # Someone else's bot can't be borrowed as "yours".
        with client.websocket_connect(f"/ws/play?token={tokens['alice']}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "kind": "bot", "vs": "bob", "bot": "bob-caller"})
            msg = ws.receive_json()
            while msg["type"] != "error":
                msg = ws.receive_json()
            assert "upload your bot" in msg["message"]

        # Human vs bot: answer every action_request by hand.
        with client.websocket_connect(f"/ws/play?token={tokens['bob']}") as ws:
            ws.send_json({"type": "hello", "client": CLI, "kind": "human", "vs": "alice"})
            while True:
                msg = ws.receive_json()
                if msg["type"] == "action_request":
                    ws.send_json({"type": "action", "reply": "C" if "C" in msg["legal"] else "X", "req": msg["req"]})
                elif msg["type"] == "event" and msg["event"]["type"] == "versus_end":
                    assert msg["event"]["result"]["hands"] == 3
                    break
        assert client.get("/api/leaderboard").json()["players"] == []  # games never score


def test_old_cli_is_told_to_update(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    cfg = Settings(data_dir=tmp_path, dev_login=True, min_cli="0.2.0", public_url="https://pokbot.example")
    for mod in ("app", "hub", "games"):
        monkeypatch.setattr(f"pokbot_server.{mod}.settings", cfg)
    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        token = client.post("/api/register", json={"name": "alice"}).json()["token"]
        for client_version in (None, "0.1.9"):
            with client.websocket_connect(f"/ws/play?token={token}") as ws:
                hello = {"type": "hello", "kind": "bot"}
                if client_version:
                    hello["client"] = client_version
                ws.send_json(hello)
                msg = ws.receive_json()
                assert msg["type"] == "error" and "pokbot update" in msg["message"]
                assert "curl -fsSL https://pokbot.example/install.sh | sh" in msg["message"]
        with client.websocket_connect("/ws/watch") as ws:  # watching still works, with a warning
            seen = [ws.receive_json() for _ in range(3)]
            assert any(m["type"] == "info" and "too old" in m["message"] for m in seen)
        assert client.get("/api/version").json()["min_cli"] == "0.2.0"


async def test_tables_play_sets_and_rank_by_sets_won():
    from pokbot_core.tournament import rank_table

    players = [HouseBot("a", "tight", 1), HouseBot("b", "caller", 2), HouseBot("c", "random", 3)]
    events = []

    async def sink(ev):
        events.append(ev)

    result = await Match(players, MatchConfig(hands=95, seed=5, sets=10), on_event=sink).run()
    sets = [e for e in events if e["type"] == "set_end"]
    assert [e["set"] for e in sets] == list(range(1, 11)) and result["sets"] == 10
    assert [e["hands"] for e in sets] == [10] * 9 + [5]  # last set is the short remainder
    assert sum(p["sets_won"] for p in result["players"]) >= 10  # ties credit every leader
    for e in sets:
        assert max(e["chips"].values()) == e["chips"][e["winners"][0]]
    # Sets beat chips: a player with more sets ranks first even with fewer chips.
    fake = {"players": [{"sets_won": 6, "chips": -50, "wins": 1}, {"sets_won": 4, "chips": 900, "wins": 9}]}
    assert rank_table(fake, [None, None]) == [0, 1]


async def test_every_set_is_played_when_the_table_runs_short_of_time():
    import time as _time

    class Slow(HouseBot):
        async def act(self, msg):
            await asyncio.sleep(0.002)
            return await super().act(msg)

    players = [Slow("a", "caller", 1), Slow("b", "tight", 2), Slow("c", "caller", 3)]
    events = []

    async def sink(ev):
        events.append(ev)

    match = Match(players, MatchConfig(hands=100_000, seed=1, sets=10), on_event=sink, deadline=_time.monotonic() + 1.0)
    result = await match.run()
    assert result["stopped_early"] and result["sets"] == 10
    assert len([e for e in events if e["type"] == "set_end"]) == 10


def test_open_tables_seat_humans_any_time_and_evict_bots(tmp_path, monkeypatch):
    import threading

    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    cfg = Settings(data_dir=tmp_path, dev_login=True)
    for mod in ("app", "hub", "games", "tables"):
        monkeypatch.setattr(f"pokbot_server.{mod}.settings", cfg)

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        tokens = {n: client.post("/api/register", json={"name": n}).json()["token"] for n in ("ann", "ben")}
        seen = {"ann": [], "ben": []}
        stop = threading.Event()

        def human(name, hands_wanted, ready):
            with client.websocket_connect(f"/ws/play?token={tokens[name]}") as ws:
                ws.send_json({"type": "hello", "kind": "human", "client": CLI})
                hands = 0
                while hands < hands_wanted and not stop.is_set():
                    msg = ws.receive_json()
                    if msg["type"] == "error":
                        raise AssertionError(msg["message"])
                    if msg["type"] == "action_request":
                        ws.send_json({"type": "action", "reply": "X" if "X" in msg["legal"] else "C", "req": msg["req"]})
                    elif msg["type"] == "event" and msg["event"]["type"] == "table":
                        ev = msg["event"]["event"]
                        seen[name].append(ev)
                        if ev["type"] == "hand_start":
                            ready.set()
                        if ev["type"] == "hand_end":
                            hands += 1

        ann_ready, ben_ready = threading.Event(), threading.Event()
        ann = threading.Thread(target=human, args=("ann", 12, ann_ready))
        ann.start()
        assert ann_ready.wait(10), "ann was never dealt in"
        ben = threading.Thread(target=human, args=("ben", 4, ben_ready))
        ben.start()
        ben.join(30)
        ann.join(30)
        stop.set()
        assert not ann.is_alive() and not ben.is_alive()

        first = next(e for e in seen["ann"] if e["type"] == "hand_start")
        assert first["players"].count("ann") == 1 and sum(p.startswith("[BOT]") for p in first["players"]) == 5
        # ben replaced a house bot at ann's table between hands...
        swaps = [e for e in seen["ann"] if e["type"] == "seat_change"]
        assert any(e["in"] == "ben" and e["out"].startswith("[BOT]") for e in swaps)
        both = [e for e in seen["ann"] if e["type"] == "hand_start" and "ben" in e["players"]]
        assert both and sum(p.startswith("[BOT]") for p in both[0]["players"]) == 4
        # ...and nobody sees anyone else's cards before showdown.
        holes = dict(zip(both[0]["players"], both[0]["holes"]))
        assert None not in holes["ann"] and holes["ben"] == [None, None]
        # ...and when ben left, a house bot took the seat back.
        assert any(e["out"] == "ben" and e["in"].startswith("[BOT]") for e in swaps)
        assert client.get("/api/leaderboard").json()["players"] == []
