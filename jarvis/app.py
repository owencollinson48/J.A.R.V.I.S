import asyncio
import hmac
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .assistant import Assistant
from .computer import Computer
from .config import Settings
from .provider import Provider, ProviderError
from .scheduler import Scheduler
from .store import Store
from .tools import ToolRunner


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=24000)


class ApprovalRequest(BaseModel):
    approved: bool


def create_app(data_dir=None, provider_factory=Provider):
    settings = Settings(data_dir)
    store = Store(settings.data_dir / "jarvis.db")
    computer = Computer(settings)
    provider = provider_factory(settings)
    assistant = Assistant(store, settings, ToolRunner(store, computer), provider)
    scheduler = Scheduler(store, settings)
    session = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        timer = asyncio.create_task(scheduler.loop())
        yield
        timer.cancel()
        try:
            await timer
        except asyncio.CancelledError:
            pass
        if assistant.jobs:
            await asyncio.gather(*assistant.jobs, return_exceptions=True)
        store.db.close()

    app = FastAPI(title="JARVIS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.settings = store, settings
    app.state.assistant, app.state.scheduler = assistant, scheduler

    @app.middleware("http")
    async def local_access(request, call_next):
        host = request.url.hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            return JSONResponse({"detail": "JARVIS only accepts local requests"}, 403)
        origin = request.headers.get("origin")
        if origin and origin != f"{request.url.scheme}://{request.url.netloc}":
            return JSONResponse({"detail": "Cross-origin access is disabled"}, 403)
        if request.url.path.startswith("/api/"):
            token = request.cookies.get("jarvis_session", "")
            if not hmac.compare_digest(token, session):
                return JSONResponse({"detail": "Open the JARVIS dashboard to start a local session"}, 401)
            if request.method in {"POST", "PATCH", "DELETE"} and request.headers.get("x-jarvis-client") != "dashboard":
                return JSONResponse({"detail": "Missing local client header"}, 403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 400)

    @app.exception_handler(sqlite3.IntegrityError)
    async def integrity(request, exc):
        return JSONResponse({"detail": "This entry refers to a goal that does not exist"}, 400)

    @app.exception_handler(OSError)
    async def filesystem_error(request, exc):
        return JSONResponse({"detail": "File operation failed: " + str(exc)}, 400)

    @app.get("/")
    async def index():
        response = FileResponse(static / "index.html")
        response.set_cookie("jarvis_session", session, httponly=True, samesite="strict")
        return response

    @app.get("/api/status")
    async def status():
        return {"ok": True, "settings": settings.public(), "drives": computer.drives(), "active": list(assistant.active)}

    @app.get("/api/settings")
    async def get_settings():
        return settings.public()

    @app.patch("/api/settings")
    async def update_settings(body: dict):
        if assistant.active:
            raise ValueError("Wait for the current action to finish before changing settings")
        return settings.update(body)

    @app.post("/api/provider/check")
    async def check_provider():
        try:
            return await provider.check()
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc

    @app.get("/api/conversations")
    async def conversations():
        return store.rows("SELECT * FROM conversations ORDER BY updated_at DESC")

    @app.post("/api/conversations")
    async def new_conversation():
        return store.conversation()

    def existing(conversation_id):
        if not store.row("SELECT id FROM conversations WHERE id=?", (conversation_id,)):
            raise HTTPException(404, "Conversation not found")

    @app.get("/api/conversations/{conversation_id}")
    async def history(conversation_id: str):
        existing(conversation_id)
        return {"messages": store.history(conversation_id), "approvals": store.pending(conversation_id),
                "active": conversation_id in assistant.active}

    @app.delete("/api/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str):
        existing(conversation_id)
        if conversation_id in assistant.active:
            raise ValueError("Wait for JARVIS to finish this conversation")
        store.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        return {"ok": True}

    @app.post("/api/conversations/{conversation_id}/chat")
    async def chat(conversation_id: str, body: ChatRequest):
        existing(conversation_id)
        if not body.content.strip():
            raise ValueError("Write a message first")
        try:
            provider.headers()
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc
        if store.pending(conversation_id):
            raise ValueError("Approve or decline the pending actions first")
        stream = assistant.start(conversation_id, lambda emit: assistant.chat(conversation_id, body.content.strip(), emit))
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.post("/api/approvals/{approval_id}")
    async def resolve(approval_id: str, body: ApprovalRequest):
        item = store.row("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if not item:
            raise HTTPException(404, "Action not found")
        if item["status"] != "pending":
            raise ValueError("This action has already been resolved")
        stream = assistant.start(item["conversation_id"], lambda emit: assistant.resolve(approval_id, body.approved, emit))
        return StreamingResponse(stream, media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    for kind in ("tasks", "goals", "reminders", "memories"):
        def routes(kind):
            @app.get(f"/api/{kind}", name=f"list_{kind}")
            async def list_items():
                return store.rows(f"SELECT * FROM {kind} ORDER BY created_at DESC")

            @app.post(f"/api/{kind}", name=f"create_{kind}")
            async def create_item(body: dict):
                return store.create_item(kind, body)

            @app.patch(f"/api/{kind}/{{item_id}}", name=f"update_{kind}")
            async def update_item(item_id: str, body: dict):
                return store.update_item(kind, item_id, body)

            @app.delete(f"/api/{kind}/{{item_id}}", name=f"delete_{kind}")
            async def delete_item(item_id: str):
                store.execute(f"DELETE FROM {kind} WHERE id=?", (item_id,))
                return {"ok": True}
        routes(kind)

    @app.get("/api/memory/search")
    async def search_memory(q: str = ""):
        return store.search_memory(q)

    @app.get("/api/files")
    async def files(path: str | None = None, q: str | None = None):
        try:
            return await asyncio.to_thread(computer.search_files, q, path) if q else await asyncio.to_thread(computer.list_files, path)
        except (OSError, ValueError) as exc:
            raise ValueError(str(exc)) from exc

    @app.get("/api/files/read")
    async def read_file(path: str):
        return await asyncio.to_thread(computer.read_file, path)

    app.mount("/static", StaticFiles(directory=static), name="static")
    return app
