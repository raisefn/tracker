"""Tests for Phase 5: co-investor graph, network stats, cross-source dedup."""

import uuid
from datetime import date

import pytest

from src.collectors.base import RawRound
from src.models import Investor, Project, Round, RoundInvestor
from src.pipeline.normalizer import make_slug


def _make_project(name: str, sector: str | None = None, **kw) -> Project:
    return Project(name=name, slug=make_slug(name), sector=sector, **kw)


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


async def _seed_graph_data(db_session):
    """Create test data for co-investor graph tests.

    Setup: 3 investors (A, B, C) across 4 rounds in 3 projects.
    - Round 1 (project1/defi): A(lead), B, C
    - Round 2 (project2/defi): A(lead), B(lead)
    - Round 3 (project1/infrastructure): B(lead), C
    - Round 4 (project3/defi): A(lead), B, C   ← A+B+C syndicate appears 2x
    """
    uid = uuid.uuid4().hex[:6]

    p1 = _make_project(f"ProjectOne-{uid}", sector="defi", chains=["ethereum"])
    p2 = _make_project(f"ProjectTwo-{uid}", sector="defi", chains=["solana"])
    p3 = _make_project(f"ProjectThree-{uid}", sector="defi", chains=["ethereum"])
    db_session.add_all([p1, p2, p3])
    await db_session.flush()

    inv_a = _make_investor(f"AlphaVC-{uid}")
    inv_b = _make_investor(f"BetaCapital-{uid}")
    inv_c = _make_investor(f"GammaFund-{uid}")
    db_session.add_all([inv_a, inv_b, inv_c])
    await db_session.flush()

    r1 = _make_round(p1, 5_000_000, "seed", date(2024, 1, 15), sector="defi")
    r2 = _make_round(p2, 20_000_000, "series_a", date(2024, 6, 1), sector="defi")
    r3 = _make_round(p1, 10_000_000, "series_a", date(2024, 9, 1), sector="infrastructure")
    r4 = _make_round(p3, 8_000_000, "seed", date(2024, 11, 1), sector="defi")
    db_session.add_all([r1, r2, r3, r4])
    await db_session.flush()

    db_session.add_all([
        # Round 1: A(lead), B, C
        RoundInvestor(round_id=r1.id, investor_id=inv_a.id, is_lead=True),
        RoundInvestor(round_id=r1.id, investor_id=inv_b.id, is_lead=False),
        RoundInvestor(round_id=r1.id, investor_id=inv_c.id, is_lead=False),
        # Round 2: A(lead), B(lead)
        RoundInvestor(round_id=r2.id, investor_id=inv_a.id, is_lead=True),
        RoundInvestor(round_id=r2.id, investor_id=inv_b.id, is_lead=True),
        # Round 3: B(lead), C
        RoundInvestor(round_id=r3.id, investor_id=inv_b.id, is_lead=True),
        RoundInvestor(round_id=r3.id, investor_id=inv_c.id, is_lead=False),
        # Round 4: A(lead), B, C
        RoundInvestor(round_id=r4.id, investor_id=inv_a.id, is_lead=True),
        RoundInvestor(round_id=r4.id, investor_id=inv_b.id, is_lead=False),
        RoundInvestor(round_id=r4.id, investor_id=inv_c.id, is_lead=False),
    ])
    await db_session.flush()

    return {
        "projects": [p1, p2, p3],
        "rounds": [r1, r2, r3, r4],
        "investors": [inv_a, inv_b, inv_c],
    }


# --- Enriched co-investors ---


@pytest.mark.asyncio
async def test_co_investors_enriched(client, db_session):
    """Co-investor endpoint returns enriched data with sectors and dates."""
    data = await _seed_graph_data(db_session)
    inv_a = data["investors"][0]  # AlphaVC: in rounds 1 (with B,C) and 2 (with B)

    resp = await client.get(f"/v1/investors/{inv_a.slug}/co-investors?min_rounds=1")
    assert resp.status_code == 200
    result = resp.json()

    # B should be top co-investor (3 shared rounds with A: R1, R2, R4)
    assert len(result) >= 1
    b_entry = next((x for x in result if x["slug"] == data["investors"][1].slug), None)
    assert b_entry is not None
    assert b_entry["shared_rounds"] == 3
    assert "defi" in b_entry["shared_sectors"]
    assert b_entry["first_coinvest"] is not None
    assert b_entry["latest_coinvest"] is not None
    assert b_entry["both_led"] == 1  # both led round 2


