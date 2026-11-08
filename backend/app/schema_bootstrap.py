from sqlalchemy import text

from app.database import Base, engine
import app.models  # noqa: F401


async def ensure_schema() -> None:
    """Создаёт таблицы один раз и помечает Alembic, без гонки и повторного CREATE."""
    async with engine.begin() as conn:
        await conn.execute(text("SELECT pg_advisory_xact_lock(40404040)"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "ALTER TABLE tiktok_accounts ADD COLUMN IF NOT EXISTS login_name VARCHAR(100)"
        ))
        await conn.execute(text(
            "ALTER TABLE tiktok_accounts ADD COLUMN IF NOT EXISTS password_encrypted TEXT"
        ))
        await conn.execute(text(
            "ALTER TABLE discovery_sources ADD COLUMN IF NOT EXISTS cycle_done BOOLEAN NOT NULL DEFAULT false"
        ))
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS alembic_version (
                version_num VARCHAR(32) NOT NULL,
                CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
            )
        """))
        await conn.execute(text("""
            INSERT INTO alembic_version (version_num)
            SELECT '0002_v3_login'
            WHERE NOT EXISTS (SELECT 1 FROM alembic_version)
        """))
        await conn.execute(text("""
            UPDATE alembic_version
            SET version_num = '0002_v3_login'
            WHERE version_num = '0001_v3_initial'
        """))
