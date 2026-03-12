"""Enrich projects with Product Hunt launch data."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

# Product Hunt has a public embed/widget endpoint that returns basic post data
PH_API = "https://www.producthunt.com/frontend/graphql"


class ProductHuntEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "producthunt"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(
                select(Project).where(Project.producthunt_slug.isnot(None))
            )
        ).scalars().all()

        if not projects:
            return result

        headers = {
            "User-Agent": "raisefn-tracker/1.0",
            "Accept": "text/html",
        }

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            for project in projects:
                try:
                    votes = await self._fetch_votes(client, project.producthunt_slug)
                    if votes is None:
                        result.records_skipped += 1
                        continue

                    project.producthunt_votes = votes
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(2.0)

                except Exception as e:
                    error_msg = (
                        f"ProductHunt error for "
                        f"{project.slug} ({project.producthunt_slug}): {e}"
                    )
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"ProductHunt enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _fetch_votes(self, client: httpx.AsyncClient, slug: str) -> int | None:
        """Fetch vote count by scraping the Product Hunt post page.

        Product Hunt's GraphQL API requires auth tokens, so we scrape the
        public post page and extract the vote count from meta tags.
        """
        resp = await client.get(
            f"https://www.producthunt.com/posts/{slug}",
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        # Look for vote count in the page — PH embeds it in JSON-LD or meta tags
        text = resp.text

        # Try extracting from JSON-LD structured data
        import re

        # Look for "votesCount":N pattern in embedded JSON
        match = re.search(r'"votesCount"\s*:\s*(\d+)', text)
        if match:
            return int(match.group(1))

        # Fallback: look for upvote count in data attributes
        match = re.search(r'data-test="vote-button"[^>]*>(\d+)', text)
        if match:
            return int(match.group(1))

        return None
