"""Enrich projects with Reddit community data."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)


class RedditEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "reddit"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(select(Project))
        ).scalars().all()

        if not projects:
            return result

        headers = {"User-Agent": "raisefn-tracker/1.0 (startup research)"}

        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            for project in projects:
                try:
                    subreddit = project.reddit_subreddit
                    if not subreddit:
                        subreddit = await self._find_subreddit(client, project)
                        if not subreddit:
                            result.records_skipped += 1
                            continue

                    data = await self._fetch_subreddit(client, subreddit)
                    if not data:
                        result.records_skipped += 1
                        continue

                    project.reddit_subreddit = subreddit
                    project.reddit_subscribers = data["subscribers"]
                    project.reddit_active_users = data["active_users"]
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(2.0)  # Reddit rate limits aggressively

                except Exception as e:
                    error_msg = f"Reddit error for {project.slug}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"Reddit enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _find_subreddit(self, client: httpx.AsyncClient, project: Project) -> str | None:
        """Try common subreddit names for a project."""
        # Try slug, name, name without spaces
        candidates = [
            project.slug,
            project.name.lower().replace(" ", ""),
            project.name.replace(" ", ""),
        ]
        # Try with common suffixes removed
        for base in [project.slug, project.name.lower()]:
            for suffix in [" protocol", "protocol", " labs", "labs", " ai", " io", " app"]:
                cleaned = base.replace(suffix, "").strip()
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)

        seen = set()
        for candidate in candidates:
            candidate = candidate.strip().lower()
            if candidate in seen or len(candidate) < 2:
                continue
            seen.add(candidate)

            data = await self._fetch_subreddit(client, candidate)
            if data and data["subscribers"] > 100:
                return candidate
            await asyncio.sleep(1.5)

        return None

    async def _fetch_subreddit(self, client: httpx.AsyncClient, subreddit: str) -> dict | None:
        """Fetch subreddit about data."""
        resp = await client.get(
            f"https://www.reddit.com/r/{subreddit}/about.json",
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return None

        data = resp.json().get("data", {})
        subscribers = data.get("subscribers")
        if subscribers is None:
            return None

        return {
            "subscribers": subscribers,
            "active_users": data.get("accounts_active", 0) or 0,
        }
