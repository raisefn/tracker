"""Enrich projects with Hacker News mention data."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.models import Project

logger = logging.getLogger(__name__)

HN_ALGOLIA_API = "https://hn.algolia.com/api/v1/search"


class HackerNewsEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "hackernews"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(select(Project))
        ).scalars().all()

        if not projects:
            return result

        ninety_days_ago = int((datetime.now(timezone.utc) - timedelta(days=90)).timestamp())

        async with httpx.AsyncClient(timeout=15.0) as client:
            for project in projects:
                try:
                    # Search for project name on HN
                    mentions, points = await self._search_hn(
                        client, project.name, ninety_days_ago
                    )

                    if mentions == 0:
                        result.records_skipped += 1
                        continue

                    project.hn_mentions_90d = mentions
                    project.hn_total_points = points
                    project.last_enriched_at = datetime.now(timezone.utc)
                    result.records_updated += 1

                    await asyncio.sleep(0.5)  # 10,000 req/hr = generous

                except Exception as e:
                    error_msg = f"HN error for {project.slug}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"HackerNews enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _search_hn(
        self, client: httpx.AsyncClient, name: str, since_ts: int
    ) -> tuple[int, int]:
        """Search HN for a project name, return (mention_count, total_points)."""
        # Use quoted search for multi-word names to reduce false positives
        query = f'"{name}"' if " " in name else name

        resp = await client.get(
            HN_ALGOLIA_API,
            params={
                "query": query,
                "tags": "story",
                "numericFilters": f"created_at_i>{since_ts}",
                "hitsPerPage": 100,
            },
        )
        if resp.status_code != 200:
            return 0, 0

        data = resp.json()
        hits = data.get("hits", [])

        # Filter to relevant hits — name must appear in title
        name_lower = name.lower()
        relevant = [h for h in hits if name_lower in h.get("title", "").lower()]

        if not relevant:
            return 0, 0

        total_points = sum(h.get("points", 0) or 0 for h in relevant)
        return len(relevant), total_points
