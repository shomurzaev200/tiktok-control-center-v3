import html
import os
import unittest

os.environ.setdefault("SETUP_SECRET_TOKEN", "test_setup_secret_token_32_characters_long")
os.environ.setdefault("JWT_SECRET_KEY", "test_jwt_secret_key_that_is_at_least_64_characters_long_for_v3_security")
os.environ.setdefault("CSRF_SECRET_KEY", "test_csrf_secret_key_32_characters_long")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://tt_admin:testpass@localhost:5432/tiktok_control_v3")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PROFILES_DIR", "/tmp/tt_v3_test_profiles")
os.environ.setdefault("BACKUPS_DIR", "/tmp/tt_v3_test_backups")

from app.logger import mask_sensitive_text
from app.security import (
    encrypt_secret,
    decrypt_secret,
    mask_proxy_url,
    validate_external_url,
    hash_password,
    verify_password,
)
from app.schemas import AccountUpsertSchema, VideoCreateSchema
from app.services.integration_service import integration_service


def test_password_hashing_and_verification():
    raw = "StrongAdminPassword_2026!"
    hashed = hash_password(raw)
    assert hashed != raw
    assert verify_password(raw, hashed) is True
    assert verify_password("WrongPassword", hashed) is False


def test_fernet_secret_encryption_roundtrip():
    proxy = "http://user123:super_secret_pw@198.51.100.42:8080"
    encrypted = encrypt_secret(proxy)
    assert encrypted is not None
    assert "super_secret_pw" not in encrypted
    decrypted = decrypt_secret(encrypted)
    assert decrypted == proxy


def test_sensitive_log_and_proxy_masking():
    raw_log = "Connecting via http://proxy_user:my_secret_pass@198.51.100.10:3128 with token=abcdef123456"
    masked = mask_sensitive_text(raw_log)
    assert "my_secret_pass" not in masked
    assert "abcdef123456" not in masked
    assert "http://proxy_user:***@" in masked

    masked_proxy = mask_proxy_url("http://proxy_user:my_secret_pass@198.51.100.10:3128")
    assert masked_proxy == "http://proxy_user:***@198.51.100.10:3128"


def test_ssrf_protection_blocks_internal_addresses():
    with unittest.TestCase().assertRaises(ValueError):
        validate_external_url("http://127.0.0.1:8000/api/health")
    with unittest.TestCase().assertRaises(ValueError):
        validate_external_url("http://localhost/admin")
    with unittest.TestCase().assertRaises(ValueError):
        validate_external_url("http://169.254.169.254/latest/meta-data/")
    with unittest.TestCase().assertRaises(ValueError):
        validate_external_url("http://10.0.0.5:6379")
    with unittest.TestCase().assertRaises(ValueError):
        validate_external_url("https://evil.example.com/video/123", allowed_hosts={"tiktok.com", "www.tiktok.com"})

    valid = validate_external_url(
        "https://www.tiktok.com/@creator/video/7123456789012345678",
        allowed_hosts={"tiktok.com", "www.tiktok.com"},
    )
    assert valid.startswith("https://www.tiktok.com/")


def test_pydantic_input_validation():
    acc = AccountUpsertSchema(username="creator_01", daily_comment_limit=30)
    assert acc.username == "@creator_01"

    vid = VideoCreateSchema(video_url="https://www.tiktok.com/@creator/video/7123456789012345678")
    assert "tiktok.com" in vid.video_url


def test_platform_error_classification_and_no_auto_retry_on_restrictions():
    c_auth = integration_service.classify_platform_error("AUTH_EXPIRED")
    assert c_auth["retryable"] is False
    assert c_auth["stop_account"] is True

    c_priv = integration_service.classify_platform_error("VIDEO_PRIVATE")
    assert c_priv["retryable"] is False
    assert c_priv["stop_account"] is False

    c_rate = integration_service.classify_platform_error("RATE_LIMITED")
    assert c_rate["retryable"] is True
    assert c_rate["stop_account"] is False


def test_telegram_html_escaping():
    malicious_comment = "<script>alert('xss')</script> & <b>bold</b>"
    escaped = html.escape(malicious_comment)
    assert "<script>" not in escaped
    assert "&lt;script&gt;" in escaped
