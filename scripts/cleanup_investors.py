"""Clean up garbage investor records from the database.

Removes investors whose names fail validation (sentence fragments,
HTML entities, lowercase starts, etc.) and re-resolves remaining
names through the entity resolver.

Usage:
    python -m scripts.cleanup_investors          # dry run (default)
    python -m scripts.cleanup_investors apply     # actually delete
"""

import asyncio
import sys

from sqlalchemy import delete, func, select

from src.collectors.news_parser import is_valid_investor_name
from src.db.session import async_session
from src.models import Investor, RoundInvestor
from src.pipeline.entity_resolver import resolve_investor_name
from src.pipeline.normalizer import make_slug


async def cleanup(dry_run: bool = True) -> None:
    async with async_session() as session:
        result = await session.execute(
            select(Investor).order_by(Investor.name)
        )
        investors = result.scalars().all()

        invalid: list[Investor] = []
        rename: list[tuple[Investor, str]] = []

        for inv in investors:
            if not is_valid_investor_name(inv.name):
                invalid.append(inv)
                continue

            canonical = resolve_investor_name(inv.name)
            if canonical != inv.name:
                rename.append((inv, canonical))

        print(f"\nTotal investors: {len(investors)}")
        print(f"Invalid (will delete): {len(invalid)}")
        print(f"Will rename: {len(rename)}")

        if invalid:
            print("\n--- INVALID INVESTORS (to delete) ---")
            for inv in invalid:
                count_result = await session.execute(
                    select(func.count()).where(RoundInvestor.investor_id == inv.id)
                )
                round_count = count_result.scalar()
                print(f"  [{round_count} rounds] {inv.name!r}")

        if rename:
            print("\n--- RENAMES ---")
            for inv, canonical in rename:
                print(f"  {inv.name!r} -> {canonical!r}")

        if dry_run:
            print("\nDry run — no changes made. Run with 'apply' to execute.")
            return

        for inv in invalid:
            await session.execute(
                delete(RoundInvestor).where(RoundInvestor.investor_id == inv.id)
            )
            await session.delete(inv)

        for inv, canonical in rename:
            new_slug = make_slug(canonical)
            existing = (await session.execute(
                select(Investor).where(Investor.slug == new_slug)
            )).scalar_one_or_none()

            if existing and existing.id != inv.id:
                await session.execute(
                    RoundInvestor.__table__.update()
                    .where(RoundInvestor.investor_id == inv.id)
                    .values(investor_id=existing.id)
                )
                await session.delete(inv)
            else:
                inv.name = canonical
                inv.slug = new_slug

        await session.commit()
        print(f"\nDone. Deleted {len(invalid)}, renamed {len(rename)}.")


if __name__ == "__main__":
    dry_run = "apply" not in sys.argv
    asyncio.run(cleanup(dry_run))
