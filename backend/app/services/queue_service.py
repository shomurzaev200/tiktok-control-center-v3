import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.models import QueueTask, TaskStatusHistory, TikTokAccount, TargetVideo, NotificationLog


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def record_status_transition(
    db: AsyncSession,
    task: QueueTask,
    new_status: str,
    worker_id: str | None = None,
    reason: str | None = None,
):
    old_status = task.status
    task.status = new_status
    task.version += 1
    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=old_status,
            to_status=new_status,
            worker_id=worker_id,
            reason=reason,
        )
    )


async def enqueue_task_idempotent(
    db: AsyncSession,
    account_id: int,
    video_id: int,
    comment_text: str,
    priority: int = 5,
    operation_uid: str | None = None,
) -> tuple[QueueTask, bool]:
    """
    Идемпотентное создание задания с контролем переполнения очереди.
    Возвращает (task, created_new).
    """
    op_uid = operation_uid or f"op_{account_id}_{video_id}_{uuid.uuid5(uuid.NAMESPACE_OID, f'{account_id}:{video_id}').hex[:16]}"
    existing = await db.scalar(
        select(QueueTask).where(
            or_(
                QueueTask.operation_uid == op_uid,
                (QueueTask.account_id == account_id) & (QueueTask.video_id == video_id),
            )
        )
    )
    if existing:
        return existing, False

    pending_count = await db.scalar(
        select(func.count(QueueTask.id)).where(QueueTask.status.in_(["pending", "running"]))
    ) or 0
    if pending_count >= settings.MAX_QUEUE_SIZE:
        raise ValueError(f"Очередь переполнена (максимум {settings.MAX_QUEUE_SIZE} активных заданий)")

    task = QueueTask(
        operation_uid=op_uid,
        queue_name="execution",
        priority=max(1, min(10, priority)),
        account_id=account_id,
        video_id=video_id,
        comment_text=comment_text.strip(),
        status="pending",
        external_result="none",
        verification_state="none",
    )
    db.add(task)
    await db.flush()
    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=None,
            to_status="pending",
            worker_id="scheduler",
            reason="Задание создано и поставлено в очередь execution",
        )
    )
    await db.commit()
    await db.refresh(task)
    return task, True


async def claim_next_task_atomic(
    db: AsyncSession, worker_id: str, queue_name: str = "execution", lease_seconds: int = 120
) -> QueueTask | None:
    """
    Атомарное резервирование одного задания воркером через SELECT ... FOR UPDATE SKIP LOCKED.
    Исключает одновременную обработку одного задания несколькими воркерами.
    """
    now = utcnow()
    stmt = (
        select(QueueTask)
        .where(
            QueueTask.queue_name == queue_name,
            QueueTask.status == "pending",
            or_(QueueTask.next_retry_at.is_(None), QueueTask.next_retry_at <= now),
        )
        .order_by(QueueTask.priority.desc(), QueueTask.id.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    task = await db.scalar(stmt)
    if not task:
        return None

    await record_status_transition(
        db, task, "running", worker_id=worker_id, reason=f"Зарезервировано воркером {worker_id}"
    )
    task.worker_id = worker_id
    task.locked_at = now
    task.lease_expires_at = now + timedelta(seconds=lease_seconds)
    task.attempts += 1
    await db.commit()
    await db.refresh(task)
    return task


async def recover_expired_leases(db: AsyncSession, worker_id: str = "recovery_manager") -> int:
    """
    Безопасное восстановление зависших заданий после истечения lease_expires_at.
    Строгое правило P0: если внешний результат неизвестен или запрос уже был отправлен
    (external_result in ('request_accepted', 'result_unknown')), автоматический повтор ЗАПРЕЩЁН —
    задание переводится в 'verification_required'.
    """
    now = utcnow()
    stuck_tasks = (
        await db.scalars(
            select(QueueTask)
            .where(
                QueueTask.status == "running",
                QueueTask.lease_expires_at.is_not(None),
                QueueTask.lease_expires_at < now,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()

    recovered = 0
    for task in stuck_tasks:
        recovered += 1
        task.worker_id = None
        task.locked_at = None
        task.lease_expires_at = None

        if task.external_result in ("request_accepted", "result_unknown"):
            task.queue_name = "verification"
            await record_status_transition(
                db,
                task,
                "verification_required",
                worker_id=worker_id,
                reason="Прервано при неопределённом внешнем результате — автоповтор запрещён (требуется проверка)",
            )
        elif task.attempts < task.max_attempts:
            backoff_sec = min(900, (2 ** task.attempts) * 15)
            task.next_retry_at = now + timedelta(seconds=backoff_sec)
            await record_status_transition(
                db,
                task,
                "pending",
                worker_id=worker_id,
                reason=f"Истёк lease до начала внешней операции — возврат в очередь (backoff {backoff_sec}с)",
            )
        else:
            task.queue_name = "dlq"
            task.error_message = "Превышен лимит попыток после истечения lease"
            await record_status_transition(
                db,
                task,
                "dlq",
                worker_id=worker_id,
                reason="Перемещено в Dead Letter Queue (превышен max_attempts)",
            )

    if recovered:
        await db.commit()
    return recovered


async def enqueue_notification(
    db: AsyncSession, category: str, title: str, message: str
) -> NotificationLog:
    notif = NotificationLog(
        category=category,
        title=title[:250],
        message=message,
        status="pending",
    )
    db.add(notif)
    await db.commit()
    await db.refresh(notif)
    return notif
