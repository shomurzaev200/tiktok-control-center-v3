from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_ENV: str = "production"
    DOMAIN: str = "localhost"
    ALLOWED_ORIGINS: str = "http://localhost,https://localhost"

    SETUP_SECRET_TOKEN: str
    JWT_SECRET_KEY: str
    CSRF_SECRET_KEY: str = "default_csrf_secret_key_change_in_prod_32"
    ENCRYPTION_KEY: str = ""

    DATABASE_URL: str
    REDIS_URL: str

    TELEGRAM_BOT_TOKEN: str = ""
    OWNER_TELEGRAM_ID: int = 0
    TIKTOK_PROXY: str = ""

    PROFILES_DIR: str = "/app/profiles"
    BACKUPS_DIR: str = "/app/backups"

    WORKER_CONCURRENCY: int = 2
    MAX_QUEUE_SIZE: int = 5000
    TASK_TIMEOUT_SEC: int = 180
    BACKUP_RETENTION_DAYS: int = 14
    MIN_FREE_DISK_MB: int = 1024
    MIN_FREE_RAM_MB: int = 256

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: str) -> str:
        if len(v.strip()) < 16 or v.startswith("change_me"):
            raise ValueError("JWT_SECRET_KEY слишком короткий или содержит небезопасное шаблонное значение")
        return v.strip()


settings = Settings()
