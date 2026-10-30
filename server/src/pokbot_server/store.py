"""Tiny JSON-file store: users, uploaded bots, leaderboard, recent tournaments.
Good enough for one server process; swap for SQLite/Postgres when it isn't."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import threading
from datetime import UTC, datetime
from pathlib import Path

from pokbot_core.players import HOUSE_PREFIX

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{2,39}$")


class StoreError(ValueError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def check_name(name: str) -> str:
    if name.startswith(HOUSE_PREFIX.strip()) or not NAME_RE.match(name):
        raise StoreError("names are 2-39 chars of letters, digits, _ or -")
    return name


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = root / "state.json"
        self._lock = threading.Lock()
        self.state = {"users": {}, "bots": {}, "leaderboard": {}, "tournaments": []}
        if self.path.exists():
            self.state.update(json.loads(self.path.read_text()))

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        tmp.replace(self.path)

    # -- users -----------------------------------------------------------------------------

    def register(self, name: str) -> str:
        check_name(name)
        with self._lock:
            if any(u["name"].lower() == name.lower() for u in self.state["users"].values()):
                raise StoreError(f"name {name!r} is taken")
            token = "pk_" + secrets.token_urlsafe(24)
            self.state["users"][_hash(token)] = {"name": name, "created": datetime.now(UTC).isoformat()}
            self._save()
        return token

    def login_github(self, login: str, github_id: int) -> str:
        """Issue a new API key for a GitHub account; the pokbot name is the GitHub login."""
        check_name(login)
        with self._lock:
            users = self.state["users"].values()
            mine = next((u for u in users if u.get("github_id") == github_id), None)
            if mine is None and any(u["name"].lower() == login.lower() for u in users):
                raise StoreError(f"name {login!r} is already registered without GitHub")
            name = mine["name"] if mine else login
            token = "pk_" + secrets.token_urlsafe(24)
            self.state["users"][_hash(token)] = {
                "name": name,
                "github_id": github_id,
                "created": datetime.now(UTC).isoformat(),
            }
            self._save()
        return token

    def user_for(self, token: str | None) -> str | None:
        if not token:
            return None
        user = self.state["users"].get(_hash(token))
        return user["name"] if user else None

    # -- bots ------------------------------------------------------------------------------

    def bot_dir(self, name: str) -> Path:
        return self.root / "bots" / name

    def save_bot(self, owner: str, name: str, manifest: dict, staged: Path) -> dict:
        check_name(name)
        with self._lock:
            existing = self.state["bots"].get(name)
            if existing and existing["owner"] != owner:
                raise StoreError(f"bot name {name!r} belongs to someone else")
            dest = self.bot_dir(name)
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            staged.rename(dest)
            record = {
                "name": name,
                "owner": owner,
                "runtime": manifest["runtime"],
                "entrypoint": manifest["entrypoint"],
                "uploaded": datetime.now(UTC).isoformat(),
            }
            self.state["bots"][name] = record
            self._save()
        return record

    def delete_bot(self, owner: str, name: str) -> None:
        with self._lock:
            record = self.state["bots"].get(name)
            if not record or record["owner"] != owner:
                raise StoreError("no such bot")
            del self.state["bots"][name]
            shutil.rmtree(self.bot_dir(name), ignore_errors=True)
            self._save()

    def bots(self) -> list[dict]:
        return list(self.state["bots"].values())

    def bots_by_name(self) -> dict[str, dict]:
        return dict(self.state["bots"])

    # -- results ---------------------------------------------------------------------------

    def record_tournament(self, result: dict) -> None:
        with self._lock:
            board = self.state["leaderboard"]
            seen: set[str] = set()
            for table in result["tables"]:
                for s in table["standings"]:
                    if s["kind"] == "house":
                        continue
                    row = board.setdefault(s["name"], {"name": s["name"], "points": 0, "titles": 0, "tournaments": 0, "chips": 0, "hands": 0})
                    row.setdefault("points", 0)
                    row["chips"] += s["chips"]
                    row["hands"] += s["hands"]
                    if s["name"] not in seen:
                        row["tournaments"] += 1
                        seen.add(s["name"])
            if result.get("winner") in board:
                board[result["winner"]]["titles"] += 1
            for name, pts in result.get("points", {}).items():
                if name in board:
                    board[name]["points"] += pts
            summary = {k: result[k] for k in ("tournament_id", "winner", "rounds")}
            summary["finished"] = datetime.now(UTC).isoformat()
            summary["entrants"] = len(seen)
            summary["points"] = result.get("points", {})
            keep = ("place", "name", "sets_won", "chips", "hands", "advances")
            summary["tables"] = [
                {
                    "table": t["table"],
                    "round": t["round"],
                    "final": t["final"],
                    "standings": [{k: s.get(k) for k in keep} for s in t["standings"]],
                }
                for t in result["tables"]
            ]
            self.state["tournaments"] = ([summary] + self.state["tournaments"])[:100]
            self._save()

    def leaderboard(self) -> list[dict]:
        rows = list(self.state["leaderboard"].values())
        return sorted(rows, key=lambda r: (-r.get("points", 0), -r["titles"], -r["chips"]))
