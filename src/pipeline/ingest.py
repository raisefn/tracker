"""Main ingestion pipeline: collect → normalize → validate → dedup → store."""

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.api.cache import invalidate_all
from src.collectors.base import BaseCollector, RawRound
from src.db.redis import get_redis_client
from src.models import CollectorRun, Founder, Investor, Project, Round, RoundInvestor
from src.collectors.news_parser import clean_company_name
from src.pipeline.entity_resolver import resolve_investor_name
from src.pipeline.webhook_dispatch import dispatch_event
from src.pipeline.normalizer import make_slug, normalize_round
from src.pipeline.validator import CORROBORATION_BOOST, MAX_CORROBORATION_BOOST, compute_confidence, validate_round

logger = logging.getLogger(__name__)


async def get_or_create_project(session: AsyncSession, name: str, raw: RawRound) -> Project:
    name = clean_company_name(name)
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
        # Set extended fields from raw_data if present
        rd = raw.raw_data or {}
        if rd.get("accelerator"):
            project.accelerator = rd["accelerator"]
        if rd.get("accelerator_batch"):
            project.accelerator_batch = rd["accelerator_batch"]
        if rd.get("one_liner"):
            project.one_liner = rd["one_liner"]
        if rd.get("team_size"):
            project.team_size = rd["team_size"]
        if rd.get("location"):
            project.location = rd["location"]
        if rd.get("cik"):
            project.sec_cik = rd["cik"]
        if rd.get("accession_number"):
            project.sec_accession_number = rd["accession_number"]
        if rd.get("state"):
            project.sec_state = rd["state"]
        if rd.get("industry_group"):
            project.sec_industry_group = rd["industry_group"]
        if rd.get("revenue_range"):
            project.sec_revenue_range = rd["revenue_range"]
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


async def find_existing_round(
    session: AsyncSession, project_id, round_date, amount_usd
) -> Round | None:
    """Find an existing round that matches (exact or fuzzy).

    Returns the existing Round or None.
    """
    # 1. Exact match: same project, date, amount
    stmt = select(Round).where(
        Round.project_id == project_id,
        Round.date == round_date,
    )
    if amount_usd is not None:
        stmt = stmt.where(Round.amount_usd == amount_usd)
    existing = (await session.execute(
        stmt.options(selectinload(Round.investor_participations))
    )).scalar_one_or_none()
    if existing:
        return existing

    # 2. Fuzzy match: same project, date ±7 days, amount ±20%
    if amount_usd is not None:
        stmt = (
            select(Round)
            .where(
                Round.project_id == project_id,
                Round.date.between(
                    round_date - timedelta(days=7),
                    round_date + timedelta(days=7),
                ),
                Round.amount_usd.between(
                    int(amount_usd * 0.8),
                    int(amount_usd * 1.2),
                ),
            )
            .options(selectinload(Round.investor_participations))
            .limit(1)
        )
        existing = (await session.execute(stmt)).scalar_one_or_none()
        if existing:
            return existing

    return None


async def _merge_into_existing(
    session: AsyncSession, existing: Round, raw: RawRound, source_type: str
) -> None:
    """Merge a duplicate round into an existing one: boost confidence, add investors."""
    # Boost confidence from corroboration
    # Create a new dict to ensure SQLAlchemy detects the JSONB mutation
    raw_data = dict(existing.raw_data) if existing.raw_data else {}
    sources = list(raw_data.get("corroborating_sources", []))
    if source_type not in sources:
        sources.append(source_type)
        raw_data["corroborating_sources"] = sources
        existing.raw_data = raw_data

        existing.confidence = min(1.0, existing.confidence + CORROBORATION_BOOST)

    # Add any new investors not already on this round
    existing_inv_ids = {ri.investor_id for ri in existing.investor_participations}

    for inv_name in raw.lead_investors:
        investor = await get_or_create_investor(session, inv_name)
        if investor.id not in existing_inv_ids:
            existing_inv_ids.add(investor.id)
            session.add(RoundInvestor(
                round_id=existing.id, investor_id=investor.id, is_lead=True,
            ))

    for inv_name in raw.other_investors:
        investor = await get_or_create_investor(session, inv_name)
        if investor.id not in existing_inv_ids:
            existing_inv_ids.add(investor.id)
            session.add(RoundInvestor(
                round_id=existing.id, investor_id=investor.id, is_lead=False,
            ))

    # Fill in missing fields from the new source
    if raw.valuation_usd and not existing.valuation_usd:
        existing.valuation_usd = raw.valuation_usd
    if raw.round_type and not existing.round_type:
        existing.round_type = raw.round_type
    if raw.source_url and not existing.source_url:
        existing.source_url = raw.source_url


