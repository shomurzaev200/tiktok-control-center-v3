import time
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=1800,
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
redis_client = Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    socket_timeout=5.0,
    socket_connect_timeout=5.0,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def measure_db_latency_ms() -> float:
    start = time.monotonic()
    try:
        from app.database import AsyncSessionLocal
        from app.models import SystemLog
        async with AsyncSessionLocal() as db:
            await db.execute(select(1))
        return round((time.monotonic() - start) * 1000, 2)
    except Exception:
        return -1.0


async def measure_redis_latency_ms() -> float:
    start = time.monotonic()
    try:
        await redis_client.ping()
        return round((time.monotonic() - start) * 1000, 2)
    except Exception:
        return -1.0
