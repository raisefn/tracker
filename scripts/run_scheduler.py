"""Entrypoint for the data collection scheduler.

Runs the scheduler loop that automatically collects and enriches data
at tiered intervals (15min / hourly / daily / weekly).

Usage:
    python -m scripts.run_scheduler
"""

import asyncio
import logging

from src.scheduler import scheduler_loop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if __name__ == "__main__":
    asyncio.run(scheduler_loop())
