from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DiscoverySource, TargetVideo

TYPE_ORDER = ("search_keyword", "main_likes", "hashtag")
TYPE_LABEL = {
    "search_keyword": "Поиск",
    "main_likes": "Лайки",
    "hashtag": "Хэштеги",
}


def normalize_source_value(source_type: str, value: str) -> str:
    clean = " ".join(value.strip().split())
    if source_type == "main_likes":
        name = clean.lstrip("@")
        return f"@{name}"
    if source_type == "hashtag":
        tag = clean.lstrip("#")
        return f"#{tag}"
    return clean


def _ordered(rows: list[DiscoverySource]) -> list[DiscoverySource]:
    rank = {name: index for index, name in enumerate(TYPE_ORDER)}
    return sorted(rows, key=lambda row: (rank.get(row.source_type, 99), row.id))


async def _all_sources(db: AsyncSession) -> list[DiscoverySource]:
    rows = (await db.scalars(select(DiscoverySource))).all()
    return _ordered(rows)


async def _pending_for_source(db: AsyncSession, source: DiscoverySource) -> int:
    keyword = source.value.lstrip("@#")
    stmt = select(func.count(TargetVideo.id)).where(
        TargetVideo.is_processed.is_(False),
        TargetVideo.is_excluded.is_(False),
        TargetVideo.video_status == "available",
        or_(
            TargetVideo.search_keyword == source.value,
            TargetVideo.search_keyword == keyword,
            TargetVideo.source == source.value,
        ),
    )
    return await db.scalar(stmt) or 0


async def _seen_for_source(db: AsyncSession, source: DiscoverySource) -> int:
    keyword = source.value.lstrip("@#")
    stmt = select(func.count(TargetVideo.id)).where(
        or_(
            TargetVideo.search_keyword == source.value,
            TargetVideo.search_keyword == keyword,
            TargetVideo.source == source.value,
        )
    )
    return await db.scalar(stmt) or 0


async def advance_finished_sources(db: AsyncSession) -> DiscoverySource | None:
    """Идёт по кругу: поиск, потом лайки, потом хештеги, потом снова поиск."""
    rows = await _all_sources(db)
    if not rows:
        return None

    changed = False
    for source in rows:
        if source.cycle_done:
            continue
        seen = await _seen_for_source(db, source)
        pending = await _pending_for_source(db, source) if seen else 0
        if seen and pending == 0:
            source.cycle_done = True
            changed = True
            continue
        if changed:
            await db.commit()
        return source

    for source in rows:
        source.cycle_done = False
    await db.commit()
    return rows[0]


async def rotation_view(db: AsyncSession) -> dict:
    current = await advance_finished_sources(db)
    rows = await _all_sources(db)
    items = []
    for source in rows:
        items.append(
            {
                "id": source.id,
                "source_type": source.source_type,
                "label": TYPE_LABEL.get(source.source_type, source.source_type),
                "value": source.value,
                "cycle_done": source.cycle_done,
                "is_current": bool(current and current.id == source.id),
                "pending_videos": await _pending_for_source(db, source),
            }
        )
    nxt = next((item for item in items if not item["cycle_done"] and not item["is_current"]), None)
    return {
        "current_id": current.id if current else None,
        "current_label": TYPE_LABEL.get(current.source_type, "") if current else "",
        "current_value": current.value if current else "",
        "next_label": nxt["label"] if nxt else TYPE_LABEL["search_keyword"],
        "next_value": nxt["value"] if nxt else "",
        "order": items,
    }
