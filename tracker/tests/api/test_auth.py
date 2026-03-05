"""Tests for API key authentication and rate limiting."""

import uuid

import fakeredis.aioredis as fakeredis
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.auth import hash_key
from src.api.deps import get_db, get_redis
from src.models.api_key import ApiKey


@pytest.mark.asyncio
async def test_health_no_auth_required(client):
    """Health endpoint should not require authentication."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_missing_api_key_returns_401(db_session):
    """Endpoints should return 401 when no API key is provided."""
    fake_r = fakeredis.FakeRedis(decode_responses=True)

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_r

    # Only override DB and Redis — do NOT override require_api_key
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds")
    assert resp.status_code == 401

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_invalid_api_key_returns_401(db_session):
    """Invalid API key should return 401."""
    fake_r = fakeredis.FakeRedis(decode_responses=True)

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_r

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds", headers={"X-API-Key": "rfn_invalid_key"})
    assert resp.status_code == 401

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_valid_api_key_via_header(db_session):
    """Valid API key in header should grant access."""
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    raw_key = "rfn_testkey123abc"

    api_key = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:8],
        owner="test",
        tier="free",
    )
    db_session.add(api_key)
    await db_session.flush()

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_r

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/v1/rounds", headers={"X-API-Key": raw_key})
    assert resp.status_code == 200

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_valid_api_key_via_query(db_session):
    """Valid API key in query param should grant access."""
    fake_r = fakeredis.FakeRedis(decode_responses=True)
    raw_key = "rfn_querykey456def"

    api_key = ApiKey(
        key_hash=hash_key(raw_key),
        key_prefix=raw_key[:8],
        owner="test",
        tier="free",
    )
    db_session.add(api_key)
    await db_session.flush()

    async def override_get_db():
        yield db_session

    async def override_get_redis():
        yield fake_r

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get(f"/v1/rounds?api_key={raw_key}")
    assert resp.status_code == 200

    app.dependency_overrides.clear()
