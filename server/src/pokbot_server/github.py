"""GitHub OAuth device flow, proxied by the server so the CLI only ever holds a pokbot key.

    CLI  -> POST /api/auth/device   -> GitHub /login/device/code  (user_code shown to the user)
    CLI  -> POST /api/auth/poll     -> GitHub /login/oauth/access_token -> GitHub /user
         <- pokbot API key for that GitHub login
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass

DEVICE_URL = "https://github.com/login/device/code"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"


class GitHubError(RuntimeError):
    pass


@dataclass
class Pending:
    device_code: str
    interval: int
    expires_at: float


def _post_form(url: str, fields: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=urllib.parse.urlencode(fields).encode(),
        headers={"Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def _get_user(access_token: str) -> dict:
    req = urllib.request.Request(
        USER_URL,
        headers={"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


class DeviceFlow:
    def __init__(self, client_id: str) -> None:
        self.client_id = client_id
        self.pending: dict[str, Pending] = {}

    async def start(self) -> dict:
        data = await asyncio.to_thread(_post_form, DEVICE_URL, {"client_id": self.client_id, "scope": "read:user"})
        if "device_code" not in data:
            raise GitHubError(data.get("error_description") or data.get("error") or "GitHub refused the request")
        self._expire()
        session = secrets.token_urlsafe(16)
        self.pending[session] = Pending(data["device_code"], int(data.get("interval", 5)), time.time() + int(data["expires_in"]))
        return {
            "session": session,
            "user_code": data["user_code"],
            "verification_uri": data["verification_uri"],
            "interval": int(data.get("interval", 5)),
            "expires_in": int(data["expires_in"]),
        }

    async def poll(self, session: str) -> dict:
        """Returns {"status": "pending", "interval": n} or {"status": "ok", "login": ..., "id": ...}."""
        self._expire()
        p = self.pending.get(session)
        if p is None:
            raise GitHubError("login session expired; run `pokbot login` again")
        data = await asyncio.to_thread(
            _post_form,
            TOKEN_URL,
            {
                "client_id": self.client_id,
                "device_code": p.device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        error = data.get("error")
        if error == "authorization_pending":
            return {"status": "pending", "interval": p.interval}
        if error == "slow_down":
            p.interval = int(data.get("interval", p.interval + 5))
            return {"status": "pending", "interval": p.interval}
        if error:
            self.pending.pop(session, None)
            raise GitHubError(data.get("error_description") or error)
        self.pending.pop(session, None)
        user = await asyncio.to_thread(_get_user, data["access_token"])
        return {"status": "ok", "login": user["login"], "id": user["id"]}

    def _expire(self) -> None:
        now = time.time()
        for k in [k for k, p in self.pending.items() if p.expires_at < now]:
            del self.pending[k]
