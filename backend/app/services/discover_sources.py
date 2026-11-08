import asyncio
import re
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from app.logger import log_event
from app.models import DiscoverySource, TargetVideo, TikTokAccount
from app.services.integration_service import integration_service
from app.services.source_rotation import corrected_source_type

_COLLECT_JS = """
() => {
  const found = new Set();
  const add = (user, id) => {
    if (user && id) found.add(`https://www.tiktok.com/@${user}/video/${id}`);
  };
  document.querySelectorAll('a[href*="/video/"]').forEach((a) => {
    const match = a.href.match(/@([^/?]+)\/video\/(\\d+)/);
    if (match) add(match[1], match[2]);
  });
  const html = document.documentElement.innerHTML;
  const re = /@([\\w.\\-]+)\\/video\\/(\\d{8,})/g;
  let match;
  while ((match = re.exec(html))) add(match[1], match[2]);
  const text = document.body ? document.body.innerText.slice(0, 400) : '';
  return { links: [...found].slice(0, 20), text };
}
"""


async def reclassify_sources(db) -> None:
    rows = (await db.scalars(select(DiscoverySource))).all()
    changed = False
    for source in rows:
        fixed = corrected_source_type(source.source_type, source.value)
        if fixed != source.source_type:
            source.source_type = fixed
            changed = True
    if changed:
        await db.commit()


def _storage_for(username: str | None) -> str | None:
    if not username:
        return None
    path = Path(integration_service.get_secure_profile_dir(username)) / "storage_state.json"
    return str(path) if path.exists() else None


async def _open_and_collect(url: str, storage_state: str | None, likes: bool = False) -> tuple[list[str], str]:
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        context = await browser.new_context(
            storage_state=storage_state,
            viewport={"width": 960, "height": 800},
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3000)
        if likes:
            tab = page.locator('[data-e2e="liked-tab"], a[href$="/liked"]').first
            if await tab.count():
                await tab.click(timeout=4000)
                await page.wait_for_timeout(3000)
        await page.mouse.wheel(0, 2200)
        await page.wait_for_timeout(2000)
        html = await page.content()
        title = await page.title()
        links = _links_from_html(html)
        note = f"{title}. {(await page.inner_text('body'))[:120].replace(chr(10), ' ')}"
        return links[:20], note
    finally:
        if browser:
            await browser.close()
        await pw.stop()


def _links_from_html(html: str) -> list[str]:
    found: list[str] = []

    def add(user: str, video_id: str) -> None:
        link = f"https://www.tiktok.com/@{user}/video/{video_id}"
        if link not in found:
            found.append(link)

    for user, video_id in re.findall(r"@([\w.\-]+)/video/(\d{8,})", html):
        add(user, video_id)
    for user, video_id in re.findall(r'"uniqueId":"([\w.\-]+)".{0,500}?"id":"(\d{15,22})"', html):
        add(user, video_id)
    for video_id, user in re.findall(r'"id":"(\d{15,22})".{0,500}?"uniqueId":"([\w.\-]+)"', html):
        add(user, video_id)
    return found


def _source_url(source: DiscoverySource) -> str:
    if source.source_type == "hashtag":
        tag = source.value.lstrip("#").replace(" ", "")
        return f"https://www.tiktok.com/tag/{quote(tag)}"
    if source.source_type == "main_likes":
        name = source.value.lstrip("@")
        return f"https://www.tiktok.com/@{name}"
    return f"https://www.tiktok.com/search/video?q={quote(source.value)}"


async def discover_next_source(db) -> int:
    await reclassify_sources(db)
    source = await db.scalar(
        select(DiscoverySource)
        .where(DiscoverySource.is_active.is_(True))
        .order_by(DiscoverySource.last_synced_at.asc().nullsfirst(), DiscoverySource.id.asc())
    )
    if not source:
        return 0

    donor = source.value if source.source_type == "main_likes" else None
    account = None
    if donor:
        name = donor.lstrip("@")
        account = await db.scalar(
            select(TikTokAccount).where(TikTokAccount.username.in_([donor, name, f"@{name}"]))
        )
    if account is None:
        account = await db.scalar(
            select(TikTokAccount).where(TikTokAccount.is_active.is_(True)).order_by(TikTokAccount.id.asc())
        )
    storage = _storage_for(account.username if account else None)
    url = _source_url(source)
    try:
        links, note = await asyncio.wait_for(
            _open_and_collect(url, storage, likes=source.source_type == "main_likes"),
            timeout=70,
        )
    except Exception as exc:
        source.last_error = str(exc).splitlines()[0][:180]
        await db.commit()
        await log_event("worker", "WARNING", "discover_failed", f"{source.value}: {source.last_error}")
        return 0

    if not links:
        low = (note or "").lower()
        if any(word in low for word in ("log in", "войти", "captcha", "verify")):
            source.last_error = "TikTok просит вход или проверку, с сервера страница пустая"
        else:
            source.last_error = f"TikTok не отдал ссылки. Страница: {note[:140]}"
        from datetime import datetime, timezone
        source.last_synced_at = datetime.now(timezone.utc)
        await db.commit()
        await log_event("worker", "WARNING", "discover_empty", f"{source.value}: {source.last_error} | {note}")
        return 0

    added = 0
    for link in links:
        exists = await db.scalar(select(TargetVideo).where(TargetVideo.video_url == link))
        if exists:
            if exists.video_status != "available":
                exists.video_status = "available"
                added += 1
            continue
        db.add(
            TargetVideo(
                video_url=link,
                search_keyword=source.value,
                source=source.value,
                video_status="available",
            )
        )
        added += 1
    from datetime import datetime, timezone

    source.last_synced_at = datetime.now(timezone.utc)
    source.last_error = None if links else "TikTok не отдал ссылки на видео"
    await db.commit()
    await log_event("worker", "INFO", "discover_done", f"{source.source_type} {source.value}: новых {added}")
    return added
