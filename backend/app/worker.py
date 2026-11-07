import asyncio
import os
import random
import signal
import socket
import time
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal, redis_client
from app.logger import log_event
from app.models import (
    TikTokAccount, TargetVideo, CommentTemplate, QueueTask
)
from app.schema_bootstrap import ensure_schema
from app.security import decrypt_secret
from app.services.integration_service import integration_service
from app.services.monitoring_service import monitoring_service
from app.services.queue_service import (
    enqueue_task_idempotent,
    claim_next_task_atomic,
    recover_expired_leases,
    record_status_transition,
    enqueue_notification,
)

WORKER_ID = f"worker-{socket.gethostname()}-{os.getpid()}"
stop_event = asyncio.Event()


def _handle_sigterm(*_):
    stop_event.set()


async def reset_daily_counters_if_needed(db):
    today = date.today()
    accounts = (
        await db.scalars(select(TikTokAccount).where(TikTokAccount.last_reset_date < today))
    ).all()
    for acc in accounts:
        acc.comments_today = 0
        acc.likes_today = 0
        acc.last_reset_date = today
    if accounts:
        await db.commit()


async def schedule_pending_tasks_if_needed(db):
    video = await db.scalar(
        select(TargetVideo)
        .where(
            TargetVideo.is_processed.is_(False),
            TargetVideo.is_excluded.is_(False),
            TargetVideo.video_status == "available",
        )
        .order_by(TargetVideo.id.asc())
    )
    if not video:
        return

    now_hour = datetime.now(timezone.utc).hour
    account = await db.scalar(
        select(TikTokAccount)
        .where(
            TikTokAccount.is_active.is_(True),
            TikTokAccount.comments_today < TikTokAccount.daily_comment_limit,
            TikTokAccount.work_hour_start <= now_hour,
            TikTokAccount.work_hour_end >= now_hour,
        )
        .order_by(TikTokAccount.last_active_at.asc().nullsfirst())
    )
    if not account:
        return

    tpl = await db.scalar(select(CommentTemplate).order_by(CommentTemplate.used_count.asc()))
    if not tpl:
        return

    try:
        _, created = await enqueue_task_idempotent(
            db, account_id=account.id, video_id=video.id, comment_text=tpl.text
        )
        if created:
            tpl.used_count += 1
            await db.commit()
    except ValueError:
        pass


