import json
import re
import traceback
from datetime import datetime, timezone
# Lazy DB imports inside async log functions to keep logger decoupled at import time

SENSITIVE_PATTERNS = [
    (re.compile(r"(https?|socks5)://([^:/@]+):([^@]+)@"), r"\1://\2:***@"),
        (re.compile(r"(password|token|secret|authorization)([=:\s]+)([^\s,&]+)", re.I), r"***"),
]


def mask_sensitive_text(text: str | None) -> str:
    if not text:
        return ""
    masked = str(text)
    for pattern, repl in SENSITIVE_PATTERNS:
        masked = pattern.sub(repl, masked)
    return masked


async def log_event(
    service: str,
    level: str,
    event: str,
    message: str,
    exc: Exception | None = None,
    request_id: str | None = None,
):
    safe_msg = mask_sensitive_text(message)
    tb_str = None
    if exc:
        raw_tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        tb_str = mask_sensitive_text(raw_tb)

    structured = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "service": service,
        "level": level,
        "event": event,
        "message": safe_msg,
    }
    print(json.dumps(structured, ensure_ascii=False))

    try:
        from app.database import AsyncSessionLocal
        from app.models import SystemLog
        async with AsyncSessionLocal() as db:
            db.add(
                SystemLog(
                    request_id=request_id,
                    service=service,
                    level=level,
                    event=event,
                    message=safe_msg,
                    traceback_text=tb_str,
                )
            )
            await db.commit()
    except Exception as db_err:
        print(json.dumps({"fallback_logger_error": str(db_err), **structured}, ensure_ascii=False))


async def log_audit(
    admin_id: int | None,
    username: str,
    action: str,
    target_type: str,
    target_id: str | None = None,
    details: str | None = None,
    ip_address: str = "unknown",
):
    safe_details = mask_sensitive_text(details) if details else None
    try:
        from app.database import AsyncSessionLocal
        from app.models import AuditLog
        async with AsyncSessionLocal() as db:
            db.add(
                AuditLog(
                    admin_id=admin_id,
                    username=username,
                    action=action,
                    target_type=target_type,
                    target_id=str(target_id) if target_id is not None else None,
                    details=safe_details,
                    ip_address=ip_address,
                )
            )
            await db.commit()
    except Exception:
        pass
