"""Tiered scheduler for data collection and enrichment.

Runs collectors and enrichers at different frequencies:
- Real-time (every 15 min): RSS feeds, Google News
- Hourly: SEC EDGAR recent filings, HackerNews, Reddit
- Daily: GitHub, npm/PyPI, Product Hunt, YC directory, Form D promoters,
         DefiLlama, CoinGecko chain, Snapshot, Etherscan
- Weekly: Accelerator scrapes, SEC investor intelligence, 990s,
          SEC EDGAR bulk, OpenVC, Wellfound, AngelList investors, Crunchbase,
          Angel groups, VC website scraping, PitchBook News
"""

import asyncio
import logging
from datetime import datetime

from src.collectors.accelerator_500 import FiveHundredGlobalCollector
from src.collectors.accelerator_directory import AcceleratorDirectoryCollector
from src.collectors.angel_group_scraper import AngelGroupScraper
from src.collectors.angel_investor_directory import AngelInvestorDirectory
from src.collectors.angellist_enricher import AngelListInvestorEnricher
from src.collectors.coingecko_community_enricher import CoinGeckoCommunityEnricher
from src.collectors.coingecko_enricher import CoinGeckoEnricher
from src.collectors.coingecko_linker import CoinGeckoLinker
from src.collectors.crunchbase_enricher import CrunchbaseEnricher
from src.collectors.cryptorank import CryptoRankCollector
from src.collectors.defillama import DefiLlamaCollector
from src.collectors.defillama_enricher import DefiLlamaProtocolEnricher
from src.collectors.etherscan_enricher import EtherscanEnricher
from src.collectors.formd_promoters import FormDPromoterEnricher
from src.collectors.github_enricher import GitHubEnricher
from src.collectors.google_news import GoogleNewsFundingCollector
from src.collectors.hackernews import HackerNewsFundingCollector
from src.collectors.hackernews_enricher import HackerNewsEnricher
from src.collectors.investor_profile_aggregator import InvestorProfileAggregator
from src.collectors.nih_reporter import NIHReporterCollector
from src.collectors.npm_enricher import NpmEnricher
from src.collectors.nsf_awards import NSFAwardsCollector
from src.collectors.producthunt_enricher import ProductHuntEnricher
from src.collectors.propublica_990 import ProPublica990Enricher
from src.collectors.pypi_enricher import PyPIEnricher
from src.collectors.reddit_enricher import RedditEnricher
from src.collectors.rss_funding import RSSFundingCollector
from src.collectors.sbir import SBIRCollector
from src.collectors.sec_13f import SEC13FEnricher
from src.collectors.sec_edgar import SECEdgarBulkCollector, SECEdgarCollector
from src.collectors.sec_form_adv import SECFormADVEnricher
from src.collectors.snapshot_enricher import SnapshotEnricher
from src.collectors.snapshot_linker import SnapshotLinker
from src.collectors.twitter_bio_enricher import TwitterBioEnricher
from src.collectors.vc_website_enricher import VCWebsiteEnricher
from src.collectors.web_search_enricher import WebSearchEnricher
from src.collectors.website_linker import WebsiteLinker
from src.collectors.founder_enricher import FounderEnricher
from src.collectors.openvc import OpenVCCollector
from src.collectors.preseed_fund_directory import PreSeedFundDirectory
from src.collectors.techstars import TechstarsCollector
from src.collectors.wellfound import WellfoundEnricher
from src.collectors.wellfound_angel_discovery import LinkedInAngelDiscovery
from src.collectors.crunchbase_angel_discovery import PublishedListAngelDiscovery
from src.collectors.yc_directory import YCDirectoryCollector
from src.db.session import async_session
from src.pipeline.enrich import run_enricher
from src.pipeline.ingest import run_collector

logger = logging.getLogger(__name__)


# Per-job timeout: 30 minutes default, prevents one slow job from blocking the scheduler
JOB_TIMEOUT = 30 * 60


