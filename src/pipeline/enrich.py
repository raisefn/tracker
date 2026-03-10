"""Enrichment pipeline: run enrichers, snapshot metrics, and update records."""

import logging
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import invalidate_all
from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.db.redis import get_redis_client
from src.models import Project, ProjectMetricSnapshot

logger = logging.getLogger(__name__)

# Which metrics to snapshot per enricher source
SOURCE_METRIC_MAP: dict[str, list[str]] = {
    "defillama_protocols": ["tvl", "tvl_change_7d"],
    "coingecko": ["market_cap", "token_price_usd"],
    "github": ["github_stars", "github_commits_30d"],
    "reddit": ["reddit_subscribers"],
    "coingecko_community": ["twitter_followers", "telegram_members"],
    "etherscan": ["token_holder_count"],
    "snapshot": ["snapshot_proposals_count", "snapshot_voters_count", "snapshot_proposal_activity_30d"],
    "hackernews": ["hn_mentions_90d", "hn_total_points"],
    "npm": ["npm_downloads_monthly"],
    "pypi": ["pypi_downloads_monthly"],
    "producthunt": ["producthunt_votes"],
}


async def snapshot_metrics(session: AsyncSession, source: str) -> int:
    """Write a daily snapshot row for each project with data from this source."""
    fields = SOURCE_METRIC_MAP.get(source)
    if not fields:
        return 0

    today = date.today()
    projects = (
        await session.execute(select(Project).where(Project.last_enriched_at.isnot(None)))
    ).scalars().all()

    count = 0
    for project in projects:
        # Only one snapshot per project per source per day
        existing = await session.execute(
            select(ProjectMetricSnapshot.id)
            .where(
                ProjectMetricSnapshot.project_id == project.id,
                ProjectMetricSnapshot.source == source,
                ProjectMetricSnapshot.snapshotted_at >= datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc),
            )
            .limit(1)
        )
        if existing.scalar_one_or_none():
            continue

        metrics = {}
        for f in fields:
            val = getattr(project, f, None)
            if val is not None:
                metrics[f] = val
        if not metrics:
            continue

        session.add(ProjectMetricSnapshot(
            project_id=project.id,
            source=source,
            metrics=metrics,
        ))
        count += 1

    return count


async def run_enricher(session: AsyncSession, enricher: BaseEnricher) -> EnrichmentResult:
    """Run an enricher, snapshot metrics, commit changes, and invalidate cache."""
    result = await enricher.enrich(session)

    # Snapshot metrics before committing
    source = enricher.source_name()
    snapshot_count = await snapshot_metrics(session, source)
    if snapshot_count:
        logger.info(f"Snapshotted {snapshot_count} projects for {source}")

    await session.commit()

    # Invalidate cached API responses after enrichment
    r = get_redis_client()
    try:
        await invalidate_all(r)
    finally:
        await r.aclose()

    return result
