import asyncio
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import engine, Base, get_db, redis_client, AsyncSessionLocal, measure_db_latency_ms, measure_redis_latency_ms
from app.logger import log_event, mask_sensitive_text
from app.models import TikTokAccount, TargetVideo, CommentTemplate, QueueTask
from app.schema_bootstrap import ensure_schema
from app.security import authenticate_websocket
from app.routers import auth, accounts, sources_videos, comments_tasks, system


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await ensure_schema()
        await log_event("backend", "INFO", "startup", "TikTok Control Center v3.0 API запущен")
    except Exception as exc:
        await log_event("backend", "ERROR", "startup_db_err", str(exc), exc)
    yield


app = FastAPI(
    title="TikTok Control Center v3.0 API",
    version="3.0.0",
    description="Production-grade управление аккаунтами, источниками, очередями и мониторингом TikTok v3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID"],
)


@app.middleware("http")
async def request_context_and_limits_middleware(request: Request, call_next):
    # Ограничение размера тела запроса (до 2 МБ)
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 2 * 1024 * 1024:
        return JSONResponse(
            status_code=413,
            content={"ok": False, "detail": "Размер запроса превышает допустимый лимит (2 МБ)"},
        )

    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    request.state.request_id = req_id
    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = round((time.monotonic() - start) * 1000, 2)
    response.headers["X-Request-ID"] = req_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)
    return response


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    req_id = getattr(request.state, "request_id", None)
    await log_event("backend", "WARNING", "db_integrity_error", str(exc), request_id=req_id)
    return JSONResponse(
        status_code=409,
        content={
            "ok": False,
            "detail": "Конфликт уникальности или целостности данных в БД (запись уже существует)",
            "request_id": req_id,
        },
    )


@app.exception_handler(SQLAlchemyError)
async def sqlalchemy_error_handler(request: Request, exc: SQLAlchemyError):
    req_id = getattr(request.state, "request_id", None)
    await log_event("backend", "ERROR", "db_unavailable", str(exc), exc, request_id=req_id)
    return JSONResponse(
        status_code=503,
        content={
            "ok": False,
            "detail": "База данных временно недоступна. Повторите запрос позже.",
            "request_id": req_id,
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    req_id = getattr(request.state, "request_id", None)
    parts = []
    for err in exc.errors():
        loc = [str(item) for item in err.get("loc", []) if item != "body"]
        msg = str(err.get("msg") or "некорректное значение")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return JSONResponse(
        status_code=422,
        content={
            "ok": False,
            "detail": mask_sensitive_text("; ".join(parts)[:400]),
            "request_id": req_id,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", None)
    await log_event("backend", "ERROR", "unhandled_exception", str(exc), exc, request_id=req_id)
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "detail": "Внутренняя ошибка сервера. Информация зафиксирована в системном журнале.",
            "request_id": req_id,
        },
    )


@app.get("/api/health/live", tags=["health"])
async def liveness_check():
    return {"status": "alive", "version": "3.0.0"}


@app.get("/api/health", tags=["health"])
@app.get("/api/health/ready", tags=["health"])
async def readiness_check():
    db_ms = await measure_db_latency_ms()
    redis_ms = await measure_redis_latency_ms()
    worker_hb = None
    tg_hb = None
    try:
        worker_hb = await redis_client.get("worker:heartbeat")
        tg_hb = await redis_client.get("telegram:heartbeat")
    except Exception:
        pass

    ready = db_ms >= 0 and redis_ms >= 0
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "degraded",
            "db_latency_ms": db_ms,
            "redis_latency_ms": redis_ms,
            "worker": "online" if worker_hb else "offline",
            "telegram": "online" if tg_hb else "offline",
        },
    )


app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(sources_videos.router)
app.include_router(comments_tasks.router)
app.include_router(system.router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    async with AsyncSessionLocal() as db:
        user = await authenticate_websocket(websocket, db)
    if not user:
        await websocket.close(code=1008, reason="Требуется авторизация для WebSocket")
        return

    await websocket.accept()
    try:
        while True:
            worker_hb = None
            tg_hb = None
            paused = False
            try:
                worker_hb = await redis_client.get("worker:heartbeat")
                tg_hb = await redis_client.get("telegram:heartbeat")
                paused = await redis_client.get("queue:paused") == "1"
            except Exception:
                pass

            async with AsyncSessionLocal() as db:
                payload = {
                    "accounts_count": await db.scalar(select(func.count(TikTokAccount.id))) or 0,
                    "videos_count": await db.scalar(select(func.count(TargetVideo.id))) or 0,
                    "comments_count": await db.scalar(select(func.count(CommentTemplate.id))) or 0,
                    "pending_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "pending")) or 0,
                    "verification_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "verification_required")) or 0,
                    "completed_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["completed", "published"]))) or 0,
                    "failed_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["failed", "dlq"]))) or 0,
                    "worker_online": bool(worker_hb),
                    "telegram_online": bool(tg_hb),
                    "queue_paused": paused,
                }
            await websocket.send_json(payload)
            await asyncio.sleep(3)
    except WebSocketDisconnect:
        pass