async def run_collector_job(name: str, collector_cls, **kwargs) -> None:
    """Run a single collector job with error handling and timeout."""
    logger.info(f"[scheduler] Starting collector: {name}")
    start = datetime.now()
    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            async with async_session() as session:
                collector = collector_cls(**kwargs)
                run = await run_collector(session, collector)
                elapsed = (datetime.now() - start).total_seconds()
                logger.info(
                    f"[scheduler] {name} done in {elapsed:.1f}s — "
                    f"fetched={run.rounds_fetched} new={run.rounds_new} "
                    f"flagged={run.rounds_flagged}"
                )
    except TimeoutError:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} timed out after {elapsed:.1f}s")
    except Exception as e:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} failed after {elapsed:.1f}s: {e}")


async def run_enricher_job(name: str, enricher_cls) -> None:
    """Run a single enricher job with error handling and timeout."""
    logger.info(f"[scheduler] Starting enricher: {name}")
    start = datetime.now()
    try:
        async with asyncio.timeout(JOB_TIMEOUT):
            async with async_session() as session:
                enricher = enricher_cls()
                result = await run_enricher(session, enricher)
                elapsed = (datetime.now() - start).total_seconds()
                logger.info(
                    f"[scheduler] {name} done in {elapsed:.1f}s — "
                    f"updated={result.records_updated} skipped={result.records_skipped} "
                    f"errors={len(result.errors)}"
                )
    except TimeoutError:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} timed out after {elapsed:.1f}s")
    except Exception as e:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} failed after {elapsed:.1f}s: {e}")


LONG_JOB_TIMEOUT = 4 * 60 * 60  # 4 hours for discovery crawlers


async def run_enricher_job_long(name: str, enricher_cls) -> None:
    """Run an enricher with extended timeout (4 hours) for large discovery crawls."""
    logger.info(f"[scheduler] Starting long enricher: {name}")
    start = datetime.now()
    try:
        async with asyncio.timeout(LONG_JOB_TIMEOUT):
            async with async_session() as session:
                enricher = enricher_cls()
                result = await run_enricher(session, enricher)
                elapsed = (datetime.now() - start).total_seconds()
                logger.info(
                    f"[scheduler] {name} done in {elapsed:.1f}s — "
                    f"updated={result.records_updated} skipped={result.records_skipped} "
                    f"errors={len(result.errors)}"
                )
    except TimeoutError:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} timed out after {elapsed:.1f}s")
    except Exception as e:
        elapsed = (datetime.now() - start).total_seconds()
        logger.error(f"[scheduler] {name} failed after {elapsed:.1f}s: {e}")


async def realtime_tick() -> None:
    """Run every 15 minutes: news feeds."""
    await run_collector_job("rss_funding", RSSFundingCollector)
    await run_collector_job("google_news", GoogleNewsFundingCollector)


async def hourly_tick() -> None:
    """Run every hour: SEC EDGAR, HackerNews, Reddit."""
    await run_collector_job("sec_edgar_recent", SECEdgarCollector, days_back=1)
    await run_collector_job("hackernews", HackerNewsFundingCollector, days=1)
    await run_enricher_job("hackernews_enrich", HackerNewsEnricher)
    await run_enricher_job("reddit", RedditEnricher)


async def daily_tick() -> None:
    """Run once per day: linkers first, then directories, metrics, governance."""
    # Phase 1: Linkers — discover external IDs for projects
    await run_enricher_job("website_linker", WebsiteLinker)
    await run_enricher_job("coingecko_linker", CoinGeckoLinker)
    await run_enricher_job("snapshot_linker", SnapshotLinker)
    # Phase 2: Collectors
    await run_collector_job("defillama", DefiLlamaCollector)
    await run_collector_job("yc_directory", YCDirectoryCollector)
    # Phase 3: Enrichers (depend on IDs discovered by linkers)
    await run_enricher_job("github", GitHubEnricher)
    await run_enricher_job("npm", NpmEnricher)
    await run_enricher_job("pypi", PyPIEnricher)
    await run_enricher_job("producthunt", ProductHuntEnricher)
    await run_enricher_job("formd_promoters", FormDPromoterEnricher)
    # Token/DeFi metrics chain: DefiLlama → CoinGecko → Community → Etherscan
    await run_enricher_job("defillama_protocols", DefiLlamaProtocolEnricher)
    await run_enricher_job("coingecko", CoinGeckoEnricher)
    await run_enricher_job("coingecko_community", CoinGeckoCommunityEnricher)
    await run_enricher_job("etherscan", EtherscanEnricher)
    await run_enricher_job("snapshot", SnapshotEnricher)
    # Twitter bio enrichment (before web_search — can discover handles web_search would also find)
    await run_enricher_job("twitter_bio", TwitterBioEnricher)
    # Web search enrichment for investor profiles (websites, social, descriptions)
    await run_enricher_job("web_search", WebSearchEnricher)
    # Investor profile aggregation (no API calls, purely DB-driven)
    await run_enricher_job("investor_profile_aggregator", InvestorProfileAggregator)
    # Founder enrichment (DDG search for LinkedIn, bio, previous companies)
    await run_enricher_job("founder_enricher", FounderEnricher)


