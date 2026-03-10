"""ProPublica Nonprofit Explorer collector for family foundations.

Private foundations (Form 990-PF filers) often make program-related
investments and grants to startups. This enricher identifies wealthy
family foundations that may be angel investors.

API: https://projects.propublica.org/nonprofits/api/v2
No API key required.
"""

import asyncio
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

PROPUBLICA_API = "https://projects.propublica.org/nonprofits/api/v2"

HEADERS = {
    "User-Agent": "raisefn/tracker (startup research)",
}

# NTEE codes related to venture/startup investment
INVESTMENT_NTEE_CODES = [
    "T",   # Philanthropy, Voluntarism & Grantmaking Foundations
    "T20", # Private Grantmaking Foundations
    "T21", # Corporate Foundations
    "T22", # Private Independent Foundations
    "T30", # Public Foundations
    "S",   # Community Improvement & Capacity Building
    "U",   # Science & Technology
    "W",   # Public & Societal Benefit
]

# Search terms to find family foundations with investment activity
SEARCH_QUERIES = [
    "family foundation",
    "venture philanthropy",
    "impact investing foundation",
    "family office foundation",
]

# Minimum assets to be interesting ($1M)
MIN_ASSETS = 1_000_000


class ProPublica990Enricher(BaseEnricher):
    """Enrich investor records with family foundation data from IRS 990 filings.

    Searches ProPublica's Nonprofit Explorer for private foundations
    with significant assets and investment activity.
    """

    def source_name(self) -> str:
        return "propublica_990"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        foundations: list[dict] = []

        async with httpx.AsyncClient(timeout=30.0, headers=HEADERS) as client:
            # Search for family foundations
            for query in SEARCH_QUERIES:
                try:
                    found = await self._search_foundations(client, query)
                    foundations.extend(found)
                    await asyncio.sleep(1.0)  # Rate limiting
                except Exception as e:
                    logger.warning(f"ProPublica search error for '{query}': {e}")

            # Also search by NTEE code
            for ntee in INVESTMENT_NTEE_CODES[:3]:  # Limit to avoid rate limits
                try:
                    found = await self._search_by_ntee(client, ntee)
                    foundations.extend(found)
                    await asyncio.sleep(1.0)
                except Exception as e:
                    logger.warning(f"ProPublica NTEE search error for '{ntee}': {e}")

        # Deduplicate by EIN
        seen_eins: set[str] = set()
        unique_foundations: list[dict] = []
        for f in foundations:
            ein = f.get("ein", "")
            if ein and ein not in seen_eins:
                seen_eins.add(ein)
                unique_foundations.append(f)

        logger.info(f"Found {len(unique_foundations)} unique foundations")

        # Process each foundation
        for foundation in unique_foundations:
            try:
                updated = await self._process_foundation(session, foundation)
                if updated:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                error_msg = f"Error processing foundation {foundation.get('name', '?')}: {e}"
                logger.warning(error_msg)
                result.errors.append(error_msg)
                result.records_skipped += 1

        await session.flush()
        logger.info(
            f"990 enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _search_foundations(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        """Search ProPublica for foundations matching a query."""
        foundations = []
        page = 0

        while page < 5:  # Max 5 pages per query
            resp = await client.get(
                f"{PROPUBLICA_API}/search.json",
                params={"q": query, "page": page},
            )
            if resp.status_code != 200:
                break

            data = resp.json()
            orgs = data.get("organizations", [])
            if not orgs:
                break

            for org in orgs:
                # Filter for private foundations (subsection 03 = 501(c)(3))
                if self._is_relevant_foundation(org):
                    foundations.append(org)

            page += 1
            await asyncio.sleep(0.5)

        return foundations

    async def _search_by_ntee(self, client: httpx.AsyncClient, ntee_code: str) -> list[dict]:
        """Search by NTEE code for foundation types."""
        foundations = []

        resp = await client.get(
            f"{PROPUBLICA_API}/search.json",
            params={"ntee[id]": ntee_code, "page": 0},
        )
        if resp.status_code != 200:
            return foundations

        data = resp.json()
        for org in data.get("organizations", []):
            if self._is_relevant_foundation(org):
                foundations.append(org)

        return foundations

    def _is_relevant_foundation(self, org: dict) -> bool:
        """Check if an organization is a relevant private foundation."""
        # Must have significant assets
        assets = org.get("total_assets", 0) or 0
        if assets < MIN_ASSETS:
            return False

        # Prefer private foundations
        subsection = str(org.get("subsection_code", ""))
        ntee = org.get("ntee_code", "") or ""

        # 501(c)(3) organizations with foundation-type NTEE codes
        if subsection == "3" and ntee.startswith(("T", "S", "U", "W")):
            return True

        # Any org with large enough assets and foundation in name
        name = (org.get("name", "") or "").lower()
        if any(kw in name for kw in ["foundation", "family", "charitable trust"]):
            return True

        return False

    async def _process_foundation(self, session: AsyncSession, org: dict) -> bool:
        """Process a single foundation — match or create investor."""
        name = (org.get("name", "") or "").strip()
        if not name or len(name) < 3:
            return False

        ein = str(org.get("ein", "")).strip()
        slug = make_slug(name)

        # Try to match by EIN first, then slug
        investor = None
        if ein:
            result = await session.execute(
                select(Investor).where(Investor.ein == ein)
            )
            investor = result.scalar_one_or_none()

        if not investor:
            result = await session.execute(
                select(Investor).where(Investor.slug == slug)
            )
            investor = result.scalar_one_or_none()

        if investor is None:
            investor = Investor(name=name, slug=slug)
            session.add(investor)

        # Update foundation fields
        if ein:
            investor.ein = ein

        assets = org.get("total_assets", 0) or 0
        if assets:
            investor.foundation_assets = int(assets)

        revenue = org.get("total_revenue", 0) or 0
        if revenue:
            investor.annual_giving = int(abs(revenue))  # Revenue can represent grants

        ntee = org.get("ntee_code", "") or ""
        if ntee:
            investor.ntee_code = ntee

        investor.type = "foundation"
        investor.investor_category = "family_foundation"

        # Location
        city = (org.get("city", "") or "").strip()
        state = (org.get("state", "") or "").strip()
        if city and state:
            investor.hq_location = f"{city}, {state}"

        investor.last_enriched_at = datetime.now(timezone.utc)
        stamp_freshness(investor, self.source_name())

        return True
