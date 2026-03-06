"""Run enrichment collectors to update project data.

Usage:
    python -m scripts.run_enrichers                    # run all enrichers
    python -m scripts.run_enrichers defillama_protocols # run specific enricher
    python -m scripts.run_enrichers coingecko github   # run multiple
"""

import asyncio
import logging
import sys

from src.collectors.coingecko_community_enricher import CoinGeckoCommunityEnricher
from src.collectors.coingecko_enricher import CoinGeckoEnricher
from src.collectors.defillama_enricher import DefiLlamaProtocolEnricher
from src.collectors.etherscan_enricher import EtherscanEnricher
from src.collectors.github_enricher import GitHubEnricher
from src.collectors.hackernews_enricher import HackerNewsEnricher
from src.collectors.reddit_enricher import RedditEnricher
from src.collectors.snapshot_enricher import SnapshotEnricher
from src.db.session import async_session
from src.pipeline.enrich import run_enricher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ENRICHERS = {
    "defillama_protocols": DefiLlamaProtocolEnricher,
    "coingecko": CoinGeckoEnricher,
    "github": GitHubEnricher,
    "snapshot": SnapshotEnricher,
    "reddit": RedditEnricher,
    "hackernews": HackerNewsEnricher,
    "coingecko_community": CoinGeckoCommunityEnricher,
    "etherscan": EtherscanEnricher,
}


async def main():
    requested = sys.argv[1:] if len(sys.argv) > 1 else list(ENRICHERS.keys())

    for name in requested:
        if name not in ENRICHERS:
            logger.error(f"Unknown enricher: {name}. Available: {', '.join(ENRICHERS.keys())}")
            continue

        enricher = ENRICHERS[name]()
        logger.info(f"Running enricher: {name}")

        async with async_session() as session:
            result = await run_enricher(session, enricher)

        logger.info(
            f"  {name}: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        if result.errors:
            for err in result.errors[:5]:
                logger.warning(f"  Error: {err}")


if __name__ == "__main__":
    asyncio.run(main())
