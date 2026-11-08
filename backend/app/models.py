from datetime import datetime, date, timezone
from sqlalchemy import (
    String, Integer, BigInteger, Boolean, Float, DateTime, Date, Text,
    ForeignKey, UniqueConstraint, CheckConstraint, Index, text
)
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        CheckConstraint("role IN ('owner', 'admin', 'viewer')", name="ck_admin_users_role"),
        Index("uq_single_owner", "is_owner", unique=True, postgresql_where=text("is_owner = true")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="admin")
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdminSession(Base):
    __tablename__ = "admin_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int] = mapped_column(ForeignKey("admin_users.id", ondelete="CASCADE"), index=True)
    ip_address: Mapped[str] = mapped_column(String(64))
    user_agent: Mapped[str] = mapped_column(String(255))
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_users.id", ondelete="SET NULL"), nullable=True)
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    ip_address: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DiscoverySource(Base):
    __tablename__ = "discovery_sources"
    __table_args__ = (
        CheckConstraint("interval_minutes >= 5 AND interval_minutes <= 1440", name="ck_source_interval"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(String(255), unique=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    cycle_done: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TikTokAccount(Base):
    __tablename__ = "tiktok_accounts"
    __table_args__ = (
        CheckConstraint("work_hour_start >= 0 AND work_hour_start <= 23", name="ck_acc_work_start"),
        CheckConstraint("work_hour_end >= 0 AND work_hour_end <= 23", name="ck_acc_work_end"),
        CheckConstraint("daily_comment_limit >= 0 AND daily_comment_limit <= 500", name="ck_acc_comm_lim"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    login_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    password_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_url_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_masked: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proxy_status: Mapped[str] = mapped_column(String(64), default="unchecked")
    proxy_ping_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auth_state: Mapped[str] = mapped_column(String(32), default="unchecked")
    status: Mapped[str] = mapped_column(String(32), default="idle")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    work_hour_start: Mapped[int] = mapped_column(Integer, default=9)
    work_hour_end: Mapped[int] = mapped_column(Integer, default=23)
    min_delay_sec: Mapped[int] = mapped_column(Integer, default=20)
    max_delay_sec: Mapped[int] = mapped_column(Integer, default=55)
    daily_comment_limit: Mapped[int] = mapped_column(Integer, default=25)
    daily_like_limit: Mapped[int] = mapped_column(Integer, default=80)

    comments_today: Mapped[int] = mapped_column(Integer, default=0)
    likes_today: Mapped[int] = mapped_column(Integer, default=0)
    last_reset_date: Mapped[date] = mapped_column(Date, default=date.today)

    total_published: Mapped[int] = mapped_column(Integer, default=0)
    total_errors: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TargetVideo(Base):
    __tablename__ = "target_videos"
    __table_args__ = (
        Index("ix_target_videos_status_processed", "video_status", "is_processed"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    video_url: Mapped[str] = mapped_column(String(500), unique=True, index=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    author_username: Mapped[str | None] = mapped_column(String(100), nullable=True)
    search_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="manual")
    video_status: Mapped[str] = mapped_column(String(32), default="available")
    is_excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False)
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommentTemplate(Base):
    __tablename__ = "comment_templates"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(100), default="general")
    text: Mapped[str] = mapped_column(Text, unique=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class QueueTask(Base):
    __tablename__ = "queue_tasks"
    __table_args__ = (
        UniqueConstraint("account_id", "video_id", name="uq_task_account_video"),
        Index("ix_queue_tasks_status_priority", "queue_name", "status", "priority", "next_retry_at"),
        Index("ix_queue_tasks_lease", "status", "lease_expires_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    operation_uid: Mapped[str] = mapped_column(String(64), unique=True)
    queue_name: Mapped[str] = mapped_column(String(32), default="execution")
    priority: Mapped[int] = mapped_column(Integer, default=5)
    account_id: Mapped[int] = mapped_column(ForeignKey("tiktok_accounts.id", ondelete="CASCADE"))
    video_id: Mapped[int] = mapped_column(ForeignKey("target_videos.id", ondelete="CASCADE"))
    comment_text: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(32), default="pending")
    external_result: Mapped[str] = mapped_column(String(32), default="none")
    verification_state: Mapped[str] = mapped_column(String(32), default="none")
    tiktok_comment_id: Mapped[str | None] = mapped_column(String(100), nullable=True)

    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskStatusHistory(Base):
    __tablename__ = "task_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("queue_tasks.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_status: Mapped[str] = mapped_column(String(32))
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class NotificationLog(Base):
    __tablename__ = "notification_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(32), default="system")
    title: Mapped[str] = mapped_column(String(255))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BackupRecord(Base):
    __tablename__ = "backup_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HealthHistory(Base):
    __tablename__ = "health_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    cpu_percent: Mapped[float] = mapped_column(Float)
    ram_percent: Mapped[float] = mapped_column(Float)
    free_ram_mb: Mapped[int] = mapped_column(Integer)
    free_disk_mb: Mapped[int] = mapped_column(Integer)
    db_latency_ms: Mapped[float] = mapped_column(Float)
    redis_latency_ms: Mapped[float] = mapped_column(Float)
    worker_online: Mapped[bool] = mapped_column(Boolean)
    telegram_online: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SystemLog(Base):
    __tablename__ = "system_logs"
    __table_args__ = (
        Index("ix_system_logs_service_level_created", "service", "level", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    service: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(16))
    event: Mapped[str] = mapped_column(String(128))
    message: Mapped[str] = mapped_column(Text)
    traceback_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
