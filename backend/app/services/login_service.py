import asyncio
import base64
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.config import settings
from app.logger import log_event
from app.services.integration_service import integration_service
from app.services.socks_bridge import SocksHttpBridge

_sessions: dict[int, dict] = {}
_signups: dict[str, dict] = {}
_guard = asyncio.Lock()
_LOGIN_TTL_SEC = 300
_proxy_cursor = 0
_bridges: dict[int, SocksHttpBridge] = {}
_dead_proxies: set[str] = set()
_DEAD_KEY = "DEAD_TIKTOK_PROXIES"


def _normalize_proxy_url(value: str) -> str:
    raw = value.strip()
    if raw.startswith("socks4://"):
        raw = "socks5://" + raw[len("socks4://"):]
    return raw


def _proxy_pool() -> list[str]:
    raw = ",".join(
        part
        for part in ((settings.TIKTOK_PROXY or ""), (settings.TIKTOK_PROXIES or ""))
        if part.strip()
    )
    found: list[str] = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        item = _normalize_proxy_url(part)
        if item and item not in found and item not in _dead_proxies:
            found.append(item)
    return found


async def _load_dead_proxies() -> None:
    if _dead_proxies:
        return
    from app.database import AsyncSessionLocal
    from app.models import SystemSetting

    async with AsyncSessionLocal() as db:
        row = await db.get(SystemSetting, _DEAD_KEY)
        if row and row.value:
            _dead_proxies.update(part for part in row.value.split(",") if part)


async def _forget_proxy(proxy_url: str | None) -> None:
    if not proxy_url or proxy_url in _dead_proxies:
        return
    from app.database import AsyncSessionLocal
    from app.models import SystemSetting

    _dead_proxies.add(proxy_url)
    async with AsyncSessionLocal() as db:
        row = await db.get(SystemSetting, _DEAD_KEY)
        value = ",".join(sorted(_dead_proxies))
        if row:
            row.value = value
        else:
            db.add(SystemSetting(key=_DEAD_KEY, value=value, description="Мёртвые прокси, больше не используются"))
        await db.commit()
    await log_event("backend", "INFO", "proxy_removed", f"Мёртвый прокси удалён из пула: {_proxy_label(proxy_url)}")


def _pick_proxy(explicit: str | None) -> tuple[str | None, int, int]:
    pool = _proxy_pool()
    if explicit and explicit.strip():
        chosen = _normalize_proxy_url(explicit)
        index = pool.index(chosen) + 1 if chosen in pool else 1
        return chosen, index, max(len(pool), 1)
    if not pool:
        return None, 0, 0
    global _proxy_cursor
    index = _proxy_cursor % len(pool)
    _proxy_cursor += 1
    return pool[index], index + 1, len(pool)


def _proxy_label(proxy_url: str | None) -> str:
    if not proxy_url:
        return "без прокси"
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return "прокси"
    return f"{parsed.hostname}:{parsed.port}"


def _proxy_for_browser(proxy_url: str | None) -> dict | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.hostname or not parsed.port:
        return None
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        proxy["username"] = parsed.username
        proxy["password"] = parsed.password or ""
    return proxy


async def _shutdown_handles(pw, browser) -> None:
    bridge = _bridges.pop(id(pw), None) if pw else None
    if bridge:
        try:
            await bridge.close()
        except Exception:
            pass
    if browser:
        try:
            await browser.close()
        except Exception:
            pass
    if pw:
        try:
            await pw.stop()
        except Exception:
            pass


async def close_login(account_id: int) -> None:
    async with _guard:
        sess = _sessions.pop(account_id, None)
    if not sess:
        return
    await _shutdown_handles(sess.get("pw"), sess.get("browser"))


async def _drop_expired() -> None:
    now = time.time()
    async with _guard:
        expired = [k for k, v in _sessions.items() if now - v.get("created", 0) > _LOGIN_TTL_SEC]
    for account_id in expired:
        await close_login(account_id)


