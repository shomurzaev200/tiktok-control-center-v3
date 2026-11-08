import asyncio
from pathlib import Path
from urllib.parse import quote

from sqlalchemy import select

from app.logger import log_event
from app.models import DiscoverySource, TargetVideo, TikTokAccount
from app.services.integration_service import integration_service
from app.services.source_rotation import corrected_source_type

_COLLECT_JS = """
() => [...document.querySelectorAll('a[href*="/video/"]')]
  .map((a) => a.href.split('?')[0])
  .filter((href) => href.includes('/video/'))
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


async def _open_and_collect(url: str, storage_state: str | None) -> list[str]:
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
        await page.wait_for_timeout(4000)
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(2000)
        found = await page.evaluate(_COLLECT_JS)
        unique = []
        for href in found:
            if href not in unique:
                unique.append(href)
        return unique[:20]
    finally:
        if browser:
            await browser.close()
        await pw.stop()


def _source_url(source: DiscoverySource) -> str:
    if source.source_type == "hashtag":
        tag = source.value.lstrip("#").replace(" ", "")
        return f"https://www.tiktok.com/tag/{quote(tag)}"
    if source.source_type == "main_likes":
        name = source.value.lstrip("@")
        return f"https://www.tiktok.com/@{name}/liked"
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
        account = await db.scalar(select(TikTokAccount).where(TikTokAccount.username == donor))
    if account is None:
        account = await db.scalar(
            select(TikTokAccount).where(TikTokAccount.is_active.is_(True)).order_by(TikTokAccount.id.asc())
        )
    storage = _storage_for(account.username if account else None)
    url = _source_url(source)
    try:
        links = await asyncio.wait_for(_open_and_collect(url, storage), timeout=70)
    except Exception as exc:
        source.last_error = str(exc).splitlines()[0][:180]
        await db.commit()
        await log_event("worker", "WARNING", "discover_failed", f"{source.value}: {source.last_error}")
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
