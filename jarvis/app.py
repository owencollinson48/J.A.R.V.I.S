import asyncio
import contextlib
import hmac
import json
import secrets
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .assistant import Assistant
from .computer import Computer
from .config import Settings
from .connection import repair_connection, use_sol
from .desktop import EmergencyHotkey
from .execution import RunContext
from .provider import Provider, ProviderError
from .scheduler import Scheduler
from .speech import Speech, SpeechError
from .store import Store, now
from .tools import ToolRunner


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=24000)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1,max_length=4000)
    rate: float = Field(default=1,ge=.75,le=1.25,strict=True)


class ApprovalRequest(BaseModel):
    approved: bool


def create_app(data_dir=None, provider_factory=Provider):
    settings = Settings(data_dir)
    store = Store(settings.data_dir / "jarvis.db")
    computer = Computer(settings)
    provider = provider_factory(settings)
    speech = Speech(settings)
    assistant = Assistant(store, settings, ToolRunner(store, computer), provider)
    services = assistant.tools
    emergency = EmergencyHotkey(assistant.cancel)
    scheduler = Scheduler(store, settings)
    session = secrets.token_urlsafe(32)
    static = Path(__file__).parent / "static"

    def retire_pending_reviews():
        if settings.values['permissions_mode'] != 'autonomous':
            return
        # Do not replay old effects merely because the execution mode changed.
        # Clear the obsolete approval barrier so the next user request can run.
        pending = store.rows("SELECT DISTINCT conversation_id FROM approvals WHERE status IN ('pending','deferred')")
        for item in pending:
            assistant.close_unanswered(item['conversation_id'], 'Execution mode changed. Previous queued actions were not run; continue from a fresh user request.')
            store.execute("UPDATE workflows SET status='cancelled',detail=?,updated_at=? WHERE conversation_id=? AND status='awaiting_confirmation'",
                          ('Old queued actions were retired after switching to automatic execution.', now(), item['conversation_id']))

    @asynccontextmanager
    async def lifespan(app):
        store.recover()
        retire_pending_reviews()
        emergency.start()
        discovery = asyncio.create_task(asyncio.to_thread(services.applications.refresh)) if services.applications.adapter.supported else None
        timer = asyncio.create_task(scheduler.loop())
        yield
        assistant.cancel()
        emergency.close()
        timer.cancel()
        try:
            await timer
        except asyncio.CancelledError:
            pass
        if assistant.jobs:
            await asyncio.gather(*assistant.jobs, return_exceptions=True)
        if discovery:
            with contextlib.suppress(Exception): await discovery
        await asyncio.to_thread(services.browser.close)
        store.db.close()

    app = FastAPI(title="JARVIS", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store, app.state.settings = store, settings
    app.state.assistant, app.state.scheduler = assistant, scheduler
    app.state.tools = services

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
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
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
        return {"ok": True, "settings": settings.public(), "drives": computer.drives(), "active": list(assistant.active),
                'applications':services.applications.count(), 'windows_control':services.applications.adapter.supported,
                'emergency_hotkey':emergency.registered}

    @app.get("/api/settings")
    async def get_settings():
        return settings.public()

    @app.patch("/api/settings")
    async def update_settings(body: dict):
        if assistant.active:
            raise ValueError("Wait for the current action to finish before changing settings")
        result = settings.update(body)
        retire_pending_reviews()
        return result

    @app.post('/api/speech')
    async def synthesize_speech(body: SpeechRequest, request: Request):
        task = asyncio.create_task(speech.synthesize(body.text,body.rate))
        try:
            while not task.done():
                await asyncio.wait({task},timeout=.1)
                if await request.is_disconnected():
                    task.cancel()
                    return Response(status_code=499)
            return Response(task.result(),media_type='audio/mpeg',headers={'Cache-Control':'no-store'})
        except SpeechError as exc:
            raise HTTPException(502,str(exc)) from exc
        finally:
            if not task.done(): task.cancel()
            with contextlib.suppress(asyncio.CancelledError,SpeechError): await task

    @app.post("/api/provider/check")
    async def check_provider():
        try:
            return await provider.check()
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc

    @app.post('/api/provider/repair')
    async def repair_provider():
        try:
            return await repair_connection(settings,provider_factory,lambda:bool(assistant.active))
        except ProviderError as exc:
            raise ValueError(str(exc)) from exc

    @app.post('/api/provider/use-sol')
    async def select_sol():
        try:
            return await use_sol(settings, provider_factory, lambda:bool(assistant.active))
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
        workflow = store.row('SELECT * FROM workflows WHERE conversation_id=? ORDER BY created_at DESC LIMIT 1',(conversation_id,))
        return {"messages": store.history(conversation_id), "approvals": store.pending(conversation_id),
                "visual":services.visuals.latest(conversation_id),
                "active": conversation_id in assistant.active,
                'workflow':workflow, 'actions':store.rows('SELECT id,workflow_id,name,status,risk,created_at FROM action_log WHERE workflow_id=? ORDER BY created_at',(workflow['id'],)) if workflow else []}

    @app.delete('/api/conversations/{conversation_id}/visuals/{visual_id}')
    async def dismiss_visual(conversation_id: str, visual_id: str):
        existing(conversation_id)
        return services.visuals.dismiss(conversation_id,visual_id)

    @app.post('/api/cancel')
    async def cancel_all():
        return assistant.cancel()

    @app.post('/api/conversations/{conversation_id}/cancel')
    async def cancel_conversation(conversation_id: str):
        existing(conversation_id)
        return assistant.cancel(conversation_id)

    @app.get('/api/activity')
    async def activity():
        workflows = store.rows('SELECT * FROM workflows ORDER BY created_at DESC LIMIT 30')
        actions = store.rows('SELECT * FROM action_log ORDER BY created_at DESC LIMIT 120')
        for workflow in workflows: workflow['plan'] = json.loads(workflow['plan'])
        for action in actions:
            action['arguments'] = json.loads(action['arguments'])
            action['result'] = json.loads(action['result']) if action['result'] else None
            action['undo_available'] = bool(store.row("SELECT action_id FROM file_changes WHERE action_id=? AND status='available'",(action['id'],)))
        return {'workflows':workflows,'actions':actions,'active':list(assistant.active),'emergency_hotkey':emergency.registered}

    @app.get('/api/applications')
    async def applications(q: str = '', refresh: bool = False):
        report = await asyncio.to_thread(services.applications.refresh, refresh)
        matches = await asyncio.to_thread(services.applications.search, q)
        return {**report, 'applications':matches, 'supported':services.applications.adapter.supported,
                'aliases':store.rows('SELECT * FROM application_aliases ORDER BY alias')}

    @app.post('/api/applications/alias')
    async def application_alias(body: dict):
        if set(body) != {'alias','application_id'} or not all(isinstance(v,str) for v in body.values()): raise ValueError('Specify an alias and application id')
        return services.applications.alias(**body)

    @app.post('/api/applications/{application_id}/test')
    async def test_application(application_id: str):
        if assistant.active: raise ValueError('Wait for the current task to finish, or cancel it first')
        app_info = services.applications.resolve(application_id)
        if app_info.get('ambiguous'): raise ValueError('Select one specific indexed app')
        conversation = store.conversation('Application launch check: ' + app_info['name'])
        context = RunContext(store,conversation['id'],'Launch and verify ' + app_info['name'],seconds=settings.values['workflow_timeout'])
        args = {'name':app_info['id']}
        preview = services.preflight('launch_application',args)
        decision = services.policy.decide('launch_application',args,preview,context)
        if decision.confirm:
            context.finish('blocked','Use the Assistant to review this launch')
            raise ValueError('This launch requires review. Ask in Assistant so the exact action can be confirmed.')
        assistant.active.add(conversation['id']); assistant.contexts[conversation['id']] = context
        action_id = context.log('launch_application',args,decision)
        try:
            result = await asyncio.to_thread(services.run,'launch_application',args,preview,conversation['id'],context,action_id)
            context.logged_result(action_id,result)
            context.finish('completed' if result.get('verified') else 'failed')
            return result
        except Exception as exc:
            context.logged_result(action_id,{'error':str(exc)},'failed'); context.finish('failed',str(exc)); raise ValueError(str(exc)) from exc
        finally:
            assistant.active.discard(conversation['id']); assistant.contexts.pop(conversation['id'],None)

    @app.get('/api/screenshots/{image_id}')
    async def screenshot(image_id: str):
        return FileResponse(services.images.path(image_id),media_type='image/jpeg',headers={'Cache-Control':'no-store'})

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
