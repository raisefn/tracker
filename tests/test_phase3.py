"""Tests for Phase 3: stats, search, comps, export."""

import csv
import io
import uuid
from datetime import date

import pytest

from src.models import Investor, Project, Round, RoundInvestor
from src.pipeline.normalizer import make_slug


def _make_project(name: str, sector: str | None = None, chains: list[str] | None = None, **kw) -> Project:
    return Project(
        name=name,
        slug=make_slug(name),
        sector=sector,
        chains=chains,
        **kw,
    )


def _make_round(project: Project, amount: int | None, round_type: str | None = None,
                round_date: date = date(2024, 6, 1), sector: str | None = None, **kw) -> Round:
    return Round(
        project_id=project.id,
        amount_usd=amount,
        round_type=round_type,
        date=round_date,
        sector=sector or project.sector,
        source_type="manual",
        confidence=0.9,
        **kw,
    )


def _make_investor(name: str, **kw) -> Investor:
    return Investor(name=name, slug=make_slug(name), **kw)


async def _seed_data(db_session):
    """Create a set of test data for stats/search/comps tests."""
    uid = uuid.uuid4().hex[:6]

    p1 = _make_project(f"AlphaProtocol-{uid}", sector="defi", chains=["ethereum"])
    p2 = _make_project(f"BetaFinance-{uid}", sector="defi", chains=["ethereum", "solana"])
    p3 = _make_project(f"GammaHealth-{uid}", sector="healthtech")

    db_session.add_all([p1, p2, p3])
    await db_session.flush()

    inv1 = _make_investor(f"Sequoia-{uid}")
    inv2 = _make_investor(f"Paradigm-{uid}")
    db_session.add_all([inv1, inv2])
    await db_session.flush()

    r1 = _make_round(p1, 5_000_000, "seed", date(2024, 3, 15), sector="defi")
    r2 = _make_round(p2, 20_000_000, "series_a", date(2024, 6, 1), sector="defi")
    r3 = _make_round(p3, 10_000_000, "seed", date(2024, 9, 1), sector="healthtech")
    r4 = _make_round(p1, 15_000_000, "series_a", date(2024, 11, 1), sector="defi")

    db_session.add_all([r1, r2, r3, r4])
    await db_session.flush()

    # Link investors to rounds
    db_session.add_all([
        RoundInvestor(round_id=r1.id, investor_id=inv1.id, is_lead=True),
        RoundInvestor(round_id=r2.id, investor_id=inv2.id, is_lead=True),
        RoundInvestor(round_id=r2.id, investor_id=inv1.id, is_lead=False),
        RoundInvestor(round_id=r3.id, investor_id=inv1.id, is_lead=True),
        RoundInvestor(round_id=r4.id, investor_id=inv2.id, is_lead=True),
    ])
    await db_session.flush()

    return {"projects": [p1, p2, p3], "rounds": [r1, r2, r3, r4], "investors": [inv1, inv2]}


# --- Stats overview ---

