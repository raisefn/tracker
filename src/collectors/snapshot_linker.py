"""Link projects to Snapshot governance spaces by matching names.

Uses the Snapshot GraphQL API to fetch all spaces with >100 followers,
then fuzzy-matches against project names. This unlocks governance metrics
(proposal count, voter count, activity).
"""

import logging
from datetime import datetime, timezone

import httpx
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

SNAPSHOT_GRAPHQL_URL = "https://hub.snapshot.org/graphql"

SPACES_QUERY = """
query {
  spaces(
    first: 1000,
    skip: 0,
    orderBy: "followersCount",
    orderDirection: desc
  ) {
    id
    name
    followersCount
  }
}
"""


class SnapshotLinker(BaseEnricher):
    def source_name(self) -> str:
        return "snapshot_linker"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Fetch all active Snapshot spaces
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                SNAPSHOT_GRAPHQL_URL,
                json={"query": SPACES_QUERY},
            )
            resp.raise_for_status()
            data = resp.json()

        spaces = data.get("data", {}).get("spaces", [])
        logger.info(f"Snapshot spaces with 100+ followers: {len(spaces)}")

        if not spaces:
            return result

        # Build lookup maps: slug → space, name_lower → space
        slug_map: dict[str, dict] = {}
        name_map: dict[str, dict] = {}
        for space in spaces:
            space_id = space.get("id", "")
            space_name = space.get("name", "")

            # Snapshot IDs are often like "aave.eth", "uniswap"
            base_id = space_id.replace(".eth", "").replace(".ens", "").lower()
            if base_id:
                slug_map[base_id] = space

            # Also map by slug of name
            name_slug = slugify(space_name, max_length=200)
            if name_slug:
                slug_map[name_slug] = space

            name_lower = space_name.lower().strip()
            if name_lower:
                name_map[name_lower] = space

        # Get projects without snapshot_space
        projects = (
            await session.execute(
                select(Project).where(Project.snapshot_space.is_(None))
            )
        ).scalars().all()

        logger.info(f"Projects without snapshot_space: {len(projects)}")

        linked = 0
        for project in projects:
            space = self._match_project(project, slug_map, name_map)
            if space:
                project.snapshot_space = space["id"]
                stamp_freshness(project, self.source_name())
                linked += 1
                result.records_updated += 1
            else:
                result.records_skipped += 1

        await session.flush()
        logger.info(f"Snapshot linker: {linked} projects linked to governance spaces")
        return result

    def _match_project(
        self,
        project: Project,
        slug_map: dict[str, dict],
        name_map: dict[str, dict],
    ) -> dict | None:
        """Try to match a project to a Snapshot space."""
        # 1. Try slug match
        space = slug_map.get(project.slug)
        if space:
            return space

        # 2. Try name match
        name_lower = project.name.lower().strip()
        space = name_map.get(name_lower)
        if space:
            return space

        # 3. Try with common suffixes stripped
        for suffix in [" protocol", " finance", " network", " dao", " labs", " io"]:
            if name_lower.endswith(suffix):
                base = name_lower[:-len(suffix)].strip()
                if base:
                    space = name_map.get(base)
                    if space:
                        return space
                    space = slug_map.get(base)
                    if space:
                        return space

        return None
