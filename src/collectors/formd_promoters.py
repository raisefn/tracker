"""Form D promoter frequency index.

Extracts "Related Persons" from SEC Form D filings and builds a
frequency index. People who appear as promoters/directors across
multiple Form D filings are likely active angel investors, syndicate
leads, or placement agents.

Data source: Form D quarterly data sets (RELATEDPERSONS.tsv)
https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets
"""

import io
import logging
import zipfile
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import (
    BaseEnricher,
    EnrichmentResult,
    find_investor_match,
    stamp_freshness,
)
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "raisefn/tracker contact@raisefn.com",
    "Accept-Encoding": "gzip, deflate",
}

SEC_FORMD_BASE = "https://www.sec.gov/files/structureddata/data/form-d-data-sets"

# Minimum appearances to be considered an active angel/promoter
MIN_APPEARANCES = 3


def _latest_quarters(n: int = 4) -> list[str]:
    """Get the latest N quarterly data set identifiers.

    SEC publishes Form D data after quarter-end, so the current quarter
    won't be available yet — start from the previous quarter.
    """
    now = datetime.now()
    quarters = []
    # Start at the PREVIOUS quarter, not the current one
    year, quarter = now.year, (now.month - 1) // 3 + 1
    quarter -= 1
    if quarter == 0:
        quarter = 4
        year -= 1

    for _ in range(n):
        quarters.append(f"{year}q{quarter}")
        quarter -= 1
        if quarter == 0:
            quarter = 4
            year -= 1

    return quarters


