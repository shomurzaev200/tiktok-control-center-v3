from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.logger import log_audit, log_event
from app.models import AdminUser, AdminSession
from app.schemas import (
    SetupOwnerRequest, LoginRequest, ChangePasswordRequest, AdminCreateRequest
)
from app.security import (
    hash_password, verify_password, check_rate_limit, clear_rate_limit,
    create_session_tokens, get_current_admin, require_role
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/setup-status")
async def get_setup_status(db: AsyncSession = Depends(get_db)):
    owner = await db.scalar(select(AdminUser).where(AdminUser.is_owner.is_(True)))
    return {"owner_initialized": bool(owner)}


@router.post("/setup-owner")
async def setup_owner(
    req: SetupOwnerRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    await check_rate_limit("setup_owner", ip, limit=5, window_sec=900)

    existing = await db.scalar(select(AdminUser).where(AdminUser.is_owner.is_(True)))
    if existing:
        raise HTTPException(status_code=403, detail="Владелец системы уже инициализирован")

    if req.setup_token != settings.SETUP_SECRET_TOKEN:
        raise HTTPException(status_code=401, detail="Неверный SETUP_SECRET_TOKEN")

    owner = AdminUser(
        username=req.username.strip(),
        password_hash=hash_password(req.password),
        role="owner",
        is_owner=True,
        telegram_id=req.telegram_id,
    )
    db.add(owner)
    try:
        await db.commit()
        await db.refresh(owner)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Владелец или логин уже существует")

    await clear_rate_limit("setup_owner", ip)
    tokens = await create_session_tokens(owner, request, response, db)
    await log_audit(owner.id, owner.username, "setup_owner", "admin_user", str(owner.id), "Инициализация владельца", ip)
    return tokens


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    await check_rate_limit("login", ip, limit=5, window_sec=900)

    user = await db.scalar(select(AdminUser).where(AdminUser.username == req.username.strip()))
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    await clear_rate_limit("login", ip)
    tokens = await create_session_tokens(user, request, response, db)
    await log_audit(user.id, user.username, "login", "session", None, "Успешный вход в систему", ip)
    return tokens


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    jti = getattr(request.state, "session_jti", None)
    if jti:
        await db.execute(
            update(AdminSession).where(AdminSession.jti == jti).values(is_revoked=True)
        )
        await db.commit()
    response.delete_cookie("tt_session", path="/")
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "logout", "session", jti, "Выход с отзывом сессии", ip)
    return {"status": "logged_out"}


@router.get("/me")
async def get_me(request: Request, admin: AdminUser = Depends(get_current_admin)):
    return {
        "id": admin.id,
        "username": admin.username,
        "role": admin.role,
        "is_owner": admin.is_owner,
        "telegram_id": admin.telegram_id,
        "csrf_token": getattr(request.state, "csrf_token", None),
    }


@router.post("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    request: Request,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    ip = request.client.host if request.client else "unknown"
    await check_rate_limit("change_pw", str(admin.id), limit=5, window_sec=900)

    if not verify_password(req.current_password, admin.password_hash):
        raise HTTPException(status_code=400, detail="Текущий пароль указан неверно")

    admin.password_hash = hash_password(req.new_password)
    current_jti = getattr(request.state, "session_jti", None)
    if req.revoke_other_sessions:
        stmt = update(AdminSession).where(AdminSession.user_id == admin.id)
        if current_jti:
            stmt = stmt.where(AdminSession.jti != current_jti)
        await db.execute(stmt.values(is_revoked=True))
    await db.commit()
    await log_audit(admin.id, admin.username, "change_password", "admin_user", str(admin.id), "Смена пароля", ip)
    return {"status": "password_updated"}


@router.get("/sessions")
async def list_active_sessions(
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.scalars(
            select(AdminSession)
            .where(
                AdminSession.user_id == admin.id,
                AdminSession.is_revoked.is_(False),
                AdminSession.expires_at > datetime.now(timezone.utc),
            )
            .order_by(AdminSession.created_at.desc())
        )
    ).all()
    return [
        {
            "id": r.id,
            "jti": r.jti[:12] + "...",
            "ip_address": r.ip_address,
            "user_agent": r.user_agent,
            "created_at": r.created_at,
            "expires_at": r.expires_at,
        }
        for r in rows
    ]


@router.post("/revoke-all-sessions")
async def revoke_all_sessions(
    request: Request,
    response: Response,
    admin: AdminUser = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    await db.execute(
        update(AdminSession).where(AdminSession.user_id == admin.id).values(is_revoked=True)
    )
    await db.commit()
    response.delete_cookie("tt_session", path="/")
    ip = request.client.host if request.client else "unknown"
    await log_audit(admin.id, admin.username, "revoke_all_sessions", "session", str(admin.id), "Отзыв всех сессий", ip)
    return {"status": "all_sessions_revoked"}


@router.get("/admins")
async def list_admins(
    admin: AdminUser = Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    users = (await db.scalars(select(AdminUser).order_by(AdminUser.id.asc()))).all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "role": u.role,
            "is_owner": u.is_owner,
            "telegram_id": u.telegram_id,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.post("/admins")
async def create_admin_user(
    req: AdminCreateRequest,
    request: Request,
    owner: AdminUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    new_user = AdminUser(
        username=req.username.strip(),
        password_hash=hash_password(req.password),
        role=req.role,
        is_owner=False,
        telegram_id=req.telegram_id,
    )
    db.add(new_user)
    try:
        await db.commit()
        await db.refresh(new_user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Администратор с таким логином уже существует")

    ip = request.client.host if request.client else "unknown"
    await log_audit(owner.id, owner.username, "create_admin", "admin_user", str(new_user.id), f"Роль: {req.role}", ip)
    return {"id": new_user.id, "username": new_user.username, "role": new_user.role}


@router.delete("/admins/{user_id}")
async def delete_admin_user(
    user_id: int,
    request: Request,
    owner: AdminUser = Depends(require_role("owner")),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(AdminUser, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target.is_owner:
        raise HTTPException(status_code=400, detail="Удаление владельца системы запрещено")
    await db.delete(target)
    await db.commit()
    ip = request.client.host if request.client else "unknown"
    await log_audit(owner.id, owner.username, "delete_admin", "admin_user", str(user_id), target.username, ip)
    return {"status": "deleted"}
