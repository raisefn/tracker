"""Investor profile aggregator — builds profiles from existing round data.

Zero external API calls. Queries round_investors + rounds + projects to
compute a description, investor_category, and type for every investor
that hasn't been profiled yet. Highest-value, lowest-risk enricher
because it uses data already in the database.
"""

import logging
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.models.round import Round
from src.models.round_investor import RoundInvestor

logger = logging.getLogger(__name__)

SOURCE_KEY = "investor_profile_aggregator"
BATCH_SIZE = 100

# Round types grouped by stage
EARLY_STAGES = {"pre_seed", "seed", "angel", "grant"}
GROWTH_STAGES = {"series_a", "series_b", "series_c", "series_d", "series_e", "series_f"}
LATE_STAGES = {"series_d", "series_e", "series_f", "ipo", "public"}

# Thresholds for type inference
ANGEL_MAX_DEALS = 15
ANGEL_MAX_CHECK = 500_000  # $500K


def _fmt_usd(amount: int) -> str:
    """Format USD amount for human-readable display."""
    if amount >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.1f}B"
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.1f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.0f}K"
    return f"${amount}"


def _classify_category(
    round_types: list[str],
    sectors: list[str],
    num_deals: int,
    num_leads: int,
    avg_check: int | None,
) -> str:
    """Infer investor_category from activity patterns."""
    stage_counts: Counter[str] = Counter()
    for rt in round_types:
        rt_lower = (rt or "").lower().replace(" ", "_")
        if rt_lower in EARLY_STAGES:
            stage_counts["early"] += 1
        elif rt_lower in GROWTH_STAGES:
            stage_counts["growth"] += 1
        elif rt_lower in LATE_STAGES:
            stage_counts["late"] += 1
        else:
            stage_counts["other"] += 1

    total_staged = stage_counts["early"] + stage_counts["growth"] + stage_counts["late"]

    # Sector focus check
    sector_counts = Counter(s for s in sectors if s)
    top_sector, top_count = sector_counts.most_common(1)[0] if sector_counts else (None, 0)
    sector_concentrated = top_count >= 3 and top_count / max(num_deals, 1) >= 0.5

    # Stage specialisation
    if total_staged > 0:
        early_pct = stage_counts["early"] / total_staged
        growth_pct = stage_counts["growth"] / total_staged
    else:
        early_pct = growth_pct = 0.0

    # Priority order
    if sector_concentrated and top_sector:
        sector_tag = top_sector.lower().replace(" ", "_").replace("/", "_")
        if early_pct >= 0.6:
            return f"seed_specialist_{sector_tag}"
        return f"sector_focused_{sector_tag}"

    if early_pct >= 0.7:
        return "seed_specialist"

    if growth_pct >= 0.6:
        return "growth_investor"

    if stage_counts["early"] > 0 and stage_counts["growth"] > 0:
        return "multi_stage"

    if num_leads >= 3:
        return "active_lead"

    if num_deals >= 10:
        return "prolific_investor"

    return "early_stage"  # safe default for crypto/web3


def _classify_type(
    current_type: str | None,
    num_deals: int,
    avg_check: int | None,
    round_types: list[str],
    num_leads: int,
) -> str | None:
    """Infer investor type if not already set from a higher-confidence source."""
    # Don't override types set by SEC or manual enrichment
    if current_type and current_type not in ("other", "unknown"):
        return current_type

    early_count = sum(
        1 for rt in round_types
        if (rt or "").lower().replace(" ", "_") in EARLY_STAGES
    )

    # Angel signals: few deals, small checks, early stage only
    if num_deals <= ANGEL_MAX_DEALS and (avg_check is None or avg_check <= ANGEL_MAX_CHECK):
        if early_count == len(round_types) or num_deals <= 3:
            return "angel"

    # DAO signal: participation_type or name-based (handled elsewhere)
    # Corporate signal: would need name matching (out of scope here)

    # Default to VC for investors with many deals or large checks
    if num_deals >= 5 or (avg_check and avg_check > ANGEL_MAX_CHECK):
        return "vc"

    if num_leads >= 2:
        return "vc"

    return "angel"  # small portfolio, unknown checks → angel


def _build_description(
    num_deals: int,
    num_leads: int,
    round_types: list[str],
    sectors: list[str],
    avg_check: int | None,
    recent_projects: list[str],
) -> str:
    """Generate a human-readable description from activity stats."""
    parts: list[str] = []

    # Opening line: stage focus
    stage_counts: Counter[str] = Counter()
    for rt in round_types:
        rt_lower = (rt or "").lower().replace(" ", "_")
        if rt_lower in EARLY_STAGES:
            stage_counts["seed"] += 1
        elif rt_lower in GROWTH_STAGES:
            stage_counts["growth"] += 1

    if stage_counts:
        top_stage = stage_counts.most_common(1)[0][0]
        parts.append(f"Active {top_stage}-stage investor.")
    else:
        parts.append("Active investor.")

    # Deal count
    parts.append(f"{num_deals} investment{'s' if num_deals != 1 else ''} tracked")

    # Top sectors
    sector_counts = Counter(s for s in sectors if s)
    top_sectors = [s for s, _ in sector_counts.most_common(3)]
    if top_sectors:
        parts[-1] += f", primarily in {' and '.join(top_sectors)}."
    else:
        parts[-1] += "."

    # Average check size
    if avg_check and avg_check > 0:
        parts.append(f"Average check size: {_fmt_usd(avg_check)}.")

    # Lead rounds
    if num_leads > 0:
        parts.append(f"Led {num_leads} round{'s' if num_leads != 1 else ''}.")

    # Recent portfolio
    if recent_projects:
        names = ", ".join(recent_projects[:5])
        parts.append(f"Recent: {names}.")

    return " ".join(parts)


