"""Tests for Phase 2: better data in — founder pipeline, RSS investors, entity resolution."""

import uuid
from datetime import date

import pytest

from src.collectors.base import RawFounder, RawRound
from src.collectors.rss_funding import _extract_investors
from src.models import Founder, Project
from src.pipeline.entity_resolver import resolve_investor_name, _normalize


# --- RawFounder + ingest pipeline ---

@pytest.mark.asyncio
async def test_founders_ingested_from_raw_round(db_session):
    """Founders in RawRound should be persisted to the Founder table."""
    from src.pipeline.ingest import ingest_round

    raw = RawRound(
        project_name=f"FounderIngestTest-{uuid.uuid4().hex[:8]}",
        date=date(2024, 3, 1),
        amount_usd=5_000_000,
        round_type="seed",
        lead_investors=["Sequoia"],
        founders=[
            RawFounder(name="Alice Smith", role="CEO"),
            RawFounder(name="Bob Jones", role="CTO", linkedin="https://linkedin.com/in/bob"),
        ],
    )

    result = await ingest_round(db_session, raw, "manual")
    assert result is not None

    from sqlalchemy import select
    from src.pipeline.normalizer import make_slug

    slug = make_slug(raw.project_name)
    project = (await db_session.execute(
        select(Project).where(Project.slug == slug)
    )).scalar_one()

    founders = (await db_session.execute(
        select(Founder).where(Founder.project_id == project.id)
    )).scalars().all()

    assert len(founders) == 2
    names = {f.name for f in founders}
    assert names == {"Alice Smith", "Bob Jones"}
    ceo = next(f for f in founders if f.name == "Alice Smith")
    assert ceo.role == "CEO"
    assert ceo.source == "manual"


@pytest.mark.asyncio
async def test_duplicate_founders_not_created(db_session):
    """If a founder with the same slug already exists for a project, skip it."""
    from src.pipeline.ingest import ingest_round

    name = f"DupFounderTest-{uuid.uuid4().hex[:8]}"

    raw1 = RawRound(
        project_name=name, date=date(2024, 1, 1),
        amount_usd=1_000_000, lead_investors=["TestVC"],
        founders=[RawFounder(name="Jane Doe", role="CEO")],
    )
    await ingest_round(db_session, raw1, "manual")

    # Second round with same founder — should not duplicate
    raw2 = RawRound(
        project_name=name, date=date(2024, 6, 1),
        amount_usd=10_000_000, lead_investors=["TestVC"],
        founders=[RawFounder(name="Jane Doe", role="CEO")],
    )
    await ingest_round(db_session, raw2, "manual")

    from sqlalchemy import select
    from src.pipeline.normalizer import make_slug

    slug = make_slug(name)
    project = (await db_session.execute(
        select(Project).where(Project.slug == slug)
    )).scalar_one()

    founders = (await db_session.execute(
        select(Founder).where(Founder.project_id == project.id)
    )).scalars().all()

    assert len(founders) == 1


# --- RSS investor extraction ---

def test_extract_led_by():
    text = "Acme raises $10M Series A led by Sequoia Capital"
    leads, others = _extract_investors(text)
    assert leads == ["Sequoia Capital"]
    assert others == []


def test_extract_led_by_with_participation():
    text = "Acme raises $10M led by Paradigm with participation from Coinbase Ventures, a16z, and Multicoin Capital."
    leads, others = _extract_investors(text)
    assert leads == ["Paradigm"]
    assert "Coinbase Ventures" in others
    assert "a16z" in others
    assert "Multicoin Capital" in others


def test_extract_backed_by():
    text = "Acme secures $5M backed by Tiger Global and Accel."
    leads, others = _extract_investors(text)
    assert "Tiger Global" in others or "Tiger Global" in leads


def test_no_investors_extracted():
    text = "Acme raises $10M to expand operations"
    leads, others = _extract_investors(text)
    assert leads == []
    assert others == []


# --- Entity resolution improvements ---

def test_multi_suffix_stripping():
    assert _normalize("Foo Capital Management LLC") == "Foo"
    assert _normalize("Bar Venture Partners Inc.") == "Bar"
    assert _normalize("Baz Holdings Group Ltd.") == "Baz"


def test_resolve_with_fuzzy_canonical():
    # "Seqoia" (typo) should fuzzy match to "Sequoia" from canonical list
    result = resolve_investor_name("Seqoia")
    assert result == "Sequoia"


def test_resolve_new_aliases():
    assert resolve_investor_name("LSVP") == "Lightspeed"
    assert resolve_investor_name("Altimeter Capital") == "Altimeter"
    assert resolve_investor_name("Dragonfly Capital Partners") == "Dragonfly"


def test_resolve_suffix_stripped_then_matched():
    # "Paradigm Capital Management LLC" → strip to "Paradigm" → match canonical
    result = resolve_investor_name("Paradigm Capital Management LLC")
    assert result == "Paradigm"
