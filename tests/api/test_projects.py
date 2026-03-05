"""Integration tests for project endpoints."""

import uuid

import pytest

from src.models import Project


@pytest.mark.asyncio
async def test_list_projects_empty(client):
    resp = await client.get("/v1/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total"] >= 0


@pytest.mark.asyncio
async def test_list_projects_with_data(client, db_session):
    proj = Project(name="TestChain", slug=f"testchain-{uuid.uuid4().hex[:8]}", sector="infrastructure")
    db_session.add(proj)
    await db_session.flush()

    resp = await client.get("/v1/projects")
    assert resp.status_code == 200
    data = resp.json()
    assert data["meta"]["total"] >= 1


@pytest.mark.asyncio
async def test_list_projects_filter_sector(client, db_session):
    proj = Project(name="DeFi App", slug=f"defi-app-{uuid.uuid4().hex[:8]}", sector="defi")
    db_session.add(proj)
    await db_session.flush()

    resp = await client.get("/v1/projects?sector=defi")
    assert resp.status_code == 200
    data = resp.json()
    assert all(p["sector"] == "defi" for p in data["data"])


@pytest.mark.asyncio
async def test_get_project_by_slug(client, db_session):
    slug = f"proj-slug-{uuid.uuid4().hex[:8]}"
    proj = Project(name="SlugProject", slug=slug)
    db_session.add(proj)
    await db_session.flush()

    resp = await client.get(f"/v1/projects/{slug}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "SlugProject"


@pytest.mark.asyncio
async def test_get_project_not_found(client):
    resp = await client.get("/v1/projects/nonexistent-slug")
    assert resp.status_code == 404
