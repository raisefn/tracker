"""Integration tests for investor endpoints."""

import uuid

import pytest

from src.models import Investor


@pytest.mark.asyncio
async def test_list_investors_empty(client):
    resp = await client.get("/v1/investors")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total"] >= 0


@pytest.mark.asyncio
async def test_list_investors_with_data(client, db_session):
    inv = Investor(name="Test Capital", slug=f"test-capital-{uuid.uuid4().hex[:8]}")
    db_session.add(inv)
    await db_session.flush()

    resp = await client.get("/v1/investors")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total"] >= 1


@pytest.mark.asyncio
async def test_list_investors_search(client, db_session):
    inv = Investor(name="Unique Ventures XYZ", slug=f"unique-xyz-{uuid.uuid4().hex[:8]}")
    db_session.add(inv)
    await db_session.flush()

    resp = await client.get("/v1/investors?search=Unique Ventures")
    assert resp.status_code == 200
    data = resp.json()
    assert any("Unique" in i["name"] for i in data["data"])


@pytest.mark.asyncio
async def test_get_investor_by_slug(client, db_session):
    slug = f"slug-test-{uuid.uuid4().hex[:8]}"
    inv = Investor(name="SlugTest VC", slug=slug)
    db_session.add(inv)
    await db_session.flush()

    resp = await client.get(f"/v1/investors/{slug}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "SlugTest VC"


@pytest.mark.asyncio
async def test_get_investor_not_found(client):
    resp = await client.get("/v1/investors/nonexistent-slug")
    assert resp.status_code == 404
