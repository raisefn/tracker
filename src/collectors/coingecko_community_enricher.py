"""Enrich projects with CoinGecko community data (Twitter, Telegram)."""

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


class CoinGeckoCommunityEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "coingecko_community"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        projects = (
            await session.execute(
                select(Project).where(Project.coingecko_id.isnot(None))
            )
        ).scalars().all()

        if not projects:
            logger.info("No projects with coingecko_id found.")
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
                            "market_data": "false",
                            "community_data": "true",
                            "developer_data": "false",
                        },
                    )

                    delay = 2.5 if settings.coingecko_api_key else 7.0

                    if resp.status_code == 429:
                        logger.warning("CoinGecko rate limited, waiting 60s")
                        await asyncio.sleep(60)
                        continue

                    if resp.status_code == 404:
                        result.records_skipped += 1
                        await asyncio.sleep(delay)
                        continue

                    resp.raise_for_status()
                    data = resp.json()

                    community = data.get("community_data", {})
                    updated = False

                    twitter_followers = community.get("twitter_followers")
                    if twitter_followers and twitter_followers > 0:
                        project.twitter_followers = twitter_followers
                        updated = True

                    telegram_members = community.get("telegram_channel_user_count")
                    if telegram_members and telegram_members > 0:
                        project.telegram_members = telegram_members
                        updated = True

                    # Also grab contract address if we don't have it
                    if not project.token_contract:
                        platforms = data.get("platforms", {})
                        eth_contract = platforms.get("ethereum")
                        if eth_contract:
                            project.token_contract = eth_contract
                            updated = True

                    if updated:
                        project.last_enriched_at = datetime.now(timezone.utc)
                        stamp_freshness(project, self.source_name())
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1

                    await asyncio.sleep(delay)

                except httpx.HTTPStatusError as e:
                    error_msg = (
                        f"CoinGecko community error for "
                        f"{project.coingecko_id}: {e.response.status_code}"
                    )
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1
                except Exception as e:
                    error_msg = f"Error enriching community {project.coingecko_id}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"CoinGecko community enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result
