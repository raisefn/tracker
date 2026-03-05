"""Main ingestion pipeline: collect → normalize → validate → dedup → store."""

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.base import BaseCollector, RawRound
from src.models import CollectorRun, Investor, Project, Round, RoundInvestor
from src.pipeline.entity_resolver import resolve_investor_name
from src.pipeline.normalizer import make_slug, normalize_round
from src.pipeline.validator import compute_confidence, validate_round

logger = logging.getLogger(__name__)


async def get_or_create_project(session: AsyncSession, name: str, raw: RawRound) -> Project:
    slug = make_slug(name)
    result = await session.execute(select(Project).where(Project.slug == slug))
    project = result.scalar_one_or_none()
    if project is None:
        project = Project(
            name=name,
            slug=slug,
            website=raw.project_url,
            sector=raw.sector,
            chains=raw.chains or None,
        )
        session.add(project)
        await session.flush()
    return project


async def get_or_create_investor(session: AsyncSession, name: str) -> Investor:
    canonical = resolve_investor_name(name)
    slug = make_slug(canonical)
    result = await session.execute(select(Investor).where(Investor.slug == slug))
    investor = result.scalar_one_or_none()
    if investor is None:
        investor = Investor(name=canonical, slug=slug)
        session.add(investor)
        await session.flush()
    return investor


async def is_duplicate(session: AsyncSession, project_id, round_date, amount_usd) -> bool:
    """Check if a round already exists (same project, date, amount)."""
    stmt = select(Round).where(
        Round.project_id == project_id,
        Round.date == round_date,
    )
    if amount_usd is not None:
        stmt = stmt.where(Round.amount_usd == amount_usd)
    result = await session.execute(stmt)
    return result.scalar_one_or_none() is not None


async def ingest_round(session: AsyncSession, raw: RawRound, source_type: str) -> bool:
    """Ingest a single round. Returns True if new round was created."""
    raw = normalize_round(raw)
    failures = validate_round(raw)
    confidence = compute_confidence(raw, source_type, failures)

    project = await get_or_create_project(session, raw.project_name, raw)

    if await is_duplicate(session, project.id, raw.date, raw.amount_usd):
        return False

    round_record = Round(
        project_id=project.id,
        round_type=raw.round_type,
        amount_usd=raw.amount_usd,
        valuation_usd=raw.valuation_usd,
        date=raw.date,
        chains=raw.chains or None,
        sector=raw.sector,
        category=raw.category,
        source_url=raw.source_url,
        source_type=source_type,
        raw_data=raw.raw_data,
        confidence=confidence,
        validation_failures={"failures": failures} if failures else None,
    )
    session.add(round_record)
    await session.flush()

    # Link investors
    for inv_name in raw.lead_investors:
        investor = await get_or_create_investor(session, inv_name)
        session.add(RoundInvestor(round_id=round_record.id, investor_id=investor.id, is_lead=True))

    for inv_name in raw.other_investors:
        investor = await get_or_create_investor(session, inv_name)
        session.add(
            RoundInvestor(round_id=round_record.id, investor_id=investor.id, is_lead=False)
        )

    return True


async def run_collector(session: AsyncSession, collector: BaseCollector) -> CollectorRun:
    """Run a collector and ingest all results."""
    run = CollectorRun(collector=collector.source_type())
    session.add(run)
    await session.flush()

    try:
        raw_rounds = await collector.collect()
        run.rounds_fetched = len(raw_rounds)

        new_count = 0
        flagged_count = 0

        for raw in raw_rounds:
            try:
                is_new = await ingest_round(session, raw, collector.source_type())
                if is_new:
                    new_count += 1
            except Exception as e:
                flagged_count += 1
                logger.warning(f"Failed to ingest round {raw.project_name}: {e}")

        run.rounds_new = new_count
        run.rounds_flagged = flagged_count
        run.completed_at = datetime.now()
        await session.commit()

    except Exception as e:
        run.errors = {"error": str(e)}
        run.completed_at = datetime.now()
        await session.commit()
        raise

    return run
