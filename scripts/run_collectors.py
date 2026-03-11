"""CLI entrypoint to run data collectors.

Usage:
    python -m scripts.run_collectors                    # run all
    python -m scripts.run_collectors defillama rss      # run specific
    python -m scripts.run_collectors sec_edgar_xml      # SEC backfill (last 90 days)
    python -m scripts.run_collectors sec_edgar_bulk_q 2025 1  # SEC bulk Q1 2025
"""

import asyncio
import logging
import sys
from datetime import date, timedelta

from src.collectors.accelerator_500 import FiveHundredGlobalCollector
from src.collectors.defillama import DefiLlamaCollector
from src.collectors.google_news import GoogleNewsFundingCollector
from src.collectors.rss_funding import RSSFundingCollector
from src.collectors.sec_edgar import SECEdgarBulkCollector, SECEdgarCollector, SECEdgarXMLCollector
from src.collectors.yc_directory import YCDirectoryCollector
from src.db.session import async_session
from src.pipeline.ingest import run_collector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

COLLECTORS = {
    "defillama": DefiLlamaCollector,
    "sec_edgar": SECEdgarCollector,
    "sec_edgar_bulk": SECEdgarBulkCollector,
    "yc_directory": YCDirectoryCollector,
    "rss_funding": RSSFundingCollector,
    "500_global": FiveHundredGlobalCollector,
    "google_news": GoogleNewsFundingCollector,
}


def _build_collector(name: str, extra_args: list[str]):
    """Build a collector, handling special cases that need constructor args."""
    if name == "sec_edgar_xml":
        # Historical backfill: default last 90 days
        days = int(extra_args[0]) if extra_args else 90
        end = date.today()
        start = end - timedelta(days=days)
        logger.info(f"SEC EDGAR XML backfill: {start} to {end} ({days} days)")
        return SECEdgarXMLCollector(start_date=start, end_date=end)

    if name == "sec_edgar_bulk_q":
        # Specific quarter: python -m scripts.run_collectors sec_edgar_bulk_q 2025 1
        if len(extra_args) >= 2:
            year, quarter = int(extra_args[0]), int(extra_args[1])
        else:
            # Default to previous quarter
            now = date.today()
            quarter = (now.month - 1) // 3
            year = now.year if quarter > 0 else now.year - 1
            quarter = quarter if quarter > 0 else 4
        logger.info(f"SEC EDGAR bulk: {year} Q{quarter}")
        return SECEdgarBulkCollector(year=year, quarter=quarter)

    cls = COLLECTORS.get(name)
    if cls is None:
        return None
    return cls()


async def main(args: list[str]) -> None:
    if not args:
        targets = list(COLLECTORS.keys())
        extra_args: list[str] = []
    else:
        # First arg(s) that match collector names are targets, rest are extra args
        targets = []
        extra_args = []
        all_names = set(COLLECTORS.keys()) | {"sec_edgar_xml", "sec_edgar_bulk_q"}
        for a in args:
            if a in all_names and not extra_args:
                targets.append(a)
            else:
                extra_args.append(a)
        if not targets:
            targets = list(COLLECTORS.keys())

    async with async_session() as session:
        for name in targets:
            collector = _build_collector(name, extra_args)
            if collector is None:
                logger.error(f"Unknown collector: {name}")
                continue

            logger.info(f"Running collector: {name}")
            run = await run_collector(session, collector)
            logger.info(
                f"  {name} done — fetched={run.rounds_fetched} new={run.rounds_new} flagged={run.rounds_flagged}"
            )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