@pytest.mark.asyncio
async def test_co_investors_min_rounds_filter(client, db_session):
    """Min rounds filter works correctly."""
    data = await _seed_graph_data(db_session)
    inv_a = data["investors"][0]

    # C co-invested with A in 2 rounds (R1, R4), so min_rounds=3 should exclude C
    resp = await client.get(f"/v1/investors/{inv_a.slug}/co-investors?min_rounds=3")
    assert resp.status_code == 200
    result = resp.json()
    slugs = [x["slug"] for x in result]
    assert data["investors"][1].slug in slugs  # B has 3 shared rounds
    assert data["investors"][2].slug not in slugs  # C only has 2


# --- Syndicates ---


@pytest.mark.asyncio
async def test_syndicate_detection(client, db_session):
    """Detects groups of investors that repeatedly invest together."""
    data = await _seed_graph_data(db_session)
    inv_b = data["investors"][1]  # BetaCapital: in all 3 rounds

    resp = await client.get(f"/v1/investors/{inv_b.slug}/syndicates?min_appearances=2")
    assert resp.status_code == 200
    result = resp.json()

    assert result["investor"]["slug"] == inv_b.slug
    # B + C appear together in rounds 1 and 3 → should be detected
    # B + A appear together in rounds 1 and 2 → should be detected
    assert len(result["syndicates"]) >= 1


@pytest.mark.asyncio
async def test_syndicate_empty(client, db_session):
    """Investor with no rounds returns empty syndicates."""
    uid = uuid.uuid4().hex[:6]
    inv = _make_investor(f"LoneWolf-{uid}")
    db_session.add(inv)
    await db_session.flush()

    resp = await client.get(f"/v1/investors/{inv.slug}/syndicates")
    assert resp.status_code == 200
    assert resp.json()["syndicates"] == []


# --- Network stats ---


@pytest.mark.asyncio
async def test_network_stats(client, db_session):
    """Network stats returns correct lead rate and co-investor count."""
    data = await _seed_graph_data(db_session)
    inv_a = data["investors"][0]  # AlphaVC: 3 rounds (R1, R2, R4), all as lead

    resp = await client.get(f"/v1/investors/{inv_a.slug}/network")
    assert resp.status_code == 200
    result = resp.json()

    assert result["rounds_as_lead"] == 3
    assert result["rounds_as_participant"] == 0
    assert result["lead_rate"] == 1.0
    assert result["total_co_investors"] >= 2  # B and C
    assert result["avg_syndicate_size"] > 1.0  # multiple investors per round
    assert result["avg_round_size"] is not None
    assert result["total_deployed"] is not None
    assert result["most_active_year"] == 2024


@pytest.mark.asyncio
async def test_network_stats_not_found(client, db_session):
    resp = await client.get("/v1/investors/nonexistent-xyz/network")
    assert resp.status_code == 404


# --- Investor rounds ---


@pytest.mark.asyncio
async def test_investor_rounds(client, db_session):
    """Investor rounds endpoint returns paginated deal history."""
    data = await _seed_graph_data(db_session)
    inv_a = data["investors"][0]

    resp = await client.get(f"/v1/investors/{inv_a.slug}/rounds")
    assert resp.status_code == 200
    result = resp.json()

    assert result["meta"]["total"] == 3  # A is in rounds 1, 2, and 4
    assert len(result["data"]) == 3
    # Should be sorted by date desc
    dates = [r["date"] for r in result["data"]]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.asyncio
async def test_investor_rounds_filter_is_lead(client, db_session):
    """Filter investor rounds by lead status."""
    data = await _seed_graph_data(db_session)
    inv_b = data["investors"][1]  # B: lead in rounds 2,3; non-lead in round 1

    resp = await client.get(f"/v1/investors/{inv_b.slug}/rounds?is_lead=true")
    assert resp.status_code == 200
    result = resp.json()
    assert result["meta"]["total"] == 2  # led rounds 2 and 3


@pytest.mark.asyncio
async def test_investor_rounds_filter_sector(client, db_session):
    """Filter investor rounds by sector."""
    data = await _seed_graph_data(db_session)
    inv_b = data["investors"][1]

    resp = await client.get(f"/v1/investors/{inv_b.slug}/rounds?sector=defi")
    assert resp.status_code == 200
    result = resp.json()
    assert result["meta"]["total"] == 3  # rounds 1, 2, 4 (all defi)
    for r in result["data"]:
        assert r["sector"] == "defi"


@pytest.mark.asyncio
async def test_investor_rounds_not_found(client, db_session):
    resp = await client.get("/v1/investors/nonexistent-xyz/rounds")
    assert resp.status_code == 404


# --- Cross-source dedup ---