async def _launch(proxy_url: str | None):
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    proxy = _proxy_for_browser(proxy_url)
    if proxy and str(proxy.get("server", "")).startswith("socks") and proxy.get("username"):
        parsed = urlparse(proxy["server"])
        bridge = SocksHttpBridge(parsed.hostname, parsed.port, proxy["username"], proxy.get("password") or "")
        await bridge.start()
        _bridges[id(pw)] = bridge
        proxy = {"server": f"http://127.0.0.1:{bridge.local_port}"}
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    context = await browser.new_context(
        proxy=proxy,
        viewport={"width": 1280, "height": 900},
        device_scale_factor=2,
        locale="ru-RU",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return pw, browser, context, page


def _public_error(exc: Exception) -> str:
    text = str(exc).splitlines()[0][:180]
    text = re.sub(r"://[^@\s/]+@", "://", text)
    return text or exc.__class__.__name__


async def _logged_in(context, page) -> bool:
    cookies = await context.cookies("https://www.tiktok.com")
    names = {c.get("name") for c in cookies}
    if names & {"sessionid", "sid_tt", "sessionid_ss"}:
        return True
    url = page.url or ""
    return "tiktok.com" in url and "/login" not in url and any(
        part in url for part in ("/foryou", "/@", "/following")
    )


async def _persist(context, username: str) -> None:
    profile = Path(integration_service.get_secure_profile_dir(username))
    storage_path = profile / "storage_state.json"
    await context.storage_state(path=str(storage_path))
    marker = profile / "session_state.json"
    marker.write_text(
        json.dumps(
            {
                "username": username,
                "created_at": time.time(),
                "expires_at": time.time() + 86400 * 30,
                "storage_state": str(storage_path),
            }
        ),
        encoding="utf-8",
    )


def _qr_phase(payload) -> str | None:
    names = {
        "confirmed": "confirmed",
        "success": "confirmed",
        "scanned": "scanned",
        "expired": "expired",
        "new": "new",
        "0": "new",
        "1": "scanned",
        "2": "confirmed",
        "3": "expired",
    }
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"status", "qr_status"} and isinstance(value, (str, int)):
                    found.append(names.get(str(value).lower(), ""))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(payload)
    for name in ("confirmed", "scanned", "expired", "new"):
        if name in found:
            return name
    return None


def _merge_phase(current: str | None, payload) -> str | None:
    fresh = _qr_phase(payload)
    if fresh in {"confirmed", "scanned", "expired"}:
        return fresh
    return current or fresh


def _reply_text(payload) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    bits = []
    if payload.get("message"):
        bits.append(str(payload["message"])[:40])
    if payload.get("_http"):
        bits.append("http=" + str(payload.get("_http"))[:3])
    if data.get("status") is not None:
        bits.append("status=" + str(data.get("status"))[:40])
    if data.get("error_code") is not None:
        bits.append("code=" + str(data.get("error_code"))[:20])
    desc = data.get("description") or data.get("captcha") or ""
    if desc:
        bits.append(str(desc)[:80])
    return ", ".join(bits)[:180]


async def _ask_confirm(sess) -> str | None:
    phase = None
    for item in sess.get("events") or []:
        if item.get("_src") == "poll" and _qr_phase(item) == "expired":
            continue
        phase = _merge_phase(phase, item)
    token = sess.get("qr_token")
    if not token:
        sess["reply"] = "токен QR не найден, обратный запрос не отправлен"
        return phase
    if phase in {"confirmed", "expired"}:
        return phase
    url = (
        "https://www.tiktok.com/passport/web/check_qrconnect/?aid=1459&token="
        + token
        + "&next=https%3A%2F%2Fwww.tiktok.com%2F"
    )
    try:
        fresh = await sess["page"].evaluate(
            """async (loginToken) => {
              const next = encodeURIComponent('https://www.tiktok.com/');
              const url = '/passport/web/check_qrconnect/?aid=1459&token='
                + encodeURIComponent(loginToken) + '&next=' + next;
              const response = await fetch(url, {credentials: 'include', cache: 'no-store'});
              let body = {};
              try { body = await response.json(); } catch (e) { body = {message: 'non-json'}; }
              if (!body || typeof body !== 'object') body = {message: 'non-json'};
              body._http = response.status;
              return body;
            }""",
            token,
        )
    except Exception as exc:
        sess["reply"] = "обратный запрос не прошёл: " + _public_error(exc)
        return phase
    if isinstance(fresh, dict):
        fresh["_src"] = "poll"
        if not (fresh.get("_http") == 200 and _qr_phase(fresh) == "expired" and phase in {None, "new"}):
            sess.setdefault("events", []).append(fresh)
            phase = _merge_phase(phase, fresh)
        sess["reply"] = _reply_text(fresh) or "TikTok ответил пустым телом"
    else:
        sess["reply"] = "TikTok ответил не JSON"
    return phase


