import re
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, Field, field_validator
from app.security import validate_external_url

T = TypeVar("T")
USERNAME_RE = re.compile(r"^@?[a-zA-Z0-9_.-]{2,64}$")


class ApiResponse(BaseModel, Generic[T]):
    ok: bool = True
    data: T | None = None
    message: str | None = None
    request_id: str | None = None


class PaginatedData(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int


class SetupOwnerRequest(BaseModel):
    setup_token: str = Field(min_length=8, max_length=128)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    telegram_id: int = Field(gt=0)

    @field_validator("username")
    @classmethod
    def check_user(cls, v: str) -> str:
        clean = v.strip()
        if not USERNAME_RE.match(clean):
            raise ValueError("Логин может содержать только латинские буквы, цифры, '_', '-', '.'")
        return clean


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)
    revoke_other_sessions: bool = True


class AdminCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=128)
    role: str = Field(default="admin", pattern="^(admin|viewer)$")
    telegram_id: int | None = None


class AccountUpsertSchema(BaseModel):
    username: str = Field(min_length=2, max_length=100)
    login_name: str | None = Field(default=None, max_length=100)
    password: str | None = Field(default=None, max_length=128)
    proxy_url: str | None = Field(default=None, max_length=255)
    is_active: bool = True
    work_hour_start: int = Field(default=9, ge=0, le=23)
    work_hour_end: int = Field(default=23, ge=0, le=23)
    min_delay_sec: int = Field(default=20, ge=5, le=3600)
    max_delay_sec: int = Field(default=55, ge=5, le=3600)
    daily_comment_limit: int = Field(default=25, ge=0, le=500)
    daily_like_limit: int = Field(default=80, ge=0, le=1000)
    version: int | None = None

    @field_validator("username")
    @classmethod
    def validate_tt_username(cls, v: str) -> str:
        clean = v.strip().lstrip("@")
        if not USERNAME_RE.match(f"@{clean}"):
            raise ValueError("Username профиля: латиница, цифры, точка, _ и -. Пример: name_01")
        return f"@{clean}"

    @field_validator("login_name")
    @classmethod
    def validate_login_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        clean = v.strip()
        return clean or None

    @field_validator("proxy_url")
    @classmethod
    def validate_proxy(cls, v: str | None) -> str | None:
        if not v or not v.strip():
            return None
        raw = v.strip()
        if "://" not in raw:
            raw = "http://" + raw
        return validate_external_url(raw, allow_proxy=True)


class LoginCodeSchema(BaseModel):
    code: str = Field(min_length=4, max_length=8)


class SourceCreateSchema(BaseModel):
    source_type: str = Field(pattern="^(main_likes|search_keyword|hashtag)$")
    value: str = Field(min_length=2, max_length=200)
    interval_minutes: int = Field(default=30, ge=5, le=1440)
    is_active: bool = True


class VideoCreateSchema(BaseModel):
    video_url: str = Field(min_length=15, max_length=500)
    search_keyword: str | None = Field(default=None, max_length=200)

    @field_validator("video_url")
    @classmethod
    def validate_tiktok_url(cls, v: str) -> str:
        clean = validate_external_url(v, allowed_hosts={"tiktok.com", "www.tiktok.com", "vm.tiktok.com"})
        return clean


class BulkVideosImportSchema(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=500)
    search_keyword: str | None = Field(default=None, max_length=200)


class BulkCommentsCreateSchema(BaseModel):
    category: str = Field(default="general", min_length=1, max_length=100)
    texts: list[str] = Field(min_length=1, max_length=500)


class CommentUpdateSchema(BaseModel):
    category: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1000)


class TaskCreateSchema(BaseModel):
    account_id: int = Field(gt=0)
    video_id: int = Field(gt=0)
    comment_text: str = Field(min_length=1, max_length=1000)
    priority: int = Field(default=5, ge=1, le=10)


class TaskManualConfirmSchema(BaseModel):
    confirmed_live: bool
    reason: str = Field(default="Ручная проверка администратором", max_length=255)


class SettingUpdateSchema(BaseModel):
    key: str = Field(min_length=2, max_length=64)
    value: str = Field(max_length=1000)
    description: str | None = Field(default=None, max_length=255)
