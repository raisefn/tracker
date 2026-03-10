"""Backfill historical SEC EDGAR Form D data.

Usage:
    python -m scripts.backfill_edgar                # most recent quarter
    python -m scripts.backfill_edgar 2024 2024      # all quarters in 2024
    python -m scripts.backfill_edgar 2020 2025      # 2020 Q1 through 2025 Q4
"""

import asyncio
import logging
import sys
from datetime import date

from src.collectors.sec_edgar import SECEdgarBulkCollector
from src.db.session import async_session
from src.pipeline.ingest import run_collector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def get_quarters(start_year: int, end_year: int) -> list[tuple[int, int]]:
    """Generate (year, quarter) tuples for the given range."""
    quarters = []
    now = date.today()
    current_quarter = (now.month - 1) // 3 + 1

    for year in range(start_year, end_year + 1):
        for q in range(1, 5):
            # Skip future quarters
            if year > now.year or (year == now.year and q > current_quarter):
                continue
            quarters.append((year, q))

    return quarters


async def main(start_year: int | None = None, end_year: int | None = None) -> None:
    if start_year and end_year:
        quarters = get_quarters(start_year, end_year)
    else:
        # Default: most recent complete quarter
        now = date.today()
        q = (now.month - 1) // 3
        year = now.year if q > 0 else now.year - 1
        q = q if q > 0 else 4
        quarters = [(year, q)]

    logger.info(f"Backfilling {len(quarters)} quarters: {quarters[0]} to {quarters[-1]}")

    for year, quarter in quarters:
        logger.info(f"--- Backfilling {year} Q{quarter} ---")
        try:
            async with async_session() as session:
                collector = SECEdgarBulkCollector(year=year, quarter=quarter)
                run = await run_collector(session, collector)
                logger.info(
                    f"  {year}Q{quarter}: fetched={run.rounds_fetched} "
                    f"new={run.rounds_new} flagged={run.rounds_flagged}"
                )
        except Exception as e:
            logger.error(f"  {year}Q{quarter} failed: {e}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) == 2:
        asyncio.run(main(int(args[0]), int(args[1])))
    elif len(args) == 0:
        asyncio.run(main())
    else:
        print("Usage: python -m scripts.backfill_edgar [start_year end_year]")
        sys.exit(1)
