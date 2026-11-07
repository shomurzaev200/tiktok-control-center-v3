from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logger import log_audit
from app.models import AdminUser, DiscoverySource, TargetVideo
from app.schemas import SourceCreateSchema, VideoCreateSchema, BulkVideosImportSchema
from app.security import get_current_admin, require_role, validate_external_url
from app.services.integration_service import integration_service

router = APIRouter(prefix="/api", tags=["sources & videos"])


@router.get("/sources")
async def list_sources(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.scalars(select(DiscoverySource).order_by(DiscoverySource.id.asc()))).all()
    return {"items": rows, "total": len(rows)}


@router.post("/sources")
async def upsert_source(
    data: SourceCreateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    clean_val = data.value.strip()
    src = await db.scalar(select(DiscoverySource).where(DiscoverySource.value == clean_val))
    if src:
        src.source_type = data.source_type
        src.interval_minutes = data.interval_minutes
        src.is_active = data.is_active
    else:
        src = DiscoverySource(
            source_type=data.source_type,
            value=clean_val,
            interval_minutes=data.interval_minutes,
            is_active=data.is_active,
        )
        db.add(src)
    await db.commit()
    await db.refresh(src)
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "upsert_source", "discovery_source", str(src.id), clean_val, ip)
    return src


@router.delete("/sources/{src_id}")
async def delete_source(
    src_id: int,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    src = await db.get(DiscoverySource, src_id)
    if not src:
        raise HTTPException(status_code=404, detail="Источник не найден")
    await db.delete(src)
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "delete_source", "discovery_source", str(src_id), src.value, ip)
    return {"status": "deleted"}


@router.get("/videos")
async def list_videos(
    search: str | None = Query(default=None),
    video_status: str | None = Query(default=None),
    is_processed: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TargetVideo)
    if search:
        stmt = stmt.where(TargetVideo.video_url.ilike(f"%{search.strip()}%"))
    if video_status:
        stmt = stmt.where(TargetVideo.video_status == video_status)
    if is_processed is not None:
        stmt = stmt.where(TargetVideo.is_processed.is_(is_processed))

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(TargetVideo.id.desc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.post("/videos")
async def add_video(
    data: VideoCreateSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.scalar(select(TargetVideo).where(TargetVideo.video_url == data.video_url))
    if existing:
        return existing

    check = await integration_service.check_video_availability(data.video_url)
    vid = TargetVideo(
        video_url=data.video_url,
        title=check.get("title"),
        author_username=check.get("author_username"),
        search_keyword=data.search_keyword.strip() if data.search_keyword else None,
        source="manual",
        video_status=check.get("video_status", "available"),
    )
    db.add(vid)
    await db.commit()
    await db.refresh(vid)
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "add_video", "target_video", str(vid.id), vid.video_url, ip)
    return vid


@router.post("/videos/bulk")
async def bulk_import_videos(
    data: BulkVideosImportSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    added = 0
    skipped = 0
    invalid = 0
    seen: set[str] = set()
    for raw_url in data.urls:
        u = raw_url.strip()
        if not u or u in seen:
            skipped += 1
            continue
        seen.add(u)
        try:
            clean = validate_external_url(u, allowed_hosts={"tiktok.com", "www.tiktok.com", "vm.tiktok.com"})
        except ValueError:
            invalid += 1
            continue
        exists = await db.scalar(select(TargetVideo).where(TargetVideo.video_url == clean))
        if exists:
            skipped += 1
            continue
        db.add(
            TargetVideo(
                video_url=clean,
                search_keyword=data.search_keyword,
                source="bulk_import",
                video_status="available",
            )
        )
        added += 1
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "bulk_import_videos", "target_video", None, f"added={added}, skipped={skipped}", ip)
    return {"status": "ok", "added": added, "skipped": skipped, "invalid": invalid}


@router.delete("/videos/{vid_id}")
async def delete_video(
    vid_id: int,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    vid = await db.get(TargetVideo, vid_id)
    if not vid:
        raise HTTPException(status_code=404, detail="Видео не найдено")
    await db.delete(vid)
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "delete_video", "target_video", str(vid_id), vid.video_url, ip)
    return {"status": "deleted"}
