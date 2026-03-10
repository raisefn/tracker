"""Enrich projects with npm download stats."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

NPM_API = "https://api.npmjs.org/downloads/point/last-month"


class NpmEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "npm"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(
                select(Project).where(Project.npm_package.isnot(None))
            )
        ).scalars().all()

        if not projects:
            return result

        async with httpx.AsyncClient(timeout=15.0) as client:
            for project in projects:
                try:
                    downloads = await self._fetch_downloads(client, project.npm_package)
                    if downloads is None:
                        result.records_skipped += 1
                        continue

                    project.npm_downloads_monthly = downloads
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(0.5)

                except Exception as e:
                    error_msg = f"npm error for {project.slug} ({project.npm_package}): {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"npm enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _fetch_downloads(self, client: httpx.AsyncClient, package: str) -> int | None:
        """Fetch last-month download count for an npm package."""
        resp = await client.get(f"{NPM_API}/{package}")
        if resp.status_code != 200:
            return None

        data = resp.json()
        return data.get("downloads")