@pytest.mark.asyncio
async def test_cross_source_exact_dedup(db_session):
    """Same round from 2 sources produces 1 record with boosted confidence."""
    from sqlalchemy import select
    from src.pipeline.ingest import ingest_round

    uid = uuid.uuid4().hex[:6]

    raw1 = RawRound(
        project_name=f"DedupTest-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
        raw_data={"source": "defillama"},
    )
    raw2 = RawRound(
        project_name=f"DedupTest-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
        other_investors=["Paradigm"],
        raw_data={"source": "news"},
    )

    result1 = await ingest_round(db_session, raw1, "defillama")
    assert result1 is not None  # New round

    result2 = await ingest_round(db_session, raw2, "news")
    assert result2 is None  # Duplicate, merged

    # Should only have 1 round
    from src.pipeline.normalizer import make_slug
    project_slug = make_slug(f"DedupTest-{uid}")
    rounds = (await db_session.execute(
        select(Round)
        .join(Project, Project.id == Round.project_id)
        .where(Project.slug == project_slug)
    )).scalars().all()
    assert len(rounds) == 1

    # Confidence should be boosted
    rd = rounds[0]
    assert rd.confidence > 0.8  # defillama base (0.5+0.3+bonuses) + corroboration boost

    # Corroborating sources should be tracked
    assert "news" in (rd.raw_data or {}).get("corroborating_sources", [])


@pytest.mark.asyncio
async def test_cross_source_fuzzy_dedup(db_session):
    """Same round with date +3 days and amount +10% is deduplicated."""
    from sqlalchemy import select
    from src.pipeline.ingest import ingest_round

    uid = uuid.uuid4().hex[:6]

    raw1 = RawRound(
        project_name=f"FuzzyDedup-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
    )
    raw2 = RawRound(
        project_name=f"FuzzyDedup-{uid}",
        date=date(2024, 5, 4),  # +3 days
        amount_usd=11_000_000,  # +10%
        round_type="seed",
        lead_investors=["Sequoia"],
    )

    result1 = await ingest_round(db_session, raw1, "defillama")
    assert result1 is not None

    result2 = await ingest_round(db_session, raw2, "news")
    assert result2 is None  # Should be caught by fuzzy dedup

    from src.pipeline.normalizer import make_slug
    project_slug = make_slug(f"FuzzyDedup-{uid}")
    rounds = (await db_session.execute(
        select(Round)
        .join(Project, Project.id == Round.project_id)
        .where(Project.slug == project_slug)
    )).scalars().all()
    assert len(rounds) == 1


@pytest.mark.asyncio
async def test_investor_merge_on_dedup(db_session):
    """When dedup merges, new investors from source B are added to existing round."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from src.pipeline.ingest import ingest_round

    uid = uuid.uuid4().hex[:6]

    raw1 = RawRound(
        project_name=f"MergeTest-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
    )
    raw2 = RawRound(
        project_name=f"MergeTest-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
        other_investors=["Paradigm", "Pantera"],
    )

    await ingest_round(db_session, raw1, "defillama")
    await ingest_round(db_session, raw2, "news")

    from src.pipeline.normalizer import make_slug
    project_slug = make_slug(f"MergeTest-{uid}")
    rounds = (await db_session.execute(
        select(Round)
        .join(Project, Project.id == Round.project_id)
        .where(Project.slug == project_slug)
        .options(selectinload(Round.investor_participations))
    )).scalars().all()
    assert len(rounds) == 1

    investor_count = len(rounds[0].investor_participations)
    assert investor_count == 3  # Sequoia + Paradigm + Pantera


@pytest.mark.asyncio
async def test_no_false_positive_dedup(db_session):
    """Different projects with same date/amount are NOT deduplicated."""
    from sqlalchemy import select
    from src.pipeline.ingest import ingest_round

    uid = uuid.uuid4().hex[:6]

    raw1 = RawRound(
        project_name=f"ProjectAlpha-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
    )
    raw2 = RawRound(
        project_name=f"ProjectBeta-{uid}",
        date=date(2024, 5, 1),
        amount_usd=10_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
    )

    result1 = await ingest_round(db_session, raw1, "defillama")
    result2 = await ingest_round(db_session, raw2, "defillama")

    assert result1 is not None
    assert result2 is not None  # Different project, should create new round

    total = (await db_session.execute(
        select(Round).where(Round.date == date(2024, 5, 1))
    )).scalars().all()
    assert len(total) >= 2


# --- Corroboration constants ---


def test_corroboration_constants():
    from src.pipeline.validator import CORROBORATION_BOOST, MAX_CORROBORATION_BOOST

    assert CORROBORATION_BOOST == 0.1
    assert MAX_CORROBORATION_BOOST == 0.3
    assert MAX_CORROBORATION_BOOST >= CORROBORATION_BOOST
