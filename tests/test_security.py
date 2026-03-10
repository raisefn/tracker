"""Tests for security hardening: key expiry, HTTPS redirect, log sanitization."""

from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.auth import hash_key, require_api_key
from src.api.deps import get_db, get_redis
from src.models.api_key import ApiKey
from src.pipeline.log_sanitizer import sanitize


# --- Log sanitizer ---


def test_sanitize_api_key_in_url():
    msg = "Failed to fetch https://api.example.com/v1?api_key=abc123secret&foo=bar"
    result = sanitize(msg)
    assert "abc123secret" not in result
    assert "api_key=***" in result
    assert "foo=bar" in result


def test_sanitize_token_in_url():
    msg = "Error: https://example.com/data?token=mysecrettoken"
    result = sanitize(msg)
    assert "mysecrettoken" not in result
    assert "token=***" in result


def test_sanitize_database_url():
    msg = "Connection failed: postgresql+asyncpg://tracker:supersecret@db:5432/tracker"
    result = sanitize(msg)
    assert "supersecret" not in result
    assert "tracker:***@" in result


def test_sanitize_redis_url():
    msg = "Redis error: redis://:myredispassword@redis:6379/0"
    result = sanitize(msg)
    assert "myredispassword" not in result


def test_sanitize_authorization_header():
    msg = "Request failed with Authorization: Bearer sk-abc123xyz"
    result = sanitize(msg)
    assert "sk-abc123xyz" not in result
    assert "Bearer ***" in result


def test_sanitize_clean_message_unchanged():
    msg = "Collected 42 rounds from SEC EDGAR in 3.2s"
    assert sanitize(msg) == msg


# --- API key expiry ---


@pytest.mark.asyncio
async def test_expired_key_rejected(db_session, fake_redis):
    """An expired API key should return 401."""
    raw_key = "rfn_testexpired1234567890abcdef1234567890abcdef1234567890abcdef12345678"
    expired_key = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:8],
        owner="test",
        tier="free",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(expired_key)
    await db_session.flush()

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides.pop(require_api_key, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds", headers={"X-API-Key": raw_key})

    app.dependency_overrides.clear()
    assert resp.status_code == 401
    assert "expired" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_valid_key_with_future_expiry(db_session, fake_redis):
    """A key with future expiry should work normally."""
    raw_key = "rfn_testvalid12345678901234567890abcdef1234567890abcdef1234567890abcdef"
    valid_key = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:8],
        owner="test",
        tier="free",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    db_session.add(valid_key)
    await db_session.flush()

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides.pop(require_api_key, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds", headers={"X-API-Key": raw_key})

    app.dependency_overrides.clear()
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_key_without_expiry_works(db_session, fake_redis):
    """A key with no expiry (None) should work forever."""
    raw_key = "rfn_testnoexpiry234567890abcdef1234567890abcdef1234567890abcdef1234567"
    no_expiry_key = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:8],
        owner="test",
        tier="free",
        expires_at=None,
    )
    db_session.add(no_expiry_key)
    await db_session.flush()

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides.pop(require_api_key, None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds", headers={"X-API-Key": raw_key})

    app.dependency_overrides.clear()
    assert resp.status_code == 200


# --- API key model ---


def test_api_key_has_expires_at_field():
    """Verify the model has the expires_at column."""
    assert hasattr(ApiKey, "expires_at")


# --- Dependency pinning ---


def test_dependencies_are_pinned():
    """Verify pyproject.toml uses exact versions (==) not ranges (>=)."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).parent.parent / "pyproject.toml"
    with open(pyproject, "rb") as f:
        data = tomllib.load(f)

    deps = data["project"]["dependencies"]
    for dep in deps:
        assert "==" in dep, f"Dependency not pinned: {dep}"
        assert ">=" not in dep, f"Dependency uses range: {dep}"