async def ingest_round(
    session: AsyncSession, raw: RawRound, source_type: str
) -> dict | None:
    """Ingest a single round. Returns event data dict if new, None if duplicate."""
    raw = normalize_round(raw)
    failures = validate_round(raw)
    confidence = compute_confidence(raw, source_type, failures)

    project = await get_or_create_project(session, raw.project_name, raw)

    existing = await find_existing_round(session, project.id, raw.date, raw.amount_usd)
    if existing:
        await _merge_into_existing(session, existing, raw, source_type)
        return None

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

    # Deduplicate investors: if someone is in both lead and other, lead wins
    seen_slugs: set[str] = set()

    for inv_name in raw.lead_investors:
        investor = await get_or_create_investor(session, inv_name)
        if investor.slug not in seen_slugs:
            seen_slugs.add(investor.slug)
            session.add(RoundInvestor(round_id=round_record.id, investor_id=investor.id, is_lead=True))

    for inv_name in raw.other_investors:
        investor = await get_or_create_investor(session, inv_name)
        if investor.slug not in seen_slugs:
            seen_slugs.add(investor.slug)
            session.add(RoundInvestor(round_id=round_record.id, investor_id=investor.id, is_lead=False))

    # Create founder records if provided
    if raw.founders:
        await _ingest_founders(session, project, raw.founders, source_type)

    return {
        "round_id": str(round_record.id),
        "project": project.name,
        "round_type": raw.round_type,
        "amount_usd": raw.amount_usd,
        "date": str(raw.date),
        "source_type": source_type,
    }


async def _ingest_founders(
    session: AsyncSession, project: Project, founders: list, source_type: str
) -> None:
    """Create Founder records, skipping duplicates by slug within the project."""
    # Get existing founder slugs for this project
    existing = await session.execute(
        select(Founder.slug).where(Founder.project_id == project.id)
    )
    existing_slugs = {row[0] for row in existing.all()}

    for raw_founder in founders:
        slug = make_slug(raw_founder.name)
        if slug in existing_slugs:
            continue
        existing_slugs.add(slug)

        session.add(Founder(
            project_id=project.id,
            name=raw_founder.name,
            slug=slug,
            role=raw_founder.role,
            linkedin=raw_founder.linkedin,
            twitter=raw_founder.twitter,
            github=raw_founder.github,
            source=source_type,
        ))


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
        new_round_events: list[dict] = []

        for raw in raw_rounds:
            try:
                # Use savepoint so one failed round doesn't kill the batch
                async with session.begin_nested():
                    event_data = await ingest_round(session, raw, collector.source_type())
                    if event_data:
                        new_count += 1
                        new_round_events.append(event_data)
            except Exception as e:
                flagged_count += 1
                logger.warning(f"Failed to ingest round {raw.project_name}: {e}")

        run.rounds_new = new_count
        run.rounds_flagged = flagged_count
        run.completed_at = datetime.now()
        await session.commit()

        # Invalidate cached API responses after new data
        r = get_redis_client()
        try:
            await invalidate_all(r)
        finally:
            await r.aclose()

        # Dispatch webhook events for new rounds
        for event_data in new_round_events:
            try:
                await dispatch_event(session, "round.created", event_data)
            except Exception as e:
                logger.warning(f"Webhook dispatch failed: {e}")

    except Exception as e:
        run.errors = {"error": str(e)}
        run.completed_at = datetime.now()
        await session.commit()
        raise

    return run
