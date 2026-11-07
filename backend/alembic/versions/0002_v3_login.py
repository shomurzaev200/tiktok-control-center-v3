"""Add TikTok account login fields

Revision ID: 0002_v3_login
Revises: 0001_v3_initial
Create Date: 2026-11-07 12:00:00.000000
"""
from alembic import op

revision = "0002_v3_login"
down_revision = "0001_v3_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tiktok_accounts ADD COLUMN IF NOT EXISTS login_name VARCHAR(100)")
    op.execute("ALTER TABLE tiktok_accounts ADD COLUMN IF NOT EXISTS password_encrypted TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE tiktok_accounts DROP COLUMN IF EXISTS password_encrypted")
    op.execute("ALTER TABLE tiktok_accounts DROP COLUMN IF EXISTS login_name")
