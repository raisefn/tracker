"""Enrich projects with Snapshot governance data."""

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

SNAPSHOT_API = "https://hub.snapshot.org/graphql"


class SnapshotEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "snapshot"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(select(Project))
        ).scalars().all()

        if not projects:
            return result

        async with httpx.AsyncClient(timeout=30.0) as client:
            for project in projects:
                try:
                    # Try to find a Snapshot space matching project slug or name
                    space_id = project.snapshot_space
                    if not space_id:
                        space_id = await self._find_space(client, project)
                        if not space_id:
                            result.records_skipped += 1
                            continue

                    # Fetch space stats
                    stats = await self._fetch_space_stats(client, space_id)
                    if not stats:
                        result.records_skipped += 1
                        continue

                    project.snapshot_space = space_id
                    project.snapshot_proposals_count = stats["proposals_count"]
                    project.snapshot_voters_count = stats["voters_count"]
                    project.snapshot_proposal_activity_30d = stats["recent_proposals"]
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(2.0)  # Rate limit: stay well under 60 req/min

                except Exception as e:
                    error_msg = f"Snapshot error for {project.slug}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"Snapshot enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _find_space(self, client: httpx.AsyncClient, project: Project) -> str | None:
        """Try to find a Snapshot space for a project."""
        # Try common patterns: slug, name lowercase
        candidates = [project.slug, project.name.lower().replace(" ", "")]
        # Also try with .eth suffix
        candidates += [f"{c}.eth" for c in candidates]

        for candidate in candidates:
            query = """
            query($id: String!) {
                space(id: $id) {
                    id
                    name
                    proposalsCount
                }
            }
            """
            resp = await client.post(
                SNAPSHOT_API,
                json={"query": query, "variables": {"id": candidate}},
            )
            if resp.status_code == 429:
                logger.warning("Snapshot rate limited, waiting 30s")
                await asyncio.sleep(30)
                continue
            if resp.status_code == 200:
                data = resp.json().get("data", {}).get("space")
                if data and data.get("proposalsCount", 0) > 0:
                    return data["id"]
            await asyncio.sleep(2.0)

        return None

    async def _fetch_space_stats(self, client: httpx.AsyncClient, space_id: str) -> dict | None:
        """Fetch proposal count and voter stats for a space."""
        thirty_days_ago = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

        # Get total proposals and recent proposals
        query = """
        query($space: String!, $since: Int!) {
            proposals(where: { space: $space }, first: 1000, orderBy: "created", orderDirection: desc) {
                id
                created
                votes
            }
            recent: proposals(where: { space: $space, created_gte: $since }, first: 1000) {
                id
            }
        }
        """
        resp = await client.post(
            SNAPSHOT_API,
            json={"query": query, "variables": {"space": space_id, "since": thirty_days_ago}},
        )
        if resp.status_code == 429:
            logger.warning("Snapshot rate limited on stats fetch, waiting 30s")
            await asyncio.sleep(30)
            return None
        if resp.status_code != 200:
            return None

        data = resp.json().get("data", {})
        proposals = data.get("proposals", [])
        recent = data.get("recent", [])

        if not proposals:
            return None

        total_voters = sum(p.get("votes", 0) for p in proposals[:100])

        return {
            "proposals_count": len(proposals),
            "voters_count": total_voters,
            "recent_proposals": len(recent),
        }
