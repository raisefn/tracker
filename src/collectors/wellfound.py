"""Wellfound (formerly AngelList) enricher.

Enriches existing projects with team and funding data from
Wellfound's public company profiles.
"""

import asyncio
import logging
import re

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

WELLFOUND_BASE = "https://wellfound.com/company"


class WellfoundEnricher(BaseEnricher):
    """Enrich projects with data from Wellfound public profiles."""

    def source_name(self) -> str:
        return "wellfound"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source="wellfound")

        # Get projects that haven't been enriched by wellfound recently
        projects = (
            await session.execute(
                select(Project)
                .where(Project.status == "active")
                .limit(100)
            )
        ).scalars().all()

        async with httpx.AsyncClient(
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; raisefn/tracker)",
                "Accept": "text/html",
            },
            follow_redirects=True,
        ) as client:
            for project in projects:
                # Skip if recently enriched
                freshness = project.source_freshness or {}
                if "wellfound" in freshness:
                    result.records_skipped += 1
                    continue

                slug = project.slug.replace("_", "-")
                try:
                    resp = await client.get(f"{WELLFOUND_BASE}/{slug}")
                    if resp.status_code == 404:
                        result.records_skipped += 1
                        continue
                    if resp.status_code != 200:
                        result.records_skipped += 1
                        continue

                    updated = self._extract_data(project, resp.text)
                    if updated:
                        stamp_freshness(project, "wellfound")
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1

                except Exception as e:
                    result.errors.append(f"{project.slug}: {e}")

                await asyncio.sleep(2)  # Rate limiting

        return result

    def _extract_data(self, project: Project, html: str) -> bool:
        """Extract company data from Wellfound HTML page."""
        updated = False

        # Try to extract description from meta tags
        desc_match = re.search(
            r'<meta\s+(?:name="description"|property="og:description")\s+content="([^"]+)"',
            html,
        )
        if desc_match and not project.description:
            project.description = desc_match.group(1).strip()
            updated = True

        # Extract team size from structured data
        size_match = re.search(r'"numberOfEmployees":\s*"?(\d+)"?', html)
        if size_match and not project.team_size:
            try:
                project.team_size = int(size_match.group(1))
                updated = True
            except ValueError:
                pass

        # Extract location
        location_match = re.search(
            r'"address":\s*\{[^}]*"addressLocality":\s*"([^"]+)"', html
        )
        if location_match and not project.location:
            project.location = location_match.group(1).strip()
            updated = True

        return updated
