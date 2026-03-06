"""Enrichment pipeline: run enrichers and update records."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.cache import invalidate_all
from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.db.redis import get_redis_client

logger = logging.getLogger(__name__)


async def run_enricher(session: AsyncSession, enricher: BaseEnricher) -> EnrichmentResult:
    """Run an enricher, commit changes, and invalidate cache."""
    result = await enricher.enrich(session)
    await session.commit()

    # Invalidate cached API responses after enrichment
    r = get_redis_client()
    try:
        await invalidate_all(r)
    finally:
        await r.aclose()

    return result
