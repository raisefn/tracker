"""Backfill historical SEC EDGAR Form D data.

Uses the EFTS search API + XML parsing since SEC removed bulk ZIP files.
Processes one month at a time to stay within EFTS pagination limits.

Usage:
    python -m scripts.backfill_edgar                # last 3 months
    python -m scripts.backfill_edgar 2024 2024      # all of 2024
    python -m scripts.backfill_edgar 2020 2026      # 2020 through today
"""

import asyncio
import logging
import sys
from datetime import date, timedelta

from src.collectors.sec_edgar import SECEdgarXMLCollector
from src.db.session import async_session
from src.pipeline.ingest import run_collector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def get_months(start_year: int, end_year: int) -> list[tuple[date, date]]:
    """Generate (start_date, end_date) tuples for each month in range."""
    months = []
    today = date.today()

    for year in range(start_year, end_year + 1):
        for month in range(1, 13):
            start = date(year, month, 1)
            if start > today:
                break
            # End of month
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            end = min(end, today)
            months.append((start, end))

    return months


async def main(start_year: int | None = None, end_year: int | None = None) -> None:
    if start_year and end_year:
        months = get_months(start_year, end_year)
    else:
        # Default: last 3 months
        today = date.today()
        months = []
        for i in range(3):
            end = today - timedelta(days=30 * i)
            start = end.replace(day=1)
            months.append((start, min(end, today)))
        months.reverse()

    total_new = 0
    total_fetched = 0

    logger.info(f"Backfilling {len(months)} months: {months[0][0]} to {months[-1][1]}")

    for start, end in months:
        label = f"{start.strftime('%Y-%m')}"
        logger.info(f"--- Backfilling {label} ({start} to {end}) ---")
        try:
            async with async_session() as session:
                collector = SECEdgarXMLCollector(start_date=start, end_date=end)
                run = await run_collector(session, collector)
                total_new += run.rounds_new
                total_fetched += run.rounds_fetched
                logger.info(
                    f"  {label}: fetched={run.rounds_fetched} "
                    f"new={run.rounds_new} flagged={run.rounds_flagged}"
                )
        except Exception as e:
            logger.error(f"  {label} failed: {e}")

    logger.info(f"=== DONE: {total_fetched} fetched, {total_new} new rounds ===")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2:
        asyncio.run(main(int(args[0]), int(args[1])))
    elif len(args) == 0:
        asyncio.run(main())
    else:
        print("Usage: python -m scripts.backfill_edgar [start_year end_year]")
        sys.exit(1)
