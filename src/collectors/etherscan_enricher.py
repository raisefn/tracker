"""Enrich projects with Etherscan token holder data."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.config import settings
from src.models import Project

logger = logging.getLogger(__name__)

ETHERSCAN_API_BASE = "https://api.etherscan.io/api"


class EtherscanEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "etherscan"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        if not settings.etherscan_api_key:
            logger.info("No ETHERSCAN_API_KEY set, skipping Etherscan enrichment")
            return result

        # Only enrich projects with a known Ethereum token contract
        projects = (
            await session.execute(
                select(Project).where(Project.token_contract.isnot(None))
            )
        ).scalars().all()

        if not projects:
            logger.info(
                "No projects with token_contract found."
                " Run CoinGecko community enricher first."
            )
            return result

        async with httpx.AsyncClient(timeout=15.0) as client:
            for project in projects:
                try:
                    holder_count = await self._fetch_holder_count(
                        client, project.token_contract
                    )

                    if holder_count is None:
                        result.records_skipped += 1
                        continue

                    project.token_holder_count = holder_count
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    await asyncio.sleep(0.25)  # 5 calls/sec

                except Exception as e:
                    error_msg = (
                        f"Etherscan error for "
                        f"{project.slug} ({project.token_contract}): {e}"
                    )
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"Etherscan enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _fetch_holder_count(self, client: httpx.AsyncClient, contract: str) -> int | None:
        """Fetch token holder count from Etherscan."""
        resp = await client.get(
            ETHERSCAN_API_BASE,
            params={
                "module": "token",
                "action": "tokeninfo",
                "contractaddress": contract,
                "apikey": settings.etherscan_api_key,
            },
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("status") != "1":
            # Try alternative: token holder list page count
            return await self._fetch_holder_count_alt(client, contract)

        result_data = data.get("result", [])
        if isinstance(result_data, list) and result_data:
            holder_count = result_data[0].get("holdersCount")
            if holder_count:
                return int(holder_count)

        return await self._fetch_holder_count_alt(client, contract)

    async def _fetch_holder_count_alt(self, client: httpx.AsyncClient, contract: str) -> int | None:
        """Alternative: use tokentx to estimate holder count."""
        # Use token supply as a fallback signal
        resp = await client.get(
            ETHERSCAN_API_BASE,
            params={
                "module": "stats",
                "action": "tokensupply",
                "contractaddress": contract,
                "apikey": settings.etherscan_api_key,
            },
        )
        if resp.status_code != 200:
            return None

        data = resp.json()
        if data.get("status") == "1" and data.get("result"):
            # We got supply but not holder count — return None
            # rather than a misleading number
            return None

        return None
