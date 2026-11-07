"""Initial TikTok Control Center v3.0 schema with constraints and indexes

Revision ID: 0001_v3_initial
Revises:
Create Date: 2026-11-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "0001_v3_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="admin"),
        sa.Column("is_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("role IN ('owner', 'admin', 'viewer')", name="ck_admin_users_role"),
    )
    op.create_index("ix_admin_users_username", "admin_users", ["username"], unique=True)
    op.create_index(
        "uq_single_owner",
        "admin_users",
        ["is_owner"],
        unique=True,
        postgresql_where=sa.text("is_owner = true"),
    )

    op.create_table(
        "admin_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("jti", sa.String(64), nullable=False, unique=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("user_agent", sa.String(255), nullable=False),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_admin_sessions_jti", "admin_sessions", ["jti"], unique=True)
    op.create_index("ix_admin_sessions_user_id", "admin_sessions", ["user_id"])

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("admin_id", sa.Integer(), sa.ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("username", sa.String(64), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "discovery_sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("value", sa.String(255), nullable=False, unique=True),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("interval_minutes >= 5 AND interval_minutes <= 1440", name="ck_source_interval"),
    )

    op.create_table(
        "tiktok_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("username", sa.String(100), nullable=False, unique=True),
        sa.Column("proxy_url_encrypted", sa.Text(), nullable=True),
        sa.Column("proxy_masked", sa.String(255), nullable=True),
        sa.Column("proxy_status", sa.String(64), nullable=False, server_default="unchecked"),
        sa.Column("proxy_ping_ms", sa.Integer(), nullable=True),
        sa.Column("auth_state", sa.String(32), nullable=False, server_default="unchecked"),
        sa.Column("status", sa.String(32), nullable=False, server_default="idle"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("work_hour_start", sa.Integer(), nullable=False, server_default="9"),
        sa.Column("work_hour_end", sa.Integer(), nullable=False, server_default="23"),
        sa.Column("min_delay_sec", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("max_delay_sec", sa.Integer(), nullable=False, server_default="55"),
        sa.Column("daily_comment_limit", sa.Integer(), nullable=False, server_default="25"),
        sa.Column("daily_like_limit", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("comments_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("likes_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_reset_date", sa.Date(), nullable=False, server_default=sa.func.current_date()),
        sa.Column("total_published", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_errors", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("work_hour_start >= 0 AND work_hour_start <= 23", name="ck_acc_work_start"),
        sa.CheckConstraint("work_hour_end >= 0 AND work_hour_end <= 23", name="ck_acc_work_end"),
        sa.CheckConstraint("daily_comment_limit >= 0 AND daily_comment_limit <= 500", name="ck_acc_comm_lim"),
    )
    op.create_index("ix_tiktok_accounts_username", "tiktok_accounts", ["username"], unique=True)

    op.create_table(
        "target_videos",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("video_url", sa.String(500), nullable=False, unique=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("author_username", sa.String(100), nullable=True),
        sa.Column("search_keyword", sa.String(255), nullable=True),
        sa.Column("source", sa.String(64), nullable=False, server_default="manual"),
        sa.Column("video_status", sa.String(32), nullable=False, server_default="available"),
        sa.Column("is_excluded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_target_videos_video_url", "target_videos", ["video_url"], unique=True)
    op.create_index("ix_target_videos_status_processed", "target_videos", ["video_status", "is_processed"])

    op.create_table(
        "comment_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(100), nullable=False, server_default="general"),
        sa.Column("text", sa.Text(), nullable=False, unique=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "queue_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("operation_uid", sa.String(64), nullable=False, unique=True),
        sa.Column("queue_name", sa.String(32), nullable=False, server_default="execution"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("account_id", sa.Integer(), sa.ForeignKey("tiktok_accounts.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("target_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("comment_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("external_result", sa.String(32), nullable=False, server_default="none"),
        sa.Column("verification_state", sa.String(32), nullable=False, server_default="none"),
        sa.Column("tiktok_comment_id", sa.String(100), nullable=True),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("account_id", "video_id", name="uq_task_account_video"),
    )
    op.create_index("ix_queue_tasks_status_priority", "queue_tasks", ["queue_name", "status", "priority", "next_retry_at"])
    op.create_index("ix_queue_tasks_lease", "queue_tasks", ["status", "lease_expires_at"])

    op.create_table(
        "task_status_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), sa.ForeignKey("queue_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("from_status", sa.String(32), nullable=True),
        sa.Column("to_status", sa.String(32), nullable=False),
        sa.Column("worker_id", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_task_status_history_task_id", "task_status_history", ["task_id"])

    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(32), nullable=False, server_default="system"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_notification_logs_status", "notification_logs", ["status", "created_at"])

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "backup_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False, unique=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "health_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cpu_percent", sa.Float(), nullable=False),
        sa.Column("ram_percent", sa.Float(), nullable=False),
        sa.Column("free_ram_mb", sa.Integer(), nullable=False),
        sa.Column("free_disk_mb", sa.Integer(), nullable=False),
        sa.Column("db_latency_ms", sa.Float(), nullable=False),
        sa.Column("redis_latency_ms", sa.Float(), nullable=False),
        sa.Column("worker_online", sa.Boolean(), nullable=False),
        sa.Column("telegram_online", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_health_history_created_at", "health_history", ["created_at"])

    op.create_table(
        "system_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("service", sa.String(32), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("event", sa.String(128), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("traceback_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_system_logs_service_level_created", "system_logs", ["service", "level", "created_at"])


def downgrade() -> None:
    for tbl in [
        "system_logs", "health_history", "backup_records", "system_settings",
        "notification_logs", "task_status_history", "queue_tasks", "comment_templates",
        "target_videos", "tiktok_accounts", "discovery_sources", "audit_logs",
        "admin_sessions", "admin_users"
    ]:
        op.drop_table(tbl)
