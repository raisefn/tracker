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
from src.collectors.formd_promoters import FormDPromoterEnricher
from src.collectors.github_enricher import GitHubEnricher
from src.collectors.hackernews_enricher import HackerNewsEnricher
from src.collectors.npm_enricher import NpmEnricher
from src.collectors.producthunt_enricher import ProductHuntEnricher
from src.collectors.propublica_990 import ProPublica990Enricher
from src.collectors.pypi_enricher import PyPIEnricher
from src.collectors.reddit_enricher import RedditEnricher
from src.collectors.sec_13f import SEC13FEnricher
from src.collectors.sec_form_adv import SECFormADVEnricher
from src.collectors.snapshot_enricher import SnapshotEnricher
from src.collectors.wellfound import WellfoundEnricher
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
    "npm": NpmEnricher,
    "pypi": PyPIEnricher,
    "producthunt": ProductHuntEnricher,
    "sec_form_adv": SECFormADVEnricher,
    "sec_13f": SEC13FEnricher,
    "formd_promoters": FormDPromoterEnricher,
    "propublica_990": ProPublica990Enricher,
    "wellfound": WellfoundEnricher,
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