async def _sync_live_qr(sess) -> str | None:
    token = sess.get("qr_token")
    image_value = None
    for item in sess.get("events") or []:
        if item.get("_src") != "page":
            continue
        found = _token_for_qr(item)
        if not found:
            continue
        token = found
        image_value = _extract_qr_value(item)
    if token:
        sess["qr_token"] = token
    if not image_value or image_value == sess.get("shown_qr"):
        return None
    image = await _image_from_value(sess["page"], image_value)
    if image:
        sess["shown_qr"] = image_value
    return image


async def _refresh_expired_qr(sess) -> str | None:
    if sess.get("reloads", 0) >= 1:
        return None
    sess["reloads"] = sess.get("reloads", 0) + 1
    events = sess.get("events")
    if isinstance(events, list):
        events.clear()
    sess["qr_token"] = None
    sess["shown_qr"] = None
    page = sess["page"]
    try:
        await page.goto("https://www.tiktok.com/login/qrcode", wait_until="domcontentloaded", timeout=20000)
    except Exception:
        return None
    for _ in range(8):
        if _sync_token_ready(sess):
            break
        await page.wait_for_timeout(500)
    return await _sync_live_qr(sess)


def _sync_token_ready(sess) -> bool:
    for item in reversed(sess.get("events") or []):
        if item.get("_src") == "page" and _token_for_qr(item):
            return True
    return False


def _extract_qr_value(payload):
    if isinstance(payload, dict):
        for key in ("qrcode", "qr_code", "qrcode_index_url", "scan_qrcode_url"):
            value = payload.get(key)
            if isinstance(value, str) and len(value) > 40:
                return value
        for value in payload.values():
            found = _extract_qr_value(value)
            if found:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _extract_qr_value(item)
            if found:
                return found
    return None