async def execute_claimed_task(task_id: int) -> int:
    start_ts = time.monotonic()
    async with AsyncSessionLocal() as db:
        task = await db.get(QueueTask, task_id)
        if not task or task.status == "cancelled":
            return 3

        account = await db.get(TikTokAccount, task.account_id)
        video = await db.get(TargetVideo, task.video_id)
        if not account or not account.is_active or not video:
            task.error_code = "ACCOUNT_OR_VIDEO_INACTIVE"
            task.error_message = "Аккаунт отключён или целевое видео удалено"
            task.queue_name = "dlq"
            await record_status_transition(db, task, "dlq", worker_id=WORKER_ID, reason=task.error_message)
            await db.commit()
            return 3

        account.status = "working"
        # Фиксируем перед внешним вызовом, что запрос готовится к отправке
        task.external_result = "request_accepted"
        await db.commit()

        raw_proxy = decrypt_secret(account.proxy_url_encrypted)
        try:
            result = await asyncio.wait_for(
                integration_service.execute_verified_interaction(
                    username=account.username,
                    proxy_url=raw_proxy,
                    video_url=video.video_url,
                    comment_text=task.comment_text,
                    stage_timeout_sec=30,
                ),
                timeout=settings.TASK_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            result = {
                "success": False,
                "external_result": "result_unknown",
                "verification_state": "unverified",
                "cid": None,
                "error_code": "TASK_TIMEOUT",
                "error": f"Превышен общий таймаут выполнения задания ({settings.TASK_TIMEOUT_SEC}с)",
            }

        elapsed_ms = int((time.monotonic() - start_ts) * 1000)
        # Сначала сохраняем внешний результат и длительность
        task.duration_ms = elapsed_ms
        task.external_result = result.get("external_result", "result_unknown")
        task.verification_state = result.get("verification_state", "none")
        task.tiktok_comment_id = result.get("cid")
        task.worker_id = None
        task.locked_at = None
        task.lease_expires_at = None
        account.last_active_at = datetime.now(timezone.utc)

        if result.get("video_status"):
            video.video_status = result["video_status"]
        if result.get("title"):
            video.title = result["title"]
        if result.get("author_username"):
            video.author_username = result["author_username"]

        if result["success"] and task.external_result == "result_confirmed":
            task.completed_at = datetime.now(timezone.utc)
            task.error_code = None
            task.error_message = None
            video.is_processed = True
            account.comments_today += 1
            account.total_published += 1
            account.status = "idle"
            await record_status_transition(
                db, task, "completed", worker_id=WORKER_ID, reason=f"Подтверждено (CID: {task.tiktok_comment_id})"
            )
            await db.commit()
            await enqueue_notification(
                db,
                category="task",
                title="✅ Операция подтверждена",
                message=f"Аккаунт: {account.username}\nВидео: {video.video_url}\nCID: {task.tiktok_comment_id}",
            )
        elif task.external_result == "result_unknown":
            # Строгое правило P0: запрет автоматического повтора внешней операции с неизвестным результатом
            task.queue_name = "verification"
            task.error_code = result.get("error_code", "VERIFICATION_REQUIRED")
            task.error_message = result.get("error")
            account.status = "idle"
            await record_status_transition(
                db,
                task,
                "verification_required",
                worker_id=WORKER_ID,
                reason="Результат внешней операции неизвестен — переведено на проверку без повтора",
            )
            await db.commit()
        else:
            err_code = result.get("error_code", "UNKNOWN")
            classification = integration_service.classify_platform_error(err_code)
            task.error_code = err_code
            task.error_message = result.get("error")
            account.total_errors += 1

            if classification["stop_account"]:
                account.is_active = False
                account.status = "restricted"
                account.auth_state = "reauth_required"

            if classification["retryable"] and task.attempts < task.max_attempts:
                backoff_sec = min(900, (2 ** task.attempts) * 20)
                task.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
                account.status = "idle"
                await record_status_transition(
                    db,
                    task,
                    "pending",
                    worker_id=WORKER_ID,
                    reason=f"Временная ошибка ({err_code}), повтор через {backoff_sec}с",
                )
            else:
                task.queue_name = "dlq"
                video.is_processed = True
                account.status = "error"
                await record_status_transition(
                    db,
                    task,
                    "dlq",
                    worker_id=WORKER_ID,
                    reason=f"Перемещено в DLQ: {err_code} ({task.error_message})",
                )
                await enqueue_notification(
                    db,
                    category="error",
                    title="❌ Задание перемещено в DLQ",
                    message=f"Аккаунт: {account.username}\nКод: {err_code}\nДетали: {task.error_message}",
                )
            await db.commit()

        low_d = min(account.min_delay_sec, account.max_delay_sec)
        high_d = max(account.min_delay_sec, account.max_delay_sec)
        return random.randint(low_d, high_d)


async def worker_loop():
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    semaphore = asyncio.Semaphore(max(1, settings.WORKER_CONCURRENCY))
    Path("/tmp/worker_healthy").touch()
    await ensure_schema()
    await log_event("worker", "INFO", "worker_started", f"Worker Manager {WORKER_ID} запущен")

    async with AsyncSessionLocal() as db:
        await recover_expired_leases(db, worker_id=WORKER_ID)

    while not stop_event.is_set():
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            await redis_client.set("worker:heartbeat", now_iso, ex=90)
            await redis_client.set(f"worker:node:{WORKER_ID}", now_iso, ex=90)
            Path("/tmp/worker_healthy").touch()

            paused = await redis_client.get("queue:paused") == "1"
            if paused:
                await asyncio.sleep(5)
                continue

            # Resource Manager: проверка свободной памяти и диска перед взятием задания
            sys_metrics = await monitoring_service.collect_system_metrics()
            if not sys_metrics["resources_healthy"]:
                await log_event(
                    "worker",
                    "WARNING",
                    "resource_throttle",
                    f"Недостаточно ресурсов (RAM={sys_metrics['free_ram_mb']}MB, Disk={sys_metrics['free_disk_mb']}MB)",
                )
                await asyncio.sleep(15)
                continue

            async with AsyncSessionLocal() as db:
                await reset_daily_counters_if_needed(db)
                await recover_expired_leases(db, worker_id=WORKER_ID)
                await schedule_pending_tasks_if_needed(db)
                claimed = await claim_next_task_atomic(db, worker_id=WORKER_ID, queue_name="execution")

            if not claimed:
                await asyncio.sleep(5)
                continue

            async with semaphore:
                delay = await execute_claimed_task(claimed.id)

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=min(delay, 10))
            except asyncio.TimeoutError:
                pass
        except Exception as exc:
            await log_event("worker", "ERROR", "worker_loop_err", str(exc), exc)
            await asyncio.sleep(5)

    await log_event("worker", "INFO", "worker_stopped", f"Worker Manager {WORKER_ID} корректно остановлен (SIGTERM)")


if __name__ == "__main__":
    asyncio.run(worker_loop())
