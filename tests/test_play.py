from pathlib import Path

import pytest
from fastapi.testclient import TestClient
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

    monkeypatch.setattr(server_app, "settings", Settings(data_dir=tmp_path, sandbox="unsafe-local"))
    monkeypatch.setattr("pokbot_server.sandbox.settings", Settings(data_dir=tmp_path, sandbox="unsafe-local"))
    from pokbot_core.bundle import pack

    with TestClient(server_app.create_app(start_scheduler=False)) as client:
        r = client.post("/api/register", json={"name": "alice"})
        assert r.status_code == 200
        token = r.json()["token"]
        assert client.post("/api/register", json={"name": "alice"}).status_code == 400
        assert client.post("/api/register", json={"name": "[BOT] x"}).status_code == 400

        data, _ = pack(str(EXAMPLES / "caller.py"))
        r = client.post("/api/bots?name=callme", content=data, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200, r.text
        assert client.post("/api/bots?name=x2", content=data).status_code == 401
        assert [b["name"] for b in client.get("/api/bots").json()["bots"]] == ["callme"]
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

    assert clean("  hi\x00 there‮ ") == "hi there"
    with pytest.raises(ChatError):
        clean("   ")
    with pytest.raises(ChatError):
        clean("x" * 281)
    rl = RateLimiter(count=2, window_s=10)
    assert rl.allow(0) and rl.allow(1) and not rl.allow(2) and rl.allow(11)


def test_chat_and_presence_over_websockets(tmp_path, monkeypatch):
    import pokbot_server.app as server_app
    from pokbot_server.config import Settings

    monkeypatch.setattr(server_app, "settings", Settings(data_dir=tmp_path))

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