def _token_for_qr(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    token = data.get("token")
    image = data.get("qrcode") or data.get("qrcode_index_url")
    if isinstance(token, str) and isinstance(image, str) and len(token) > 16 and len(image) > 40:
        return token
    return None


async def _close_other_browsers() -> None:
    async with _guard:
        signup_tokens = list(_signups)
        account_ids = list(_sessions)
    for token in signup_tokens:
        await close_signup(token)
    for account_id in account_ids:
        await close_login(account_id)


async def _image_from_value(page, value: str) -> str | None:
    if value.startswith("data:image"):
        return value.split(",", 1)[-1]
    if value.startswith("http://") or value.startswith("https://"):
        response = await page.context.request.get(value, timeout=15000)
        if not response.ok:
            return None
        return base64.b64encode(await response.body()).decode()
    compact = re.sub(r"\s+", "", value)
    if len(compact) > 200 and re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return compact
    return None


async def _open_qr(page) -> list:
    captured: list = []

    async def handle(response):
        url = response.url.lower()
        if "qrcode" not in url and "qr_code" not in url and "qrconnect" not in url:
            return
        try:
            body = await response.json()
        except Exception:
            body = {"message": "non-json"}
        if isinstance(body, dict):
            body["_http"] = response.status
            body["_src"] = "page"
            captured.append(body)

    page.on("response", lambda response: asyncio.create_task(handle(response)))
    await page.goto("https://www.tiktok.com/login/qrcode", wait_until="domcontentloaded", timeout=20000)
    for label in ("Use QR code", "QR code", "QR-код", "Сканировать"):
        button = page.get_by_text(label, exact=False).first
        try:
            if await button.count():
                await button.click(timeout=1500)
                break
        except Exception:
            continue
    for _ in range(12):
        if any(_extract_qr_value(item) for item in captured):
            break
        await page.wait_for_timeout(1000)
    return captured


async def _grab_qr(page) -> str:
    script = """() => {
      const nodes = [...document.querySelectorAll('canvas, img, svg')];
      let best = null;
      for (const el of nodes) {
        const r = el.getBoundingClientRect();
        if (r.width < 110 || r.height < 110 || r.width > 420) continue;
        const ratio = r.width / r.height;
        if (ratio < 0.8 || ratio > 1.25) continue;
        const area = r.width * r.height;
        if (!best || area > best.area) best = {x: r.x, y: r.y, width: r.width, height: r.height, area};
      }
      return best;
    }"""
    for _ in range(8):
        box = await page.evaluate(script)
        if box:
            png = await page.screenshot(
                clip={
                    "x": max(0, float(box["x"]) - 8),
                    "y": max(0, float(box["y"]) - 8),
                    "width": float(box["width"]) + 16,
                    "height": float(box["height"]) + 16,
                }
            )
            return base64.b64encode(png).decode()
        await page.wait_for_timeout(1000)
    raise RuntimeError("TikTok не прислал QR: на странице только значок загрузки")


async def _qr_image(page, captured: list) -> str:
    for item in captured:
        value = _extract_qr_value(item)
        if not value:
            continue
        image = await _image_from_value(page, value)
        if image:
            return image
    return await _grab_qr(page)


async def start_qr_login(account_id: int, username: str, proxy_url: str | None) -> dict:
    await _drop_expired()
    await _close_other_browsers()
    await close_login(account_id)
    pw = browser = None
    try:
        pw, browser, context, page = await _launch(proxy_url)
        captured = await _open_qr(page)
        image = await _qr_image(page, captured)
        qr_token = None
        for item in captured:
            qr_token = _token_for_qr(item) or qr_token
        async with _guard:
            _sessions[account_id] = {
                "pw": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "username": username,
                "mode": "qr",
                "created": time.time(),
                "events": captured,
                "qr_token": qr_token,
            }
        return {
            "status": "qr_ready",
            "image_base64": image,
            "detail": f"Сессия запишется в {username}. На телефоне открой этот аккаунт и нажми «Это я».",
        }
    except Exception as exc:
        await _shutdown_handles(pw, browser)
        await log_event("backend", "WARNING", "qr_login_failed", _public_error(exc))
        return {
            "status": "failed",
            "detail": f"QR для {username} не открылся: {_public_error(exc)}",
        }


async def poll_qr_login(account_id: int) -> dict:
    await _drop_expired()
    async with _guard:
        sess = _sessions.get(account_id)
    if not sess or sess.get("mode") != "qr":
        return {"status": "idle", "detail": "QR не запущен"}
    now = time.time()
    try:
        phase = await _ask_confirm(sess)
        reply = sess.get("reply") or "обратного ответа нет"
        if phase == "expired":
            image = await _refresh_expired_qr(sess)
            if image:
                return {
                    "status": "waiting_scan",
                    "detail": "Старый код истёк. На экране новый, сканируй его.",
                    "image_base64": image,
                }
            if sess.get("reloads", 0) >= 1:
                await close_login(account_id)
                return {"status": "expired", "detail": "QR истёк. Нажми «QR с телефона» ещё раз."}
            phase = None
        logged = await _logged_in(sess["context"], sess["page"])
        if phase in {"confirmed", "success"} and not logged:
            try:
                await sess["page"].goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
            logged = await _logged_in(sess["context"], sess["page"])
        if logged or phase in {"confirmed", "success"}:
            await _persist(sess["context"], sess["username"])
            await close_login(account_id)
            return {"status": "authenticated", "detail": f"Сессия записана в {sess['username']}"}
        if phase == "scanned":
            image = await _sync_live_qr(sess)
            payload = {"status": "waiting_scan", "detail": f"Телефон увидел код. Нажми «Это я» для {sess['username']}."}
            if image:
                payload["image_base64"] = image
            return payload
    except Exception:
        await close_login(account_id)
        return {"status": "failed", "detail": "Сессия QR оборвалась. Нажми «QR с телефона» ещё раз."}
    image = await _sync_live_qr(sess)
    payload = {
        "status": "waiting_scan",
        "detail": f"Обратный ответ TikTok: {reply}",
    }
    if image:
        payload["image_base64"] = image
    return payload


async def _read_username(page) -> str | None:
    script = """() => {
      const link = document.querySelector('a[data-e2e="nav-profile"]')
        || document.querySelector('a[href^="/@"]');
      if (link) return link.getAttribute('href') || '';
      const html = document.documentElement ? document.documentElement.innerHTML : '';
      const match = html.match(/"uniqueId":"([A-Za-z0-9._]{2,24})"/);
      return match ? '/@' + match[1] : '';
    }"""
    for _ in range(2):
        try:
            href = await page.evaluate(script)
            if href and "/@" in href:
                name = href.split("/@", 1)[1].split("/", 1)[0].split("?", 1)[0]
                if re.fullmatch(r"[A-Za-z0-9._]{2,24}", name):
                    return name
        except Exception:
            pass
        try:
            await page.goto("https://www.tiktok.com/", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(1500)
        except Exception:
            break
    match = re.search(r"tiktok\.com/@([A-Za-z0-9._]{2,24})", page.url or "")
    return match.group(1) if match else None


async def close_signup(token: str) -> None:
    async with _guard:
        sess = _signups.pop(token, None)
    if sess:
        await _shutdown_handles(sess.get("pw"), sess.get("browser"))


async def start_qr_signup(target_username: str | None = None) -> dict:
    await _drop_expired()
    await _load_dead_proxies()
    await _close_other_browsers()
    pool = _proxy_pool()
    ordered: list[str | None] = []
    if pool:
        global _proxy_cursor
        start = _proxy_cursor % len(pool)
        for offset in range(min(3, len(pool))):
            ordered.append(pool[(start + offset) % len(pool)])
        _proxy_cursor = (start + len(ordered)) % len(pool)
    ordered.append(None)
    errors: list[str] = []
    for offset, chosen in enumerate(ordered):
        slot = offset + 1
        label = "напрямую" if chosen is None else f"{slot}/{len(ordered)} {_proxy_label(chosen)}"
        token = uuid.uuid4().hex
        pw = browser = None
        try:
            pw, browser, context, page = await _launch(chosen)
            captured = await _open_qr(page)
            image = None
            for item in captured:
                value = _extract_qr_value(item)
                if value:
                    image = await _image_from_value(page, value)
                    if image:
                        break
            if not image:
                image = await _grab_qr(page)
            qr_token = None
            for item in captured:
                qr_token = _token_for_qr(item) or qr_token
            async with _guard:
                _signups[token] = {
                    "pw": pw,
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "created": time.time(),
                    "events": captured,
                    "qr_token": qr_token,
                    "target_username": (target_username or "").strip() or None,
                }
            skipped = f" Пропущено мёртвых: {offset}." if offset else ""
            return {
                "status": "qr_ready",
                "token": token,
                "image_base64": image,
                "detail": f"Идёт только прокси {label}.{skipped} На телефоне: TikTok → Профиль → меню ☰ → Сканировать.",
            }
        except Exception as exc:
            await _shutdown_handles(pw, browser)
            if chosen:
                await _forget_proxy(chosen)
            errors.append(f"{label}: {_public_error(exc)}")
    reason = errors[-1] if errors else "нет прокси"
    await log_event("backend", "WARNING", "qr_signup_failed", reason)
    return {"status": "failed", "detail": f"Ни один прокси не открыл QR. Последний: {reason}"}


async def poll_qr_signup(token: str) -> dict:
    now = time.time()
    async with _guard:
        expired = [key for key, sess in _signups.items() if now - sess.get("created", 0) > _LOGIN_TTL_SEC]
    for key in expired:
        await close_signup(key)
    async with _guard:
        sess = _signups.get(token)
    if not sess:
        return {"status": "expired", "detail": "QR истёк. Нажми «Добавить сканером» ещё раз."}
    try:
        phase = await _ask_confirm(sess)
        reply = sess.get("reply") or "обратного ответа нет"
        if phase == "expired":
            image = await _refresh_expired_qr(sess)
            if image:
                return {
                    "status": "waiting_scan",
                    "detail": "Старый код истёк. На экране новый, сканируй его.",
                    "image_base64": image,
                }
            if sess.get("reloads", 0) >= 1:
                await close_signup(token)
                return {"status": "expired", "detail": "QR истёк. Нажми «Добавить сканером» ещё раз."}
            phase = None
        logged = await _logged_in(sess["context"], sess["page"])
        if phase in {"confirmed", "success"} and not logged:
            try:
                await sess["page"].goto("https://www.tiktok.com/foryou", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
            logged = await _logged_in(sess["context"], sess["page"])
        if not logged and phase != "confirmed":
            image = await _sync_live_qr(sess)
            if phase == "scanned":
                detail = "Телефон увидел код. Нажми «Это я» в TikTok."
            elif phase == "new":
                detail = "TikTok ещё не видит скан. Если картинка сменилась, сканируй новую."
            else:
                detail = f"Обратный ответ TikTok: {reply}"
            payload = {"status": "waiting_scan", "detail": detail}
            if image:
                payload["image_base64"] = image
            return payload
        username = (sess.get("target_username") or "").lstrip("@") or await _read_username(sess["page"])
        if username and not str(username).startswith("@"):
            username = username
        if not username:
            seen = sess.get("login_seen") or now
            sess["login_seen"] = seen
            if now - seen > 25:
                await close_signup(token)
                return {"status": "failed", "detail": "Телефон подтвердил вход, но имя профиля не прочиталось. Запусти QR ещё раз."}
            return {"status": "waiting_scan", "detail": "Телефон подтвердил вход. Читаю имя профиля..."}
        await _persist(sess["context"], f"@{username}")
        await close_signup(token)
        return {
            "status": "authenticated",
            "username": f"@{username}",
            "detail": f"Аккаунт @{username} добавлен",
        }
    except Exception:
        await close_signup(token)
        return {"status": "failed", "detail": "Сессия QR оборвалась. Нажми «Добавить сканером» ещё раз."}


async def start_password_login(
    account_id: int,
    username: str,
    login_name: str,
    password: str,
    proxy_url: str | None,
) -> dict:
    await _drop_expired()
    await close_login(account_id)
    pw = browser = None
    try:
        pw, browser, context, page = await _launch(proxy_url)
        await page.goto(
            "https://www.tiktok.com/login/phone-or-email/email",
            wait_until="domcontentloaded",
            timeout=45000,
        )
        user = page.locator('input[name="username"]').first
        if await user.count() == 0:
            user = page.locator('input[type="text"]').first
        await user.fill(login_name, timeout=15000)
        await page.locator('input[type="password"]').first.fill(password, timeout=15000)
        button = page.locator('button[data-e2e="login-button"], button[type="submit"]').first
        await button.click(timeout=15000)
        await page.wait_for_timeout(4000)
        if await _logged_in(context, page):
            await _persist(context, username)
            await _shutdown_handles(pw, browser)
            return {"status": "authenticated", "detail": "Вход выполнен"}

        code = page.locator(
            'input[autocomplete="one-time-code"], input[placeholder*="code" i], '
            'input[placeholder*="код" i], input[name="code"]'
        ).first
        url = page.url or ""
        needs_code = await code.count() > 0 or any(part in url for part in ("2fa", "verify", "digit"))
        if needs_code:
            async with _guard:
                _sessions[account_id] = {
                    "pw": pw,
                    "browser": browser,
                    "context": context,
                    "page": page,
                    "username": username,
                    "mode": "code",
                    "created": time.time(),
                }
            return {
                "status": "needs_2fa",
                "detail": "TikTok просит код. Введи его в поле ниже в этой панели. Код приходит в SMS, почту или приложение-аутентификатор.",
            }

        body = ""
        try:
            body = (await page.inner_text("body"))[:500].lower()
        except Exception:
            pass
        await _shutdown_handles(pw, browser)
        if any(word in body for word in ("captcha", "puzzle", "drag the", "verify")):
            return {
                "status": "challenge",
                "detail": "TikTok показал проверку. Нажми «QR с телефона» и подтверди вход в приложении.",
            }
        return {
            "status": "failed",
            "detail": "Не удалось войти. Проверь логин, пароль и прокси, либо войди через QR.",
        }
    except Exception:
        await _shutdown_handles(pw, browser)
        await log_event("backend", "WARNING", "password_login_failed", f"account_id={account_id}")
        return {
            "status": "failed",
            "detail": "Вход не запустился. Чаще всего это прокси или блокировка TikTok. Попробуй QR с телефона.",
        }


async def submit_login_code(account_id: int, code: str) -> dict:
    await _drop_expired()
    digits = "".join(ch for ch in code if ch.isdigit())
    if len(digits) < 4:
        return {"status": "failed", "detail": "Код должен состоять из цифр"}
    async with _guard:
        sess = _sessions.get(account_id)
    if not sess or sess.get("mode") != "code":
        return {"status": "expired", "detail": "Окно ввода кода закрыто. Нажми «Войти» ещё раз и дождись нового кода."}
    page = sess["page"]
    try:
        loc = page.locator(
            'input[autocomplete="one-time-code"], input[placeholder*="code" i], '
            'input[placeholder*="код" i], input[name="code"], input[type="tel"]'
        ).first
        await loc.click(timeout=8000)
        await loc.fill(digits, timeout=8000)
        submit = page.locator('button[type="submit"], button[data-e2e="login-button"]').first
        if await submit.count():
            await submit.click(timeout=8000)
        await page.wait_for_timeout(4000)
        if await _logged_in(sess["context"], page):
            await _persist(sess["context"], sess["username"])
            await close_login(account_id)
            return {"status": "authenticated", "detail": "Код принят, аккаунт вошёл"}
        return {"status": "needs_2fa", "detail": "Код не подошёл. Запроси новый в TikTok и введи его сюда."}
    except Exception:
        await close_login(account_id)
        return {"status": "failed", "detail": "Не удалось отправить код. Начни вход заново."}