@pytest.mark.asyncio
async def test_stats_overview(client, db_session):
    data = await _seed_data(db_session)

    resp = await client.get("/v1/stats/overview", params={"period": "all"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["period"] == "all"
    assert body["total_rounds"] >= 4
    assert body["total_capital"] is not None
    assert body["total_capital"] >= 50_000_000  # 5M + 20M + 10M + 15M
    assert body["avg_round_size"] is not None
    assert body["median_round_size"] is not None


@pytest.mark.asyncio
async def test_stats_overview_round_type_breakdown(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/stats/overview", params={"period": "all"})
    body = resp.json()

    type_map = {rt["round_type"]: rt for rt in body["by_round_type"]}
    assert "seed" in type_map
    assert type_map["seed"]["count"] >= 2


# --- Stats sectors ---

@pytest.mark.asyncio
async def test_stats_sectors(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/stats/sectors", params={"period": "all"})
    assert resp.status_code == 200
    body = resp.json()

    sectors = {s["sector"]: s for s in body}
    assert "defi" in sectors
    assert sectors["defi"]["round_count"] >= 3
    assert "healthtech" in sectors


# --- Stats investors ---

@pytest.mark.asyncio
async def test_stats_investors(client, db_session):
    data = await _seed_data(db_session)

    resp = await client.get("/v1/stats/investors", params={"period": "all", "limit": 10})
    assert resp.status_code == 200
    body = resp.json()

    assert body["period"] == "all"
    assert len(body["most_active"]) >= 2
    assert len(body["biggest_deployers"]) >= 1

    # First most_active should have highest round count
    assert body["most_active"][0]["round_count"] >= body["most_active"][1]["round_count"]


# --- Stats trends ---

@pytest.mark.asyncio
async def test_stats_trends(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/stats/trends", params={
        "metric": "round_count",
        "granularity": "month",
        "period": "all",
    })
    assert resp.status_code == 200
    body = resp.json()

    assert body["metric"] == "round_count"
    assert body["granularity"] == "month"
    assert len(body["data"]) >= 1  # At least one month bucket

    # Each trend point should have period and value
    for point in body["data"]:
        assert "period" in point
        assert "value" in point


@pytest.mark.asyncio
async def test_stats_trends_with_sector_filter(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/stats/trends", params={
        "metric": "total_capital",
        "granularity": "quarter",
        "sector": "defi",
        "period": "all",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["sector"] == "defi"


# --- Search ---

@pytest.mark.asyncio
async def test_search_projects(client, db_session):
    data = await _seed_data(db_session)
    name = data["projects"][0].name

    # Search by exact substring
    resp = await client.get("/v1/search", params={"q": name[:10], "type": "projects"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1
    assert any(r["name"] == name for r in body["results"])


@pytest.mark.asyncio
async def test_search_investors(client, db_session):
    data = await _seed_data(db_session)
    name = data["investors"][0].name

    resp = await client.get("/v1/search", params={"q": name[:8], "type": "investors"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 1


@pytest.mark.asyncio
async def test_search_all(client, db_session):
    data = await _seed_data(db_session)

    # Use a short unique suffix that both projects and investors share
    uid = data["projects"][0].name.split("-")[-1]
    resp = await client.get("/v1/search", params={"q": uid, "type": "all"})
    assert resp.status_code == 200
    body = resp.json()

    types = {r["entity_type"] for r in body["results"]}
    # Should have at least one type
    assert len(types) >= 1


# --- Comps ---

@pytest.mark.asyncio
async def test_comps_same_sector(client, db_session):
    data = await _seed_data(db_session)
    slug = data["projects"][0].slug

    resp = await client.get(f"/v1/projects/{slug}/comps", params={"limit": 5})
    assert resp.status_code == 200
    body = resp.json()

    assert body["target"]["slug"] == slug
    # p2 is same sector (defi) and shares ethereum chain — should be a comp
    comp_slugs = [c["project"]["slug"] for c in body["comps"]]
    assert data["projects"][1].slug in comp_slugs

    # Each comp should have score and reasons
    for comp in body["comps"]:
        assert comp["score"] > 0
        assert len(comp["match_reasons"]) > 0


@pytest.mark.asyncio
async def test_comps_not_found(client, db_session):
    resp = await client.get("/v1/projects/nonexistent-project/comps")
    assert resp.status_code == 404


# --- Export ---

@pytest.mark.asyncio
async def test_export_rounds_csv(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/export/rounds")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]

    # Parse CSV
    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)

    # First row is header
    assert rows[0] == [
        "date", "project_name", "round_type", "amount_usd", "valuation_usd",
        "sector", "chains", "lead_investors", "all_investors",
        "source_url", "source_type", "confidence",
    ]
    # At least 4 data rows
    assert len(rows) >= 5


@pytest.mark.asyncio
async def test_export_rounds_with_filters(client, db_session):
    await _seed_data(db_session)

    resp = await client.get("/v1/export/rounds", params={"sector": "defi"})
    assert resp.status_code == 200

    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    # Header + at least 3 defi rounds
    assert len(rows) >= 4

    # All data rows should have defi sector
    for row in rows[1:]:
        assert row[5] == "defi"