class FormDPromoterEnricher(BaseEnricher):
    """Build a promoter frequency index from Form D related persons.

    People who appear as promoters or directors on multiple Form D
    filings are strong signals for active angel investors.
    """

    def source_name(self) -> str:
        return "formd_promoters"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        try:
            promoters = await self._build_promoter_index()
        except Exception as e:
            error_msg = f"Failed to build promoter index: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result

        # Filter to people appearing in MIN_APPEARANCES+ filings
        active_promoters = {
            name: data for name, data in promoters.items() if data["count"] >= MIN_APPEARANCES
        }

        logger.info(
            f"Found {len(active_promoters)} active promoters "
            f"(from {len(promoters)} total, threshold={MIN_APPEARANCES})"
        )

        for name, data in active_promoters.items():
            try:
                updated = await self._process_promoter(session, name, data)
                if updated:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                error_msg = f"Error processing promoter {name}: {e}"
                logger.warning(error_msg)
                result.errors.append(error_msg)
                result.records_skipped += 1

        await session.flush()
        logger.info(
            f"Form D promoter enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _build_promoter_index(self) -> dict[str, dict]:
        """Download Form D related persons data and build frequency index."""
        promoters: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "roles": set(),
                "states": set(),
                "companies": [],
            }
        )

        quarters = _latest_quarters(4)  # Last 4 quarters

        async with httpx.AsyncClient(timeout=120.0, headers=HEADERS) as client:
            for quarter in quarters:
                try:
                    await self._process_quarter(client, quarter, promoters)
                except Exception as e:
                    logger.warning(f"Failed to process Form D quarter {quarter}: {e}")

        # Convert sets to lists for JSON serialization
        result = {}
        for name, data in promoters.items():
            result[name] = {
                "count": data["count"],
                "roles": list(data["roles"]),
                "states": list(data["states"]),
                "companies": data["companies"][:20],  # Cap at 20
            }

        return result

    async def _process_quarter(
        self,
        client: httpx.AsyncClient,
        quarter: str,
        promoters: dict,
    ) -> None:
        """Process a single quarterly Form D data set."""
        # SEC URL pattern is `{quarter}_d.zip`, not `_formd.zip` (verified 2026-05-02)
        url = f"{SEC_FORMD_BASE}/{quarter}_d.zip"
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            logger.warning(f"Form D data set {quarter} not available: {resp.status_code}")
            return

        zf = zipfile.ZipFile(io.BytesIO(resp.content))

        # Find RELATEDPERSONS + ISSUERS files inside the zip.
        # Schema verified 2026-05-02 against 2025q4_d.zip:
        # - RELATEDPERSONS columns: ACCESSIONNUMBER, FIRSTNAME, LASTNAME,
        #   RELATIONSHIP_1/2/3, STATEORCOUNTRY (no RELATEDPERSON* prefix)
        # - Company names live in ISSUERS.tsv (ENTITYNAME), not FORMDSUBMISSION
        persons_file = None
        issuers_file = None
        for name in zf.namelist():
            lower = name.lower()
            if "relatedperson" in lower:
                persons_file = name
            elif "issuers" in lower:
                issuers_file = name

        if not persons_file:
            logger.warning(f"No RELATEDPERSONS file in {quarter}")
            return

        # Build accession → primary issuer/company-name mapping
        company_map: dict[str, str] = {}
        if issuers_file:
            with zf.open(issuers_file) as f:
                text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                header = text.readline().strip().split("\t")
                for line in text:
                    parts = line.strip().split("\t")
                    if len(parts) < len(header):
                        continue
                    row = dict(zip(header, parts))
                    accession = (row.get("ACCESSIONNUMBER") or "").strip()
                    company = (row.get("ENTITYNAME") or "").strip()
                    is_primary = (row.get("IS_PRIMARYISSUER_FLAG") or "").strip().upper() == "YES"
                    # Prefer the primary issuer; only fall back if no primary seen
                    if accession and company and (is_primary or accession not in company_map):
                        company_map[accession] = company

        # Process related persons
        with zf.open(persons_file) as f:
            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
            header = text.readline().strip().split("\t")
            for line in text:
                parts = line.strip().split("\t")
                if len(parts) < len(header):
                    continue
                row = dict(zip(header, parts))

                first = (row.get("FIRSTNAME") or "").strip()
                last = (row.get("LASTNAME") or "").strip()
                # SEC uses "N/A" as a sentinel for entity rows (LLCs etc.) — skip
                if not last or first.upper() == "N/A" or last.upper() == "N/A":
                    continue

                name = f"{first} {last}".strip() if first else last
                # 3 relationship columns; collect each
                role_parts = [(row.get(f"RELATIONSHIP_{i}") or "").strip() for i in (1, 2, 3)]
                roles = [r for r in role_parts if r]
                state = (row.get("STATEORCOUNTRY") or "").strip()
                accession = (row.get("ACCESSIONNUMBER") or "").strip()

                promoters[name]["count"] += 1
                for role in roles:
                    promoters[name]["roles"].add(role)
                if state:
                    promoters[name]["states"].add(state)

                company = company_map.get(accession)
                if company and company not in promoters[name]["companies"]:
                    promoters[name]["companies"].append(company)

    async def _process_promoter(self, session: AsyncSession, name: str, data: dict) -> bool:
        """Process a single promoter — match or create investor."""
        if not name or len(name) < 3:
            return False

        investor = await find_investor_match(session, name)

        if investor is None:
            # FormD promoters are genuine angel discoveries — create new record
            slug = make_slug(name)
            investor = Investor(name=name, slug=slug)
            session.add(investor)

        # Update promoter fields
        investor.formd_appearances = data["count"]
        investor.formd_roles = {
            "roles": data["roles"],
            "states": data["states"],
            "recent_companies": data["companies"][:10],
        }

        # Classify type based on role patterns
        roles = set(r.lower() for r in data["roles"])
        if "promoter" in roles:
            if not investor.type or investor.type == "other":
                investor.type = "angel"
                investor.investor_category = "angel"
        elif "director" in roles and data["count"] >= 5:
            if not investor.type or investor.type == "other":
                investor.type = "angel"
                investor.investor_category = "serial_director"

        investor.last_enriched_at = datetime.now(timezone.utc)
        stamp_freshness(investor, self.source_name())

        return True
