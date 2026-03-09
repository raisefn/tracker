"""Tests for Phase 1 data model additions: Founder, Fund, exit fields, RoundInvestor enhancements."""

import uuid
from datetime import date

import pytest

from src.models import Founder, Fund, Investor, Project, Round, RoundInvestor


@pytest.mark.asyncio
async def test_founder_crud(db_session):
    proj = Project(name="FounderTest", slug=f"foundertest-{uuid.uuid4().hex[:8]}")
    db_session.add(proj)
    await db_session.flush()

    founder = Founder(
        project_id=proj.id,
        name="Jane Doe",
        slug="jane-doe",
        role="CEO",
        linkedin="https://linkedin.com/in/janedoe",
        twitter="janedoe",
        github="janedoe",
        bio="Serial entrepreneur",
        previous_companies=[{"name": "OldCo", "role": "CTO", "years": "2018-2021"}],
        source="yc_directory",
    )
    db_session.add(founder)
    await db_session.flush()

    assert founder.id is not None
    assert founder.project_id == proj.id
    assert founder.name == "Jane Doe"
    assert founder.role == "CEO"
    assert founder.previous_companies[0]["name"] == "OldCo"


@pytest.mark.asyncio
async def test_founder_project_relationship(db_session):
    proj = Project(name="RelTest", slug=f"reltest-{uuid.uuid4().hex[:8]}")
    db_session.add(proj)
    await db_session.flush()

    f1 = Founder(project_id=proj.id, name="Alice", slug="alice", role="CEO", source="manual")
    f2 = Founder(project_id=proj.id, name="Bob", slug="bob", role="CTO", source="manual")
    db_session.add_all([f1, f2])
    await db_session.flush()

    await db_session.refresh(proj, ["founders"])
    assert len(proj.founders) == 2
    names = {f.name for f in proj.founders}
    assert names == {"Alice", "Bob"}


@pytest.mark.asyncio
async def test_fund_crud(db_session):
    inv = Investor(name=f"FundTestVC-{uuid.uuid4().hex[:8]}", slug=f"fundtestvc-{uuid.uuid4().hex[:8]}")
    db_session.add(inv)
    await db_session.flush()

    fund = Fund(
        investor_id=inv.id,
        name="Fund III",
        slug="fund-iii",
        vintage_year=2023,
        fund_size_usd=500_000_000,
        focus_sectors=["DeFi", "Infrastructure"],
        focus_stages=["Seed", "Series A"],
        status="active",
        source="sec_form_adv",
    )
    db_session.add(fund)
    await db_session.flush()

    assert fund.id is not None
    assert fund.investor_id == inv.id
    assert fund.fund_size_usd == 500_000_000
    assert "DeFi" in fund.focus_sectors


@pytest.mark.asyncio
async def test_fund_investor_relationship(db_session):
    inv = Investor(name=f"FundRelVC-{uuid.uuid4().hex[:8]}", slug=f"fundrelvc-{uuid.uuid4().hex[:8]}")
    db_session.add(inv)
    await db_session.flush()

    f1 = Fund(investor_id=inv.id, name="Fund I", slug="fund-i", vintage_year=2020, source="manual")
    f2 = Fund(investor_id=inv.id, name="Fund II", slug="fund-ii", vintage_year=2022, source="manual")
    db_session.add_all([f1, f2])
    await db_session.flush()

    await db_session.refresh(inv, ["funds"])
    assert len(inv.funds) == 2


@pytest.mark.asyncio
async def test_round_investor_check_size(db_session):
    proj = Project(name="CheckSizeTest", slug=f"checksizetest-{uuid.uuid4().hex[:8]}")
    db_session.add(proj)
    await db_session.flush()

    rnd = Round(
        project_id=proj.id, round_type="seed", amount_usd=10_000_000,
        date=date(2024, 1, 15), source_type="manual",
    )
    db_session.add(rnd)
    await db_session.flush()

    inv = Investor(name=f"CheckVC-{uuid.uuid4().hex[:8]}", slug=f"checkvc-{uuid.uuid4().hex[:8]}")
    db_session.add(inv)
    await db_session.flush()

    ri = RoundInvestor(
        round_id=rnd.id, investor_id=inv.id, is_lead=True,
        check_size_usd=5_000_000, participation_type="equity",
    )
    db_session.add(ri)
    await db_session.flush()

    assert ri.check_size_usd == 5_000_000
    assert ri.participation_type == "equity"


@pytest.mark.asyncio
async def test_project_exit_fields(db_session):
    proj = Project(
        name="ExitTest", slug=f"exittest-{uuid.uuid4().hex[:8]}",
        status="acquired",
        exit_type="acquisition",
        exit_date=date(2024, 6, 1),
        acquirer="BigCorp",
        exit_valuation_usd=100_000_000,
    )
    db_session.add(proj)
    await db_session.flush()

    assert proj.exit_type == "acquisition"
    assert proj.exit_date == date(2024, 6, 1)
    assert proj.acquirer == "BigCorp"
    assert proj.exit_valuation_usd == 100_000_000


@pytest.mark.asyncio
async def test_project_detail_includes_founders(client, db_session):
    slug = f"apifounder-{uuid.uuid4().hex[:8]}"
    proj = Project(name="APIFounderTest", slug=slug)
    db_session.add(proj)
    await db_session.flush()

    founder = Founder(project_id=proj.id, name="TestFounder", slug="testfounder", role="CEO", source="manual")
    db_session.add(founder)
    await db_session.flush()

    resp = await client.get(f"/v1/projects/{slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert "founders" in data
    assert len(data["founders"]) == 1
    assert data["founders"][0]["name"] == "TestFounder"
    assert data["founders"][0]["role"] == "CEO"


@pytest.mark.asyncio
async def test_project_detail_includes_exit_fields(client, db_session):
    slug = f"apiexit-{uuid.uuid4().hex[:8]}"
    proj = Project(
        name="APIExitTest", slug=slug, status="acquired",
        exit_type="acquisition", acquirer="MegaCorp", exit_valuation_usd=50_000_000,
    )
    db_session.add(proj)
    await db_session.flush()

    resp = await client.get(f"/v1/projects/{slug}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["exit_type"] == "acquisition"
    assert data["acquirer"] == "MegaCorp"
    assert data["exit_valuation_usd"] == 50_000_000