class InvestorProfileAggregator(BaseEnricher):
    """Aggregate investor profiles from existing round participation data.

    No external API calls — purely database-driven enrichment.
    """

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Find investors not yet profiled by this enricher.
        # Check source_freshness JSON for our key.
        total_investors = await session.execute(select(func.count(Investor.id)))
        total = total_investors.scalar() or 0
        logger.info(f"[{SOURCE_KEY}] Total investors: {total}")

        offset = 0
        while True:
            # Fetch a batch of investors that haven't been profiled by us
            stmt = (
                select(Investor)
                .where(
                    # source_freshness is NULL or doesn't contain our key
                    or_(
                        Investor.source_freshness.is_(None),
                        ~cast(Investor.source_freshness, String).contains(SOURCE_KEY),
                    )
                )
                .order_by(Investor.name)
                .limit(BATCH_SIZE)
                .offset(offset)
            )
            batch_result = await session.execute(stmt)
            investors = batch_result.scalars().all()

            if not investors:
                break

            logger.info(
                f"[{SOURCE_KEY}] Processing batch of {len(investors)} investors "
                f"(offset={offset})"
            )

            for investor in investors:
                try:
                    updated = await self._profile_investor(session, investor)
                    if updated:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                except Exception as e:
                    error_msg = f"Error profiling {investor.name}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

            # Flush after each batch to keep memory bounded
            await session.flush()

            # If we got fewer than BATCH_SIZE, we're done
            if len(investors) < BATCH_SIZE:
                break

            # Don't increment offset — the WHERE clause excludes already-processed
            # investors (they now have the source_freshness key), so offset stays 0.
            # But guard against infinite loops if stamp_freshness somehow fails:
            offset = 0

        logger.info(
            f"[{SOURCE_KEY}] Complete: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _profile_investor(
        self, session: AsyncSession, investor: Investor
    ) -> bool:
        """Build a profile for a single investor from their round participations."""
        # Query all round participations with round + project data
        stmt = (
            select(RoundInvestor)
            .where(RoundInvestor.investor_id == investor.id)
            .options(
                selectinload(RoundInvestor.round).selectinload(Round.project)
            )
        )
        participation_result = await session.execute(stmt)
        participations = participation_result.scalars().all()

        if not participations:
            # No round data — stamp freshness so we don't re-process, but skip
            stamp_freshness(investor, self.source_name())
            return False

        # Extract stats
        round_types: list[str] = []
        sectors: list[str] = []
        check_sizes: list[int] = []
        num_leads = 0
        recent_projects: list[tuple[str, str | None]] = []  # (name, date)

        for p in participations:
            rnd = p.round
            if rnd is None:
                continue

            if rnd.round_type:
                round_types.append(rnd.round_type)
            if rnd.sector:
                sectors.append(rnd.sector)
            if p.check_size_usd and p.check_size_usd > 0:
                check_sizes.append(p.check_size_usd)
            if p.is_lead:
                num_leads += 1

            project = rnd.project
            if project:
                date_str = str(rnd.date) if rnd.date else None
                recent_projects.append((project.name, date_str))

        num_deals = len(participations)
        avg_check = int(sum(check_sizes) / len(check_sizes)) if check_sizes else None

        # Sort recent projects by date descending
        recent_projects.sort(key=lambda x: x[1] or "", reverse=True)
        recent_names = [name for name, _ in recent_projects]

        # Build description
        investor.description = _build_description(
            num_deals=num_deals,
            num_leads=num_leads,
            round_types=round_types,
            sectors=sectors,
            avg_check=avg_check,
            recent_projects=recent_names,
        )

        # Classify category
        investor.investor_category = _classify_category(
            round_types=round_types,
            sectors=sectors,
            num_deals=num_deals,
            num_leads=num_leads,
            avg_check=avg_check,
        )

        # Infer type (only if not already set by higher-confidence source)
        inferred_type = _classify_type(
            current_type=investor.type,
            num_deals=num_deals,
            avg_check=avg_check,
            round_types=round_types,
            num_leads=num_leads,
        )
        if inferred_type:
            investor.type = inferred_type

        investor.last_enriched_at = datetime.now(timezone.utc)
        stamp_freshness(investor, self.source_name())

        return True
