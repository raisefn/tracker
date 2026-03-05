"""CLI entrypoint to run data collectors."""

import asyncio
import logging
import sys

from src.collectors.defillama import DefiLlamaCollector
from src.db.session import async_session
from src.pipeline.ingest import run_collector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

COLLECTORS = {
    "defillama": DefiLlamaCollector,
}


async def main(names: list[str] | None = None) -> None:
    targets = names or list(COLLECTORS.keys())

    async with async_session() as session:
        for name in targets:
            cls = COLLECTORS.get(name)
            if cls is None:
                logger.error(f"Unknown collector: {name}")
                continue

            logger.info(f"Running collector: {name}")
            collector = cls()
            run = await run_collector(session, collector)
            logger.info(
                f"  {name} done — fetched={run.rounds_fetched} new={run.rounds_new} flagged={run.rounds_flagged}"
            )


if __name__ == "__main__":
    names = sys.argv[1:] or None
    asyncio.run(main(names))
