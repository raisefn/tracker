"""Enrich projects with market data from CoinGecko."""

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

COINGECKO_API_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "coingecko"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Only enrich projects that have a coingecko_id (set by DefiLlama enricher)
        projects = (
            await session.execute(
                select(Project).where(Project.coingecko_id.isnot(None))
            )
        ).scalars().all()

        if not projects:
            logger.info("No projects with coingecko_id found. Run DefiLlama enricher first.")
            return result

        headers = {}
        if settings.coingecko_api_key:
            headers["x-cg-demo-api-key"] = settings.coingecko_api_key

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            for project in projects:
                try:
                    resp = await client.get(
                        f"{COINGECKO_API_BASE}/coins/{project.coingecko_id}",
                        params={
                            "localization": "false",
                            "tickers": "false",
                            "market_data": "true",
                            "community_data": "false",
                            "developer_data": "false",
                        },
                    )

                    if resp.status_code == 429:
                        logger.warning("CoinGecko rate limited, stopping enrichment")
                        result.errors.append("Rate limited by CoinGecko")
                        break

                    if resp.status_code == 404:
                        result.records_skipped += 1
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    # Update market data
                    market = data.get("market_data", {})
                    if market:
                        mcap = market.get("market_cap", {}).get("usd")
                        if mcap:
                            project.market_cap = int(mcap)
                        price = market.get("current_price", {}).get("usd")
                        if price:
                            project.token_price_usd = price

                    # Token symbol
                    symbol = data.get("symbol")
                    if symbol:
                        project.token_symbol = symbol.upper()

                    # Fill missing fields
                    if not project.description:
                        desc = data.get("description", {}).get("en", "")
                        if desc:
                            # Strip HTML tags from CoinGecko descriptions
                            import re
                            project.description = re.sub(r"<[^>]+>", "", desc)[:2000]

                    if not project.twitter:
                        twitter = data.get("links", {}).get("twitter_screen_name")
                        if twitter:
                            project.twitter = twitter

                    if not project.website:
                        homepage = data.get("links", {}).get("homepage", [])
                        if homepage and homepage[0]:
                            project.website = homepage[0]

                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                    # Rate limit: with API key 30 req/min, without ~10 req/min
                    delay = 2.5 if settings.coingecko_api_key else 7.0
                    await asyncio.sleep(delay)

                except httpx.HTTPStatusError as e:
                    error_msg = f"CoinGecko error for {project.coingecko_id}: {e.response.status_code}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1
                except Exception as e:
                    error_msg = f"Error enriching {project.coingecko_id}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"CoinGecko enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result
