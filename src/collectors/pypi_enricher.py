"""Enrich projects with PyPI download stats."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

# pypistats.org provides a free JSON API for download stats
PYPISTATS_API = "https://pypistats.org/api/packages"


class PyPIEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "pypi"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(
                select(Project).where(Project.pypi_package.isnot(None))
            )
        ).scalars().all()

        if not projects:
            return result

        async with httpx.AsyncClient(timeout=15.0) as client:
            for project in projects:
                try:
                    downloads = await self._fetch_downloads(client, project.pypi_package)
                    if downloads is None:
                        result.records_skipped += 1
                        continue

                    project.pypi_downloads_monthly = downloads
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(1.0)  # pypistats rate limits

                except Exception as e:
                    error_msg = f"PyPI error for {project.slug} ({project.pypi_package}): {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"PyPI enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _fetch_downloads(self, client: httpx.AsyncClient, package: str) -> int | None:
        """Fetch recent monthly download count for a PyPI package."""
        resp = await client.get(f"{PYPISTATS_API}/{package}/recent")
        if resp.status_code != 200:
            return None

        data = resp.json()
        return data.get("data", {}).get("last_month")
