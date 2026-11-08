import base64
import hashlib
import ipaddress
import socket
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request, Response, WebSocket, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db, redis_client
from app.models import AdminUser, AdminSession

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)

ROLE_LEVELS = {"viewer": 1, "admin": 2, "owner": 3}


def _get_fernet() -> Fernet:
    raw_key = settings.ENCRYPTION_KEY.strip()
    if len(raw_key) == 44:
        try:
            return Fernet(raw_key.encode("utf-8"))
        except Exception:
            pass
    derived = base64.urlsafe_b64encode(hashlib.sha256(settings.JWT_SECRET_KEY.encode("utf-8")).digest())
    return Fernet(derived)


def encrypt_secret(plain: str | None) -> str | None:
    if not plain:
        return None
    return _get_fernet().encrypt(plain.strip().encode("utf-8")).decode("utf-8")


def decrypt_secret(cipher: str | None) -> str | None:
    if not cipher:
        return None
    try:
        return _get_fernet().decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception:
        return None


def mask_proxy_url(proxy_url: str | None) -> str | None:
    if not proxy_url:
        return None
    try:
        parsed = urlparse(proxy_url.strip())
        if parsed.username or parsed.password:
            user = parsed.username or ""
            return f"{parsed.scheme}://{user}:***@{parsed.hostname}:{parsed.port or ''}"
        return f"{parsed.scheme}://{parsed.hostname}:{parsed.port or ''}"
    except Exception:
        return "masked://proxy"


def validate_external_url(url: str, allowed_hosts: set[str] | None = None, allow_proxy: bool = False) -> str:
    """Защита от SSRF: проверяет схему, хост и блокирует локальные/внутренние IP-адреса."""
    clean = url.strip()
    parsed = urlparse(clean)
    allowed_schemes = {"http", "https", "socks5"} if allow_proxy else {"https", "http"}
    if parsed.scheme.lower() not in allowed_schemes:
        raise ValueError(f"Недопустимая схема URL: {parsed.scheme}")
    host = (parsed.hostname or "").lower()
    if not host or host in {"localhost", "0.0.0.0", "metadata.google.internal"} or host.endswith(".local"):
        raise ValueError("Внутренние и локальные адреса запрещены политикой SSRF")

    if allowed_hosts and not any(host == h or host.endswith(f".{h}") for h in allowed_hosts):
        raise ValueError(f"Домен {host} не входит в список разрешённых ({', '.join(allowed_hosts)})")

    try:
        ip_obj = ipaddress.ip_address(host)
        if (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        ):
            raise ValueError("Частные и зарезервированные IP-адреса запрещены (SSRF защита)")
    except ValueError as ve:
        if "SSRF" in str(ve) or "запрещены" in str(ve):
            raise
    return clean


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


async def check_rate_limit(scope: str, identifier: str, limit: int = 10, window_sec: int = 900):
    key = f"rl:{scope}:{identifier}"
    try:
        current = await redis_client.incr(key)
        if current == 1:
            await redis_client.expire(key, window_sec)
        if current > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Превышен лимит запросов ({limit} за {window_sec // 60} мин). Повторите позже.",
            )
    except HTTPException:
        raise
    except Exception:
        pass


async def clear_rate_limit(scope: str, identifier: str):
    try:
        await redis_client.delete(f"rl:{scope}:{identifier}")
    except Exception:
        pass


async def create_session_tokens(
    user: AdminUser, request: Request, response: Response, db: AsyncSession
) -> dict:
    jti = uuid.uuid4().hex
    csrf_token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=30)

    session_row = AdminSession(
        jti=jti,
        csrf_token=csrf_token,
        user_id=user.id,
        ip_address=request.client.host if request.client else "unknown",
        user_agent=(request.headers.get("user-agent") or "unknown")[:250],
        expires_at=expires,
    )
    db.add(session_row)
    await db.commit()

    payload = {
        "sub": str(user.id),
        "username": user.username,
        "role": user.role,
        "jti": jti,
        "csrf": csrf_token,
        "exp": expires,
    }
    jwt_token = jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm="HS256")

    response.set_cookie(
        key="tt_session",
        value=jwt_token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=86400 * 30,
        path="/",
    )
    return {
        "access_token": jwt_token,
        "csrf_token": csrf_token,
        "username": user.username,
        "role": user.role,
    }


def _extract_token_from_request(request: Request, creds: HTTPAuthorizationCredentials | None) -> tuple[str | None, bool]:
    cookie_tok = request.cookies.get("tt_session")
    if cookie_tok:
        return cookie_tok, True
    if creds and creds.credentials:
        return creds.credentials, False
    return None, False


async def get_current_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> AdminUser:
    raw_token, from_cookie = _extract_token_from_request(request, creds)
    if not raw_token:
        raise HTTPException(status_code=401, detail="Требуется авторизация")

    try:
        payload = jwt.decode(raw_token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        jti = payload.get("jti")
        user_id = int(payload.get("sub"))
    except Exception:
        raise HTTPException(status_code=401, detail="Недействительный или просроченный токен сессии")

    sess = await db.scalar(
        select(AdminSession).where(
            AdminSession.jti == jti,
            AdminSession.is_revoked.is_(False),
            AdminSession.expires_at > datetime.now(timezone.utc),
        )
    )
    if not sess:
        raise HTTPException(status_code=401, detail="Сессия завершена или отозвана")

    if from_cookie and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        header_csrf = request.headers.get("X-CSRF-Token")
        if not header_csrf or header_csrf != sess.csrf_token:
            raise HTTPException(status_code=403, detail="Ошибка проверки CSRF-токена")

    user = await db.get(AdminUser, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="Пользователь не найден")

    request.state.session_jti = jti
    request.state.csrf_token = sess.csrf_token
    return user


def require_role(min_role: str):
    async def role_dependency(user: AdminUser = Depends(get_current_admin)) -> AdminUser:
        user_lvl = ROLE_LEVELS.get(user.role, 0)
        req_lvl = ROLE_LEVELS.get(min_role, 99)
        if user_lvl < req_lvl:
            raise HTTPException(
                status_code=403,
                detail=f"Недостаточно прав. Требуется роль не ниже '{min_role}'.",
            )
        return user

    return role_dependency


async def authenticate_websocket(websocket: WebSocket, db: AsyncSession) -> AdminUser | None:
    token = websocket.cookies.get("tt_session") or websocket.query_params.get("token")
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
        jti = payload.get("jti")
        user_id = int(payload.get("sub"))
    except Exception:
        return None

    sess = await db.scalar(
        select(AdminSession).where(
            AdminSession.jti == jti,
            AdminSession.is_revoked.is_(False),
            AdminSession.expires_at > datetime.now(timezone.utc),
        )
    )
    if not sess:
        return None
    return await db.get(AdminUser, user_id)
