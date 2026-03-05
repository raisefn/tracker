"""Integration tests for rounds endpoints."""

import uuid
from datetime import date, datetime

import pytest

from src.models import Project, Round


@pytest.mark.asyncio
async def test_list_rounds_empty(client):
    resp = await client.get("/v1/rounds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"] == []
    assert data["meta"]["total"] == 0


@pytest.mark.asyncio
async def test_list_rounds_with_data(client, db_session):
    project = Project(name="TestCoin", slug="testcoin", sector="defi")
    db_session.add(project)
    await db_session.flush()

    round_ = Round(
        project_id=project.id,
        round_type="seed",
        amount_usd=5_000_000,
        date=date(2024, 6, 1),
        source_type="defillama",
        confidence=0.9,
    )
    db_session.add(round_)
    await db_session.flush()

    resp = await client.get("/v1/rounds")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total"] >= 1
    found = [r for r in data["data"] if r["id"] == str(round_.id)]
    assert len(found) == 1
    assert found[0]["round_type"] == "seed"
    assert found[0]["amount_usd"] == 5_000_000


@pytest.mark.asyncio
async def test_list_rounds_filter_sector(client, db_session):
    project = Project(name="DeFiThing", slug=f"defi-{uuid.uuid4().hex[:8]}", sector="defi")
    db_session.add(project)
    await db_session.flush()

    round_ = Round(
        project_id=project.id,
        date=date(2024, 1, 1),
        source_type="defillama",
        confidence=0.9,
        sector="defi",
    )
    db_session.add(round_)
    await db_session.flush()

    resp = await client.get("/v1/rounds?sector=defi")
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["sector"] == "defi" for r in data["data"])


@pytest.mark.asyncio
async def test_list_rounds_pagination(client, db_session):
    project = Project(name="PagTest", slug=f"pagtest-{uuid.uuid4().hex[:8]}")
    db_session.add(project)
    await db_session.flush()

    for i in range(5):
        db_session.add(Round(
            project_id=project.id,
            date=date(2024, 1, i + 1),
            source_type="defillama",
            confidence=0.9,
        ))
    await db_session.flush()

    resp = await client.get("/v1/rounds?limit=2&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]) == 2
    assert data["meta"]["has_more"] is True


@pytest.mark.asyncio
async def test_get_round_detail(client, db_session):
    project = Project(name="DetailTest", slug=f"detail-{uuid.uuid4().hex[:8]}")
    db_session.add(project)
    await db_session.flush()

    round_ = Round(
        project_id=project.id,
        round_type="series_a",
        amount_usd=10_000_000,
        date=date(2024, 3, 1),
        source_type="defillama",
        confidence=0.85,
    )
    db_session.add(round_)
    await db_session.flush()

    resp = await client.get(f"/v1/rounds/{round_.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["round_type"] == "series_a"
    assert data["amount_usd"] == 10_000_000


@pytest.mark.asyncio
async def test_get_round_not_found(client):
    fake_id = uuid.uuid4()
    resp = await client.get(f"/v1/rounds/{fake_id}")
    assert resp.status_code == 404
