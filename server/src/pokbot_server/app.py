"""HTTP + websocket API and the static intro site."""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pokbot_core.bundle import MAX_BUNDLE_BYTES, BundleError, unpack
from pokbot_core.engine.variants import VARIANTS, get_variant
from pydantic import BaseModel

from . import sandbox
from .chat import ChatError
from .config import settings
from .github import DeviceFlow, GitHubError
from .hub import Conn, Hub
from .store import Store, StoreError, check_name


class RegisterBody(BaseModel):
    name: str


class PollBody(BaseModel):
    session: str


def every(seconds: int) -> str:
    return f"{seconds // 60} minutes" if seconds >= 60 else f"{seconds} seconds"


SITE_PAGES = ("/", "/bots.md", "/llms.txt")


def sitemap_xml(site: str) -> str:
    urls = "".join(f"<url><loc>{site}{p}</loc></url>" for p in SITE_PAGES)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>\n'
    )


def create_app(start_scheduler: bool = True) -> FastAPI:
    store = Store(settings.data_dir)
    hub = Hub(store)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(hub.run_forever()) if start_scheduler else None
        yield
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="pokbot", lifespan=lifespan)
    if settings.site_url:
        # The static site reads schedule, presence and leaderboard; nothing else is cross-origin.
        origin = "/".join(settings.site_url.split("/")[:3])
        app.add_middleware(CORSMiddleware, allow_origins=[origin], allow_methods=["GET"])
    app.state.hub = hub
    app.state.store = store

    def owner(authorization: str | None) -> str:
        token = (authorization or "").removeprefix("Bearer ").strip()
        name = store.user_for(token)
        if not name:
            raise HTTPException(401, "missing or invalid API key; run `pokbot login`")
        return name

    device = DeviceFlow(settings.github_client_id) if settings.github_client_id else None

    @app.post("/api/auth/device")
    async def auth_device() -> dict:
        if device is None:
            raise HTTPException(501, "GitHub login is not configured on this server (POKBOT_GITHUB_CLIENT_ID)")
        try:
            return await device.start()
        except (GitHubError, OSError) as e:
            raise HTTPException(502, f"GitHub: {e}") from None

    @app.post("/api/auth/poll")
    async def auth_poll(body: PollBody) -> dict:
        if device is None:
            raise HTTPException(501, "GitHub login is not configured on this server")
        try:
            result = await device.poll(body.session)
        except (GitHubError, OSError) as e:
            raise HTTPException(400, str(e)) from None
        if result["status"] != "ok":
            return result
        try:
            token = store.login_github(result["login"], result["id"])
        except StoreError as e:
            raise HTTPException(409, str(e)) from None
        return {"status": "ok", "name": store.user_for(token), "token": token}

    @app.get("/api/whoami")
    def whoami(authorization: str | None = Header(None)) -> dict:
        return {"name": owner(authorization)}

    def base_url(request: Request) -> str:
        return (settings.public_url or str(request.base_url)).rstrip("/")

    def site_url(request: Request) -> str:
        return settings.site_url or base_url(request)

    def render(request: Request, filename: str, media_type: str) -> Response:
        """Serve a web/ file with __POKBOT_SITE__ / __POKBOT_BASE__ replaced by the public URLs."""
        path = settings.web_dir / filename
        if not path.is_file():
            raise HTTPException(404, "not found")
        text = path.read_text().replace("__POKBOT_SITE__", site_url(request))
        return Response(text.replace("__POKBOT_BASE__", base_url(request)), media_type=media_type)

    def page(request: Request, filename: str, media_type: str) -> Response:
        # One canonical copy for search engines: send visitors to the static site when there is one.
        if settings.site_url:
            target = settings.site_url + ("/" if filename == "index.html" else f"/{filename}")
            return RedirectResponse(target, status_code=301)
        return render(request, filename, media_type)

    @app.get("/install.sh", response_class=PlainTextResponse)
    def install_script(request: Request) -> Response:
        return render(request, "install.sh", "text/x-shellscript")

    # Templated pages: canonical URLs, Open Graph tags and agent docs need the public host.
    @app.get("/", include_in_schema=False)
    @app.get("/index.html", include_in_schema=False)
    def index(request: Request) -> Response:
        return page(request, "index.html", "text/html; charset=utf-8")

    @app.get("/llms.txt", include_in_schema=False)
    def llms_txt(request: Request) -> Response:
        return page(request, "llms.txt", "text/plain; charset=utf-8")

    @app.get("/bots.md", include_in_schema=False)
    def bots_md(request: Request) -> Response:
        return page(request, "bots.md", "text/markdown; charset=utf-8")

    @app.get("/robots.txt", include_in_schema=False)
    def robots(request: Request) -> Response:
        text = f"User-agent: *\nAllow: /\nDisallow: /api/\nSitemap: {site_url(request)}/sitemap.xml\n"
        return Response(text, media_type="text/plain; charset=utf-8")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap(request: Request) -> Response:
        if settings.site_url:
            return RedirectResponse(settings.site_url + "/sitemap.xml", status_code=301)
        return Response(sitemap_xml(site_url(request)), media_type="application/xml")

    @app.post("/api/register")
    def register(body: RegisterBody) -> dict:
        """Name-only sign-up for local development (POKBOT_DEV_LOGIN=1). Production uses GitHub."""
        if not settings.dev_login:
            raise HTTPException(403, "sign in with GitHub: run `pokbot login`")
        try:
            token = store.register(body.name)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None
        return {"name": body.name, "token": token}

    @app.post("/api/bots")
    async def upload_bot(request: Request, name: str, authorization: str | None = Header(None)) -> dict:
        user = owner(authorization)
        if not sandbox.available():
            raise HTTPException(503, "this server has no sandbox (docker); uploads are disabled. Play live with `pokbot play FILE`.")
        try:
            check_name(name)
        except StoreError as e:
            raise HTTPException(400, str(e)) from None
        if name != user and not name.startswith(user + "-"):
            raise HTTPException(400, f"bot names must be `{user}` or start with `{user}-`")
        if name in hub.lobby or name in hub.participants:
            raise HTTPException(409, f"{name} is connected live right now")
        data = await request.body()
        if len(data) > MAX_BUNDLE_BYTES:
            raise HTTPException(413, "bundle too large")
        staging = Path(tempfile.mkdtemp(dir=settings.data_dir))
        try:
            manifest = unpack(data, staging / "bot")
            record = store.save_bot(user, name, manifest, staging / "bot")
        except (BundleError, StoreError) as e:
            raise HTTPException(400, str(e)) from None
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {**record, "next_tournament": hub.next_at.isoformat()}

    @app.delete("/api/bots/{name}")
    def delete_bot(name: str, authorization: str | None = Header(None)) -> dict:
        try:
            store.delete_bot(owner(authorization), name)
        except StoreError as e:
            raise HTTPException(404, str(e)) from None
        return {"deleted": name}

    @app.get("/api/bots")
    def list_bots() -> dict:
        return {"bots": [{k: b[k] for k in ("name", "owner", "runtime", "uploaded")} for b in store.bots()]}

    @app.get("/api/schedule")
    def schedule() -> dict:
        msg = hub.lobby_message()
        return {
            "next_tournament": msg["starts_at"],
            "seconds": msg["seconds"],
            "interval_s": settings.interval_s,
            "entrants": msg["entrants"],
            "running": msg["running"],
            "online": hub.presence()["online"],
        }

    @app.get("/api/leaderboard")
    def leaderboard() -> dict:
        return {"players": store.leaderboard()}

    @app.get("/api/tournaments")
    def tournaments() -> dict:
        return {"tournaments": store.state["tournaments"]}

    @app.get("/api/games")
    def games() -> dict:
        return {"games": [{"key": v.key, "name": v.name, "available": v.available} for v in VARIANTS.values()]}

    async def serve(conn: Conn, on_open, on_close) -> None:
        """Shared websocket loop: action replies go to the match, chat goes to the room."""
        writer = asyncio.create_task(conn.writer())
        on_open()
        hub.connect(conn)
        try:
            while True:
                msg = json.loads(await conn.ws.receive_text())
                kind = msg.get("type") if isinstance(msg, dict) else None
                if kind == "action":
                    await conn.replies.put(msg)
                elif kind == "chat":
                    try:
                        hub.say(conn, msg.get("text", ""))
                    except ChatError as e:
                        conn.push_event({"type": "chat_error", "message": str(e)})
        except (WebSocketDisconnect, json.JSONDecodeError, RuntimeError):
            pass
        finally:
            conn.closed.set()
            on_close()
            hub.disconnect(conn)
            writer.cancel()

    @app.websocket("/ws/play")
    async def ws_play(ws: WebSocket, token: str | None = None) -> None:
        await ws.accept()
        user = store.user_for(token)
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), 10))
            if hello.get("type") != "hello":
                raise ValueError("first message must be hello")
            get_variant(hello.get("game", "holdem"))
            if not user:
                raise ValueError("sign in with GitHub first: run `pokbot login`")
            kind = "human" if hello.get("kind") == "human" else "bot"
            name = user  # one live seat per account; the name is never client-chosen
            check_name(name)
            conn = Conn(ws, name, kind, user=user)
            hub.join(conn)
        except (ValueError, StoreError, TimeoutError) as e:
            await ws.send_text(json.dumps({"type": "error", "message": str(e)}))
            await ws.close()
            return

        def opened() -> None:
            conn.push_event({"type": "info", "message": f"joined as [bold]{name}[/] ({kind}); tournaments start every {every(settings.interval_s)}"})
            conn.push_event(hub.lobby_message())

        await serve(conn, opened, lambda: hub.leave(conn))

    @app.websocket("/ws/watch")
    async def ws_watch(ws: WebSocket, token: str | None = None) -> None:
        """Spectate the running tournament and chat (chatting needs a signed-in token)."""
        await ws.accept()
        user = store.user_for(token)
        conn = Conn(ws, user or f"spectator-{id(ws)}", "spectator", user=user)

        def opened() -> None:
            hub.spectators.add(conn)
            conn.push_event(hub.lobby_message())

        await serve(conn, opened, lambda: hub.spectators.discard(conn))

    @app.get("/api/online")
    def online() -> dict:
        p = hub.presence()
        return {k: p[k] for k in ("online", "playing", "spectators", "names")}

    if settings.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=settings.web_dir, html=True), name="web")
    return app
