from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, redis_client
from app.logger import log_audit
from app.models import AdminUser, CommentTemplate, QueueTask, TaskStatusHistory, TargetVideo
from app.schemas import (
    BulkCommentsCreateSchema, CommentUpdateSchema, TaskCreateSchema, TaskManualConfirmSchema
)
from app.security import get_current_admin, require_role
from app.services.queue_service import enqueue_task_idempotent, record_status_transition

router = APIRouter(prefix="/api", tags=["comments & queue"])


@router.get("/comments")
async def list_comments(
    category: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(CommentTemplate)
    if category:
        stmt = stmt.where(CommentTemplate.category == category)
    if search:
        stmt = stmt.where(CommentTemplate.text.ilike(f"%{search.strip()}%"))

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(CommentTemplate.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/comments/bulk")
async def bulk_comments(
    data: BulkCommentsCreateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    added = 0
    skipped = 0
    seen: set[str] = set()
    cat = data.category.strip() or "general"
    for raw in data.texts:
        clean = raw.strip()
        if not clean or clean in seen:
            skipped += 1
            continue
        seen.add(clean)
        exists = await db.scalar(select(CommentTemplate).where(CommentTemplate.text == clean))
        if exists:
            skipped += 1
            continue
        db.add(CommentTemplate(category=cat, text=clean))
        added += 1
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "bulk_comments", "comment_template", None, f"added={added}, skipped={skipped}", ip)
    return {"status": "ok", "added": added, "skipped": skipped}


@router.put("/comments/{tpl_id}")
async def update_comment_template(
    tpl_id: int,
    data: CommentUpdateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    tpl = await db.get(CommentTemplate, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    tpl.category = data.category.strip()
    tpl.text = data.text.strip()
    await db.commit()
    await db.refresh(tpl)
    return tpl


@router.delete("/comments/{tpl_id}")
async def delete_comment(
    tpl_id: int,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    tpl = await db.get(CommentTemplate, tpl_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Шаблон не найден")
    await db.delete(tpl)
    await db.commit()
    return {"status": "deleted"}


@router.get("/tasks")
async def list_tasks(
    queue_name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(QueueTask)
    if queue_name:
        stmt = stmt.where(QueueTask.queue_name == queue_name)
    if status:
        stmt = stmt.where(QueueTask.status == status)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(QueueTask.priority.desc(), QueueTask.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/tasks/{task_id}")
async def get_task_detail(
    task_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    task = await db.get(QueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    history = (
        await db.scalars(
            select(TaskStatusHistory)
            .where(TaskStatusHistory.task_id == task_id)
            .order_by(TaskStatusHistory.id.asc())
        )
    ).all()
    return {"task": task, "history": history}


@router.post("/tasks")
async def create_task_manual(
    data: TaskCreateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    try:
        task, created = await enqueue_task_idempotent(
            db,
            account_id=data.account_id,
            video_id=data.video_id,
            comment_text=data.comment_text,
            priority=data.priority,
        )
    except ValueError as ve:
        raise HTTPException(status_code=429, detail=str(ve))
    return {"task": task, "created_new": created}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: int,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    task = await db.get(QueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено")
    if task.status in ("completed", "cancelled"):
        return {"status": task.status}
    await record_status_transition(db, task, "cancelled", worker_id=admin.username, reason="Отменено администратором")
    task.worker_id = None
    task.lease_expires_at = None
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "cancel_task", "queue_task", str(task_id), None, ip)
    return {"status": "cancelled"}


@router.post("/tasks/{task_id}/manual-confirm")
async def manual_confirm_task(
    task_id: int,
    data: TaskManualConfirmSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    """Ручное подтверждение для заданий со статусом verification_required (п. 3 и п. 9)."""
    task = await db.get(QueueTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Задание не найдено")

    if data.confirmed_live:
        task.external_result = "result_confirmed"
        task.verification_state = "manual_confirmed"
        task.completed_at = datetime.now(timezone.utc)
        task.error_message = None
        await record_status_transition(db, task, "completed", worker_id=admin.username, reason=data.reason)
        vid = await db.get(TargetVideo, task.video_id)
        if vid:
            vid.is_processed = True
    else:
        task.external_result = "rejected"
        task.verification_state = "unverified"
        task.queue_name = "dlq"
        task.error_message = data.reason
        await record_status_transition(db, task, "dlq", worker_id=admin.username, reason=data.reason)

    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "manual_confirm_task", "queue_task", str(task_id), data.reason, ip)
    return {"task": task}


@router.post("/queue/toggle-pause")
async def toggle_queue_pause(
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
):
    paused = await redis_client.get("queue:paused") == "1"
    if paused:
        await redis_client.delete("queue:paused")
    else:
        await redis_client.set("queue:paused", "1")
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "toggle_queue_pause", "queue", None, f"paused={not paused}", ip)
    return {"queue_paused": not paused}