async def weekly_tick() -> None:
    """Run once per week: accelerators, SEC intelligence."""
    await run_collector_job("500_global", FiveHundredGlobalCollector)
    await run_collector_job("techstars", TechstarsCollector)
    await run_collector_job("openvc", OpenVCCollector)
    await run_enricher_job("sec_form_adv", SECFormADVEnricher)
    await run_enricher_job("sec_13f", SEC13FEnricher)
    await run_enricher_job("propublica_990", ProPublica990Enricher)
    await run_collector_job("sec_edgar_bulk", SECEdgarBulkCollector)
    await run_enricher_job("wellfound", WellfoundEnricher)
    await run_enricher_job("angellist_investors", AngelListInvestorEnricher)
    await run_enricher_job("crunchbase", CrunchbaseEnricher)
    await run_enricher_job("angel_groups", AngelGroupScraper)
    await run_enricher_job("angel_investors", AngelInvestorDirectory)
    await run_enricher_job("preseed_funds", PreSeedFundDirectory)
    await run_collector_job("accelerator_directory", AcceleratorDirectoryCollector)
    await run_enricher_job("vc_website", VCWebsiteEnricher)
    # Angel investor discovery — find new angels from Wellfound + Crunchbase
    # These run with extended timeout (4 hours) since they crawl thousands of pages
    await run_enricher_job_long("linkedin_angel_discovery", LinkedInAngelDiscovery)
    await run_enricher_job_long("published_list_discovery", PublishedListAngelDiscovery)
    await run_collector_job("sbir", SBIRCollector)
    await run_collector_job("cryptorank", CryptoRankCollector)
    await run_collector_job("nsf_awards", NSFAwardsCollector)
    await run_collector_job("nih_reporter", NIHReporterCollector)


async def scheduler_loop() -> None:
    """Main scheduler loop. Runs indefinitely."""
    logger.info("[scheduler] Starting scheduler loop")

    # Run daily + weekly enrichers on startup so deploys don't wait 24h
    logger.info("[scheduler] Running daily tick on startup")
    try:
        await daily_tick()
    except Exception as e:
        logger.error(f"[scheduler] startup daily_tick error: {e}")

    logger.info("[scheduler] Running weekly tick on startup")
    try:
        await weekly_tick()
    except Exception as e:
        logger.error(f"[scheduler] startup weekly_tick error: {e}")

    tick_count = 0

    while True:
        tick_count += 1

        # Every tick (15 min): real-time sources
        try:
            await realtime_tick()
        except Exception as e:
            logger.error(f"[scheduler] realtime_tick error: {e}")

        # Every 4th tick (hourly): hourly sources
        if tick_count % 4 == 0:
            try:
                await hourly_tick()
            except Exception as e:
                logger.error(f"[scheduler] hourly_tick error: {e}")

        # Every 96th tick (daily, ~24h): daily sources
        if tick_count % 96 == 0:
            try:
                await daily_tick()
            except Exception as e:
                logger.error(f"[scheduler] daily_tick error: {e}")

        # Every 672nd tick (weekly, ~7 days): weekly sources
        if tick_count % 672 == 0:
            try:
                await weekly_tick()
            except Exception as e:
                logger.error(f"[scheduler] weekly_tick error: {e}")
            tick_count = 0  # Reset to prevent overflow

        # Sleep 15 minutes
        await asyncio.sleep(15 * 60)
