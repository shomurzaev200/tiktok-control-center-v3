from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db, redis_client
from app.logger import log_audit
from app.models import (
    AdminUser, TikTokAccount, TargetVideo, CommentTemplate, QueueTask,
    SystemLog, AuditLog, HealthHistory, BackupRecord, SystemSetting, NotificationLog
)
from app.schemas import SettingUpdateSchema
from app.security import get_current_admin, require_role
from app.services.backup_service import backup_service
from app.services.monitoring_service import monitoring_service

router = APIRouter(prefix="/api", tags=["system & monitoring"])


@router.get("/dashboard")
async def get_dashboard(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    metrics = await monitoring_service.collect_system_metrics()
    paused = False
    try:
        paused = await redis_client.get("queue:paused") == "1"
    except Exception:
        pass

    history_rows = (
        await db.scalars(select(HealthHistory).order_by(HealthHistory.id.desc()).limit(20))
    ).all()

    return {
        "accounts_count": await db.scalar(select(func.count(TikTokAccount.id))) or 0,
        "videos_count": await db.scalar(select(func.count(TargetVideo.id))) or 0,
        "comments_count": await db.scalar(select(func.count(CommentTemplate.id))) or 0,
        "pending_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "pending")) or 0,
        "running_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "running")) or 0,
        "verification_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status == "verification_required")) or 0,
        "completed_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["completed", "published"]))) or 0,
        "failed_tasks": await db.scalar(select(func.count(QueueTask.id)).where(QueueTask.status.in_(["failed", "dlq"]))) or 0,
        "queue_paused": paused,
        "metrics": metrics,
        "health_series": [
            {
                "ts": h.created_at.isoformat() if h.created_at else "",
                "cpu": h.cpu_percent,
                "ram": h.ram_percent,
                "db_ms": h.db_latency_ms,
            }
            for h in reversed(history_rows)
        ],
    }


@router.get("/logs")
async def list_logs(
    service: str | None = Query(default=None),
    level: str | None = Query(default=None),
    search: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SystemLog)
    if service:
        stmt = stmt.where(SystemLog.service == service)
    if level:
        stmt = stmt.where(SystemLog.level == level)
    if search:
        stmt = stmt.where(SystemLog.message.ilike(f"%{search.strip()}%"))

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(SystemLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/audit-logs")
async def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AuditLog)
    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(AuditLog.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/diagnostics")
async def get_diagnostics_report(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    metrics = await monitoring_service.record_health_snapshot(db)
    recent_errors = (
        await db.scalars(
            select(SystemLog).where(SystemLog.level == "ERROR").order_by(SystemLog.id.desc()).limit(10)
        )
    ).all()
    pending_notifs = await db.scalar(
        select(func.count(NotificationLog.id)).where(NotificationLog.status == "pending")
    ) or 0
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "pending_notifications": pending_notifs,
        "recent_errors": recent_errors,
    }


@router.post("/diagnostics/cleanup")
async def trigger_cleanup(
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    res = await monitoring_service.cleanup_stale_records(db)
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "cleanup_stale_records", "system", None, str(res), ip)
    return res


@router.get("/backups")
async def list_backups(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.scalars(select(BackupRecord).order_by(BackupRecord.id.desc()).limit(50))).all()
    return {"items": rows, "total": len(rows)}


@router.post("/backups")
async def create_backup_now(
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    rec = await backup_service.create_and_verify_backup(db, notes=f"manual by {admin.username}")
    await backup_service.cleanup_old_backups(db)
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "create_backup", "backup", str(rec.id), rec.filename, ip)
    return rec


@router.get("/settings")
async def list_settings(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.scalars(select(SystemSetting).order_by(SystemSetting.key.asc()))).all()
    return {"items": rows}


@router.post("/settings")
async def save_setting(
    data: SettingUpdateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    row = await db.get(SystemSetting, data.key)
    if row:
        row.value = data.value
        row.description = data.description
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = SystemSetting(key=data.key, value=data.value, description=data.description)
        db.add(row)
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "update_setting", "setting", data.key, data.value, ip)
    return row
