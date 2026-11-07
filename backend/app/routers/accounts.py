from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logger import log_audit
from app.models import AdminUser, TikTokAccount
from app.schemas import AccountUpsertSchema, LoginCodeSchema
from app.security import (
    encrypt_secret, decrypt_secret, mask_proxy_url, get_current_admin, require_role
)
from app.services.integration_service import integration_service
from app.services.login_service import (
    close_manual_login,
    manual_click,
    manual_key,
    manual_type,
    poll_manual_login,
    poll_qr_login,
    poll_qr_signup,
    start_manual_login,
    start_password_login,
    start_qr_login,
    start_qr_signup,
    submit_login_code,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.post("/qr-signup")
async def begin_qr_signup(payload: dict | None = Body(default=None), admin: AdminUser = Depends(require_role("admin"))):
    target = ""
    if isinstance(payload, dict):
        target = str(payload.get("username") or "")
    return await start_qr_signup(target or None)


@router.get("/qr-signup/{token}")
async def watch_qr_signup(
    token: str,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await poll_qr_signup(token)
    username = result.get("username")
    if result.get("status") == "authenticated" and username:
        acc = await db.scalar(select(TikTokAccount).where(TikTokAccount.username == username))
        if acc:
            acc.auth_state = "authenticated"
            acc.is_active = True
        else:
            acc = TikTokAccount(username=username, auth_state="authenticated", is_active=True)
            db.add(acc)
        await db.commit()
        await db.refresh(acc)
        result["account_id"] = acc.id
    return result


async def _store_login(db: AsyncSession, result: dict) -> dict:
    username = result.get("username")
    if result.get("status") == "authenticated" and username:
        acc = await db.scalar(select(TikTokAccount).where(TikTokAccount.username == username))
        if acc:
            acc.auth_state = "authenticated"
            acc.is_active = True
        else:
            acc = TikTokAccount(username=username, auth_state="authenticated", is_active=True)
            db.add(acc)
        await db.commit()
        await db.refresh(acc)
        result["account_id"] = acc.id
    return result


@router.post("/manual-login")
async def begin_manual_login(admin: AdminUser = Depends(require_role("admin"))):
    return await start_manual_login()


@router.get("/manual-login")
async def watch_manual_login(admin: AdminUser = Depends(require_role("admin")), db: AsyncSession = Depends(get_db)):
    return await _store_login(db, await poll_manual_login())


@router.post("/manual-login/click")
async def manual_login_click(payload: dict = Body(...), admin: AdminUser = Depends(require_role("admin"))):
    await manual_click(float(payload.get("x") or 0), float(payload.get("y") or 0))
    return {"ok": True}


@router.post("/manual-login/type")
async def manual_login_type(payload: dict = Body(...), admin: AdminUser = Depends(require_role("admin"))):
    await manual_type(str(payload.get("text") or ""))
    image = ""
    try:
        from app.services.login_service import _manual_image
        image = await _manual_image()
    except Exception:
        image = ""
    return {"ok": True, "image_base64": image}


@router.post("/manual-login/key")
async def manual_login_key(payload: dict = Body(...), admin: AdminUser = Depends(require_role("admin"))):
    await manual_key(str(payload.get("key") or ""))
    return {"ok": True}


@router.delete("/manual-login")
async def end_manual_login(admin: AdminUser = Depends(require_role("admin"))):
    await close_manual_login()
    return {"ok": True}


def _serialize_account(acc: TikTokAccount) -> dict:
    return {
        "id": acc.id,
        "username": acc.username,
        "login_name": acc.login_name,
        "has_password": bool(acc.password_encrypted),
        "proxy_masked": acc.proxy_masked or "direct (без прокси)",
        "proxy_status": acc.proxy_status,
        "proxy_ping_ms": acc.proxy_ping_ms,
        "auth_state": acc.auth_state,
        "status": acc.status,
        "is_active": acc.is_active,
        "work_hour_start": acc.work_hour_start,
        "work_hour_end": acc.work_hour_end,
        "min_delay_sec": acc.min_delay_sec,
        "max_delay_sec": acc.max_delay_sec,
        "daily_comment_limit": acc.daily_comment_limit,
        "daily_like_limit": acc.daily_like_limit,
        "comments_today": acc.comments_today,
        "likes_today": acc.likes_today,
        "total_published": acc.total_published,
        "total_errors": acc.total_errors,
        "version": acc.version,
        "last_active_at": acc.last_active_at,
    }


@router.get("")
async def list_accounts(
    search: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(TikTokAccount)
    if search:
        stmt = stmt.where(TikTokAccount.username.ilike(f"%{search.strip()}%"))
    if status:
        stmt = stmt.where(TikTokAccount.status == status)

    total = await db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await db.scalars(
            stmt.order_by(TikTokAccount.id.asc()).offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return {
        "items": [_serialize_account(a) for a in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/{acc_id}")
async def get_account_detail(
    acc_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(TikTokAccount, acc_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    auth_diag = integration_service.check_account_auth_state(acc.username)
    if acc.auth_state not in {"needs_2fa", "qr_pending"} or auth_diag["auth_state"] == "authenticated":
        acc.auth_state = auth_diag["auth_state"]
    await db.commit()
    return {**_serialize_account(acc), "auth_diagnostics": auth_diag}


@router.post("")
async def upsert_account(
    data: AccountUpsertSchema,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.scalar(select(TikTokAccount).where(TikTokAccount.username == data.username))
    if acc:
        if data.version is not None and acc.version != data.version:
            raise HTTPException(
                status_code=409,
                detail="Конфликт версий: аккаунт был изменён другим администратором. Обновите страницу.",
            )
        acc.version += 1
    else:
        acc = TikTokAccount(username=data.username, version=1)
        db.add(acc)

    if data.login_name:
        acc.login_name = data.login_name
    if data.password:
        acc.password_encrypted = encrypt_secret(data.password)

    if data.proxy_url:
        acc.proxy_url_encrypted = encrypt_secret(data.proxy_url)
        acc.proxy_masked = mask_proxy_url(data.proxy_url)
    elif data.proxy_url == "":
        acc.proxy_url_encrypted = None
        acc.proxy_masked = None

    acc.is_active = data.is_active
    acc.work_hour_start = data.work_hour_start
    acc.work_hour_end = data.work_hour_end
    acc.min_delay_sec = min(data.min_delay_sec, data.max_delay_sec)
    acc.max_delay_sec = max(data.min_delay_sec, data.max_delay_sec)
    acc.daily_comment_limit = data.daily_comment_limit
    acc.daily_like_limit = data.daily_like_limit

    auth_diag = integration_service.check_account_auth_state(acc.username)
    if acc.auth_state not in {"needs_2fa", "qr_pending"} or auth_diag["auth_state"] == "authenticated":
        acc.auth_state = auth_diag["auth_state"]

    await db.commit()
    await db.refresh(acc)
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "upsert_account", "tiktok_account", str(acc.id), acc.username, ip)
    return _serialize_account(acc)


async def _account_or_404(db: AsyncSession, acc_id: int) -> TikTokAccount:
    acc = await db.get(TikTokAccount, acc_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    return acc


def _apply_auth_state(acc: TikTokAccount, result: dict) -> dict:
    status = result.get("status")
    if status == "authenticated":
        acc.auth_state = "authenticated"
    elif status == "needs_2fa":
        acc.auth_state = "needs_2fa"
    elif status == "qr_ready":
        acc.auth_state = "qr_pending"
    elif status == "failed":
        acc.auth_state = "reauth_required"
    return result


@router.post("/{acc_id}/login")
async def login_with_password(
    acc_id: int,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await _account_or_404(db, acc_id)
    password = decrypt_secret(acc.password_encrypted)
    if not acc.login_name or not password:
        raise HTTPException(status_code=400, detail="Сначала сохрани логин и пароль этого аккаунта")
    result = await start_password_login(
        acc.id,
        acc.username,
        acc.login_name,
        password,
        decrypt_secret(acc.proxy_url_encrypted),
    )
    _apply_auth_state(acc, result)
    await db.commit()
    return result


@router.post("/{acc_id}/login/code")
async def login_submit_code(
    acc_id: int,
    data: LoginCodeSchema,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await _account_or_404(db, acc_id)
    result = await submit_login_code(acc.id, data.code)
    _apply_auth_state(acc, result)
    await db.commit()
    return result


@router.post("/{acc_id}/login/qr")
async def login_with_qr(
    acc_id: int,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await _account_or_404(db, acc_id)
    result = await start_qr_login(acc.id, acc.username, decrypt_secret(acc.proxy_url_encrypted))
    _apply_auth_state(acc, result)
    await db.commit()
    return result


@router.get("/{acc_id}/login/qr-status")
async def login_qr_status(
    acc_id: int,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    acc = await _account_or_404(db, acc_id)
    result = await poll_qr_login(acc.id)
    if result.get("status") == "authenticated":
        acc.auth_state = "authenticated"
        await db.commit()
    return result


@router.post("/{acc_id}/check-proxy")
async def check_account_proxy(
    acc_id: int,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(TikTokAccount, acc_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    raw_proxy = decrypt_secret(acc.proxy_url_encrypted)
    diag = await integration_service.check_proxy_diagnostics(raw_proxy)
    acc.proxy_status = diag["status"]
    acc.proxy_ping_ms = diag["ping_ms"]
    await db.commit()
    return diag


@router.delete("/{acc_id}")
async def delete_account(
    acc_id: int,
    request: Request,
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    acc = await db.get(TikTokAccount, acc_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Аккаунт не найден")
    uname = acc.username
    await db.delete(acc)
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "delete_account", "tiktok_account", str(acc_id), uname, ip)
    return {"status": "deleted"}
