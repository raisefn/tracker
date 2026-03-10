"""Integration tests for the ingestion pipeline."""

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from src.collectors.base import RawRound
from src.collectors.news_parser import clean_company_name
from src.models import Investor, Project, Round, RoundInvestor
from src.pipeline.ingest import ingest_round


# --- Company name cleaning ---


def test_clean_headline_prefix():
    assert clean_company_name("South Korean AI game firm Verse8") == "Verse8"


def test_clean_crypto_startup_prefix():
    assert clean_company_name("Crypto startup Aave") == "Aave"


def test_clean_multiple_descriptors():
    assert clean_company_name("Indian fintech startup Razorpay") == "Razorpay"


def test_clean_boundary_comma():
    assert clean_company_name("Verse8, a gaming startup") == "Verse8"


def test_clean_boundary_dash():
    assert clean_company_name("Verse8 — formerly GameCo") == "Verse8"


def test_clean_already_clean():
    assert clean_company_name("Uniswap") == "Uniswap"


def test_clean_exclusive_prefix():
    assert clean_company_name("Exclusive: MoonPay") == "MoonPay"


def test_clean_preserves_multiword_names():
    assert clean_company_name("Circle Internet Financial") == "Circle Internet Financial"


def _make_raw(**kwargs) -> RawRound:
    defaults = {
        "project_name": f"TestProject-{uuid.uuid4().hex[:8]}",
        "date": date(2024, 6, 15),
        "amount_usd": 5_000_000,
        "lead_investors": ["Sequoia"],
        "other_investors": ["Paradigm"],
        "source_url": "https://example.com",
        "sector": "defi",
        "chains": ["ethereum"],
    }
    defaults.update(kwargs)
    return RawRound(**defaults)


@pytest.mark.asyncio
async def test_ingest_creates_project_and_investors(db_session):
    raw = _make_raw()
    result = await ingest_round(db_session, raw, "defillama")
    assert result is not None

    # Check project was created
    proj = (await db_session.execute(
        select(Project).where(Project.name == raw.project_name)
    )).scalar_one_or_none()
    assert proj is not None

    # Check investors were created
    sequoia = (await db_session.execute(
        select(Investor).where(Investor.slug == "sequoia")
    )).scalar_one_or_none()
    assert sequoia is not None

    paradigm = (await db_session.execute(
        select(Investor).where(Investor.slug == "paradigm")
    )).scalar_one_or_none()
    assert paradigm is not None


@pytest.mark.asyncio
async def test_duplicate_detection(db_session):
    raw = _make_raw(project_name="DupTest")
    first = await ingest_round(db_session, raw, "defillama")
    assert first is not None

    second = await ingest_round(db_session, raw, "defillama")
    assert second is None


@pytest.mark.asyncio
async def test_investor_dedup_lead_wins(db_session):
    raw = _make_raw(
        project_name=f"LeadTest-{uuid.uuid4().hex[:8]}",
        lead_investors=["Paradigm"],
        other_investors=["Paradigm"],  # same investor in both
    )
    await ingest_round(db_session, raw, "defillama")

    # Should only have one RoundInvestor entry for Paradigm, and it should be lead
    paradigm = (await db_session.execute(
        select(Investor).where(Investor.slug == "paradigm")
    )).scalar_one()

    round_ = (await db_session.execute(
        select(Round).where(Round.sector == "defi")
    )).scalars().all()

    # Find the round for this test
    for r in round_:
        ri = (await db_session.execute(
            select(RoundInvestor).where(
                RoundInvestor.round_id == r.id,
                RoundInvestor.investor_id == paradigm.id,
            )
        )).scalar_one_or_none()
        if ri:
            assert ri.is_lead is True
            break


@pytest.mark.asyncio
async def test_confidence_stored(db_session):
    raw = _make_raw(project_name=f"ConfTest-{uuid.uuid4().hex[:8]}")
    await ingest_round(db_session, raw, "defillama")

    round_ = (await db_session.execute(
        select(Round).order_by(Round.created_at.desc()).limit(1)
    )).scalar_one()
    assert round_.confidence > 0.0
    assert round_.confidence <= 1.0
