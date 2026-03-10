"""Tests for webhook CRUD and dispatch."""

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import app
from src.api.auth import require_api_key
from src.api.deps import get_db, get_redis
from src.models.webhook import Webhook
from src.pipeline.webhook_dispatch import _sign_payload, dispatch_event


# --- Signature ---


def test_sign_payload():
    payload = b'{"event": "round.created"}'
    secret = "testsecret"
    sig = _sign_payload(payload, secret)
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    assert sig == expected


# --- Dispatch ---


@pytest.mark.asyncio
async def test_dispatch_no_webhooks(db_session):
    """Dispatch with no registered webhooks returns 0."""
    count = await dispatch_event(db_session, "round.created", {"test": True})
    assert count == 0


@pytest.mark.asyncio
async def test_dispatch_skips_unsubscribed_event(db_session):
    """Webhook not subscribed to this event should be skipped."""
    wh = Webhook(
        url="https://example.com/hook",
        events=["round.updated"],
        secret="abc123",
        owner="test",
    )
    db_session.add(wh)
    await db_session.flush()

    count = await dispatch_event(db_session, "round.created", {"test": True})
    assert count == 0


# --- API CRUD ---


@pytest.mark.asyncio
async def test_create_webhook(client):
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["round.created"],
        "owner": "test",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["url"] == "https://example.com/hook"
    assert data["events"] == ["round.created"]
    assert "secret" in data


@pytest.mark.asyncio
async def test_create_webhook_invalid_event(client):
    resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["invalid.event"],
        "owner": "test",
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_webhooks(client):
    # Create one first
    await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["round.created"],
        "owner": "listtest",
    })
    resp = await client.get("/v1/webhooks?owner=listtest")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    # Secret should not be exposed in list
    assert "secret" not in data[0]


@pytest.mark.asyncio
async def test_delete_webhook(client):
    # Create
    create_resp = await client.post("/v1/webhooks", json={
        "url": "https://example.com/hook",
        "events": ["round.created"],
        "owner": "deltest",
    })
    wh_id = create_resp.json()["id"]

    # Delete
    del_resp = await client.delete(f"/v1/webhooks/{wh_id}")
    assert del_resp.status_code == 204

    # Verify gone
    list_resp = await client.get("/v1/webhooks?owner=deltest")
    assert len(list_resp.json()) == 0
