"""FastAPI 기반 버블 챗봇 (SSE 스트리밍)."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncGenerator, AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from txt2sql import SessionContext, Txt2SqlEngine
from txt2sql.config import Settings, load_settings
from txt2sql.data import create_data_router
from txt2sql.map import create_map_router, start_cleanup_scheduler
from txt2sql.observability import mask_mapping
from txt2sql.security.auth import AuthContext, auth_dependency_factory
from txt2sql.security import ensure_user
from txt2sql.security.deploy import SecurityGateMiddleware
from txt2sql.security.errors import PublicError, UserInputError, correlation_id
from txt2sql.security.headers import SecurityHeadersMiddleware
from txt2sql.security.http import (
    http_from_public,
    public_error_handler,
    redact_sse_error,
)
from txt2sql.security.sessions import SessionStore, is_valid_session_id

STATIC_DIR = Path(__file__).resolve().parent / "static"
DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
_SENTINEL = object()
_CATALOG_JSON = DOCS_DIR / "kordb_catalog.json"
_CATALOG_MD = DOCS_DIR / "kordb_필드카탈로그.md"
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    session_id: str | None = None
    include_map: bool = False


def create_app(*, settings: Settings | None = None) -> FastAPI:
    engine_holder: dict[str, Any] = {"engine": None, "settings": settings}
    settings_holder: dict[str, Settings | None] = {"settings": settings}

    def get_settings() -> Settings:
        cached = settings_holder.get("settings")
        if cached is not None:
            return cached
        loaded = load_settings()
        settings_holder["settings"] = loaded
        return loaded

    sessions = SessionStore(
        ttl_seconds=get_settings().session_ttl_seconds if settings else 3600,
        max_sessions=get_settings().session_max_count if settings else 200,
        factory=SessionContext,
    )
    # Rebind after settings known
    def _rebind_sessions(cfg: Settings) -> None:
        sessions.ttl_seconds = max(60, int(cfg.session_ttl_seconds))
        sessions.max_sessions = max(1, int(cfg.session_max_count))

    ask_slots = threading.BoundedSemaphore(
        value=max(1, int(settings.max_concurrent_asks) if settings else 4)
    )
    worker_pool = ThreadPoolExecutor(
        max_workers=max(1, int(settings.max_concurrent_asks) if settings else 4),
        thread_name_prefix="txt2sql-ask",
    )
    auth_dep = auth_dependency_factory(get_settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        cfg = get_settings()
        _rebind_sessions(cfg)
        ask_slots._initial_value = max(1, int(cfg.max_concurrent_asks))  # type: ignore[attr-defined]
        engine_holder["engine"] = Txt2SqlEngine.from_settings(cfg)
        try:
            start_cleanup_scheduler(cfg)
        except Exception:
            pass
        try:
            yield
        finally:
            worker_pool.shutdown(wait=False, cancel_futures=True)
            engine = engine_holder.get("engine")
            if engine is not None:
                engine.close()

    app = FastAPI(title="txt2sql Chat", version="0.3.3", lifespan=lifespan)
    app.add_middleware(SecurityHeadersMiddleware, get_settings=get_settings)
    app.add_middleware(SecurityGateMiddleware, get_settings=get_settings)
    app.add_exception_handler(PublicError, public_error_handler)

    def get_engine() -> Txt2SqlEngine:
        engine = engine_holder.get("engine")
        if engine is None:
            raise HTTPException(
                status_code=503, detail="엔진이 아직 준비되지 않았습니다."
            )
        return engine

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/chat")
    def chat_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/map")
    def map_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "map.html")

    @app.get("/data")
    def data_home() -> FileResponse:
        return FileResponse(STATIC_DIR / "data.html")

    @app.get("/data/upload")
    def data_upload() -> FileResponse:
        return FileResponse(STATIC_DIR / "data_upload.html")

    @app.get("/data/metadata")
    def data_metadata() -> FileResponse:
        return FileResponse(STATIC_DIR / "data_metadata.html")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/download/kordb-catalog.json")
    def download_kordb_catalog_json(
        auth: AuthContext = Depends(auth_dep),
    ) -> FileResponse:
        ensure_user(auth)
        if not _CATALOG_JSON.is_file():
            raise HTTPException(
                status_code=404, detail="KorDB 카탈로그 JSON이 없습니다."
            )
        return FileResponse(
            _CATALOG_JSON,
            media_type="application/json; charset=utf-8",
            filename="kordb_catalog.json",
        )

    @app.get("/download/kordb-catalog.md")
    def download_kordb_catalog_md(
        auth: AuthContext = Depends(auth_dep),
    ) -> FileResponse:
        ensure_user(auth)
        if not _CATALOG_MD.is_file():
            raise HTTPException(
                status_code=404, detail="KorDB 카탈로그 Markdown이 없습니다."
            )
        return FileResponse(
            _CATALOG_MD,
            media_type="text/markdown; charset=utf-8",
            filename="kordb_field_catalog.md",
        )

    @app.post("/api/session")
    def new_session(auth: AuthContext = Depends(auth_dep)) -> dict[str, str]:
        ensure_user(auth)
        sid, _ = sessions.create(owner=auth.subject)
        return {"session_id": sid}

    @app.post("/api/chat")
    async def chat(
        body: ChatRequest,
        request: Request,
        auth: AuthContext = Depends(auth_dep),
    ) -> StreamingResponse:
        ensure_user(auth)
        cfg = get_settings()
        corr = getattr(request.state, "correlation_id", None) or correlation_id()
        question = body.question.strip()
        if not question:
            raise http_from_public(UserInputError("질문이 비어 있습니다."))
        if len(question) > int(cfg.max_question_chars):
            raise http_from_public(
                UserInputError("질문 길이가 제한을 초과했습니다.", code="question_too_long")
            )

        engine = get_engine()
        if body.session_id and is_valid_session_id(body.session_id):
            session = sessions.get(body.session_id, owner=auth.subject)
            if session is not None:
                session_id = body.session_id
            else:
                # 서버 재시작·TTL 만료 후 localStorage에 남은 id → 새 세션으로 이어간다.
                session_id, session = sessions.create(owner=auth.subject)
        else:
            session_id, session = sessions.create(owner=auth.subject)

        max_events = 2000
        event_q: Queue[Any] = Queue(maxsize=max_events)

        def on_progress(
            stage: str, message: str, detail: dict[str, Any] | None
        ) -> None:
            try:
                event_q.put_nowait(
                    {
                        "type": "progress",
                        "stage": stage,
                        "message": message,
                        "detail": mask_mapping(detail or {}),
                    }
                )
            except Full:
                pass

        def on_token(text: str) -> None:
            if text:
                try:
                    event_q.put_nowait({"type": "token", "text": text})
                except Full:
                    pass

        acquired = ask_slots.acquire(blocking=False)
        if not acquired:
            raise HTTPException(
                status_code=429,
                detail={
                    "detail": "동시 요청 한도를 초과했습니다.",
                    "code": "too_many_requests",
                    "correlation_id": corr,
                },
            )

        def worker() -> None:
            try:
                result = engine.ask(
                    question,
                    session=session,
                    session_id=session_id,
                    on_progress=on_progress,
                    on_token=on_token,
                    include_map=body.include_map,
                )
                payload = result.to_dict()
                rows = payload.get("rows") or []
                if isinstance(rows, list) and len(rows) > 20:
                    payload["rows"] = rows[:20]
                    payload["rows_truncated"] = len(rows) - 20
                event_q.put(
                    {
                        "type": "done",
                        "session_id": session_id,
                        "result": payload,
                    }
                )
            except Exception as exc:
                logger.exception("chat worker failed corr=%s", corr)
                event_q.put(redact_sse_error(exc, corr_id=corr) | {"session_id": session_id})
            finally:
                ask_slots.release()
                event_q.put(_SENTINEL)

        worker_pool.submit(worker)

        async def event_stream() -> AsyncIterator[bytes]:
            yield _sse({"type": "ready", "session_id": session_id})
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.to_thread(event_q.get, True, 0.25)
                except Empty:
                    yield b": ping\n\n"
                    continue
                if item is _SENTINEL:
                    break
                yield _sse(item)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "X-Correlation-ID": corr,
            },
        )

    app.include_router(
        create_map_router(
            get_settings,
            get_ollama=lambda: get_engine().ollama_client,
            get_auth=auth_dep,
        )
    )
    app.include_router(
        create_data_router(get_settings, get_auth=auth_dep)
    )

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    # expose for tests
    app.state.session_store = sessions  # type: ignore[attr-defined]
    app.state.get_settings = get_settings  # type: ignore[attr-defined]
    return app


def _sse(payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"data: {data}\n\n".encode("utf-8")


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "txt2sql.webapp.app:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
