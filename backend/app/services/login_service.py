import asyncio
import base64
import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

from app.logger import log_event
from app.services.integration_service import integration_service

_sessions: dict[int, dict] = {}
_signups: dict[str, dict] = {}
_guard = asyncio.Lock()
_LOGIN_TTL_SEC = 180


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
    browser = await pw.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
    )
    context = await browser.new_context(
        proxy=_proxy_for_browser(proxy_url),
        viewport={"width": 1280, "height": 900},
        locale="ru-RU",
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
    )
    page = await context.new_page()
    return pw, browser, context, page


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


async def _grab_qr(page) -> str:
    box = await page.evaluate(
        """() => {
          const nodes = [...document.querySelectorAll('canvas, img, [data-e2e="qr-code"]')];
          let best = null;
          for (const el of nodes) {
            const r = el.getBoundingClientRect();
            if (r.width < 80 || r.height < 80) continue;
            const area = r.width * r.height;
            if (!best || area > best.area) {
              best = {x: r.x, y: r.y, width: r.width, height: r.height, area};
            }
          }
          return best;
        }"""
    )
    if box:
        try:
            png = await page.screenshot(
                clip={
                    "x": max(0, float(box["x"]) - 12),
                    "y": max(0, float(box["y"]) - 12),
                    "width": float(box["width"]) + 24,
                    "height": float(box["height"]) + 24,
                }
            )
            return base64.b64encode(png).decode()
        except Exception:
            pass
    png = await page.screenshot(clip={"x": 430, "y": 180, "width": 420, "height": 460})
    return base64.b64encode(png).decode()


async def start_qr_login(account_id: int, username: str, proxy_url: str | None) -> dict:
    await _drop_expired()
    await close_login(account_id)
    pw = browser = None
    try:
        pw, browser, context, page = await _launch(proxy_url)
        await page.goto("https://www.tiktok.com/login/qrcode", wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(1500)
        image = await _grab_qr(page)
        async with _guard:
            _sessions[account_id] = {
                "pw": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "username": username,
                "mode": "qr",
                "created": time.time(),
            }
        return {
            "status": "qr_ready",
            "image_base64": image,
            "detail": "Открой TikTok на телефоне → Профиль → меню ☰ → Сканировать. Наведи камеру на этот QR.",
        }
    except Exception:
        await _shutdown_handles(pw, browser)
        await log_event("backend", "WARNING", "qr_login_failed", f"account_id={account_id}")
        return {
            "status": "failed",
            "detail": "Не удалось открыть QR. Проверь прокси и что сервер может открыть tiktok.com.",
        }


async def poll_qr_login(account_id: int) -> dict:
    await _drop_expired()
    async with _guard:
        sess = _sessions.get(account_id)
    if not sess or sess.get("mode") != "qr":
        return {"status": "idle", "detail": "QR не запущен"}
    try:
        if await _logged_in(sess["context"], sess["page"]):
            await _persist(sess["context"], sess["username"])
            await close_login(account_id)
            return {"status": "authenticated", "detail": "Телефон подтвердил вход"}
    except Exception:
        await close_login(account_id)
        return {"status": "failed", "detail": "Сессия QR оборвалась. Запусти QR ещё раз."}
    return {"status": "waiting_scan", "detail": "Жду подтверждение в приложении TikTok"}


async def _open_qr(page) -> None:
    await page.goto("https://www.tiktok.com/login/qrcode", wait_until="domcontentloaded", timeout=45000)
    for label in ("Use QR code", "QR code", "QR-код", "Сканировать"):
        button = page.get_by_text(label, exact=False).first
        try:
            if await button.count():
                await button.click(timeout=1500)
                break
        except Exception:
            continue
    await page.wait_for_timeout(1200)


async def _read_username(page) -> str | None:
    script = """() => {
      const link = document.querySelector('a[data-e2e="nav-profile"]')
        || document.querySelector('a[href^="/@"]');
      return link ? (link.getAttribute('href') || '') : '';
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


async def start_qr_signup() -> dict:
    await _drop_expired()
    token = uuid.uuid4().hex
    pw = browser = None
    try:
        pw, browser, context, page = await _launch(None)
        await _open_qr(page)
        image = await _grab_qr(page)
        async with _guard:
            _signups[token] = {
                "pw": pw,
                "browser": browser,
                "context": context,
                "page": page,
                "created": time.time(),
            }
        return {
            "status": "qr_ready",
            "token": token,
            "image_base64": image,
            "detail": "На телефоне открой TikTok → Профиль → меню ☰ → Сканировать и наведи камеру на QR. Пароль и прокси не нужны.",
        }
    except Exception:
        await _shutdown_handles(pw, browser)
        await log_event("backend", "WARNING", "qr_signup_failed", "Не удалось открыть QR для нового аккаунта")
        return {"status": "failed", "detail": "Не удалось открыть QR. Сервер должен открывать tiktok.com."}


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
        if not await _logged_in(sess["context"], sess["page"]):
            return {"status": "waiting_scan", "detail": "Жду, пока ты отсканируешь QR в приложении TikTok"}
        username = await _read_username(sess["page"])
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
