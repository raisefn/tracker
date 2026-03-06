"""Enrich projects with TVL and protocol data from DefiLlama."""

import logging
from datetime import datetime, timezone

import httpx
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.models import Project

logger = logging.getLogger(__name__)

DEFILLAMA_PROTOCOLS_URL = "https://api.llama.fi/protocols"


class DefiLlamaProtocolEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "defillama_protocols"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Fetch all protocols from DefiLlama
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(DEFILLAMA_PROTOCOLS_URL)
            resp.raise_for_status()
            protocols = resp.json()

        # Build lookup maps: slug → protocol, name_lower → protocol
        slug_map: dict[str, dict] = {}
        name_map: dict[str, dict] = {}
        for p in protocols:
            p_slug = slugify(p.get("name", ""), max_length=200)
            if p_slug:
                slug_map[p_slug] = p
            name_lower = p.get("name", "").lower().strip()
            if name_lower:
                name_map[name_lower] = p

        # Get all projects from DB
        projects = (await session.execute(select(Project))).scalars().all()

        for project in projects:
            # Try to match: first by existing defillama_slug, then our slug, then name
            protocol = None
            if project.defillama_slug:
                protocol = slug_map.get(project.defillama_slug)

            if protocol is None:
                protocol = slug_map.get(project.slug)

            if protocol is None:
                protocol = name_map.get(project.name.lower().strip())

            if protocol is None:
                result.records_skipped += 1
                continue

            # Update project with protocol data
            project.defillama_slug = slugify(protocol.get("name", ""), max_length=200)
            project.tvl = int(protocol.get("tvl", 0)) if protocol.get("tvl") else None
            project.tvl_change_7d = protocol.get("change_7d")

            # Set coingecko_id for downstream CoinGecko enrichment
            gecko_id = protocol.get("gecko_id")
            if gecko_id:
                project.coingecko_id = gecko_id

            # Set token symbol
            symbol = protocol.get("symbol")
            if symbol and symbol != "-":
                project.token_symbol = symbol

            # Fill in missing fields
            if not project.description and protocol.get("description"):
                project.description = protocol["description"]
            if not project.website and protocol.get("url"):
                project.website = protocol["url"]
            if not project.twitter and protocol.get("twitter"):
                project.twitter = protocol["twitter"]
            if not project.github and protocol.get("github"):
                # DefiLlama stores github as list of URLs
                gh = protocol["github"]
                if isinstance(gh, list) and gh:
                    project.github = gh[0]
                elif isinstance(gh, str):
                    project.github = gh

            # Merge chains
            protocol_chains = protocol.get("chains", [])
            if protocol_chains:
                existing = set(project.chains or [])
                merged = existing | {c.lower() for c in protocol_chains}
                project.chains = sorted(merged)

            project.last_enriched_at = datetime.now(timezone.utc)
            result.records_updated += 1

        await session.flush()
        logger.info(
            f"DefiLlama enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped"
        )
        return result
