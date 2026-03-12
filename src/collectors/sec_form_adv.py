"""SEC Form ADV collector for investment advisers and family offices.

Downloads bulk CSV data from the SEC IAPD system. Every registered
investment adviser and exempt reporting adviser (including family offices)
files Form ADV with identifying information, AUM, client types, and
business affiliations.

Data source: https://www.sec.gov/foia-services/frequently-requested-documents/form-adv-data
Bulk downloads: https://adviserinfo.sec.gov/compilation
"""

import csv
import io
import logging
import zipfile
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, find_investor_match, stamp_freshness

logger = logging.getLogger(__name__)

# SEC IAPD bulk data URLs
ADV_BULK_URL = "https://adviserinfo.sec.gov/IAPD/IAPDFirmSummary.xml"
ADV_CSV_URL = "https://www.sec.gov/foia-services/frequently-requested-documents/form-adv-data"

# Direct CSV download — the SEC publishes monthly snapshots
ADV_CURRENT_URL = "https://adviserinfo.sec.gov/IAPD/content/downloads/advfoia/advisors.zip"

HEADERS = {
    "User-Agent": "raisefn/tracker contact@raisefn.com",
    "Accept-Encoding": "gzip, deflate",
}

# AUM buckets in Form ADV Item 5.F
AUM_BUCKETS = {
    "1": 0,
    "2": 5_000_000,
    "3": 25_000_000,
    "4": 100_000_000,
    "5": 500_000_000,
    "6": 1_000_000_000,
    "7": 5_000_000_000,
    "8": 10_000_000_000,
    "9": 50_000_000_000,
    "10": 100_000_000_000,
}

# Investor type classification based on Form ADV fields
FAMILY_OFFICE_INDICATORS = [
    "exempt reporting adviser",
    "family office",
    "single family",
    "multi family",
    "private wealth",
]


def _classify_investor_type(row: dict) -> str:
    """Classify an adviser as vc, family_office, angel, etc."""
    org_type = (row.get("TYPEOFORGANIZATION", "") or "").lower()
    status = (row.get("STATUS", "") or "").lower()
    name = (row.get("FIRMNAME", "") or "").lower()

    # Check for family office indicators
    for indicator in FAMILY_OFFICE_INDICATORS:
        if indicator in name or indicator in status:
            return "family_office"

    # Exempt reporting advisers are often family offices or small VCs
    if "exempt" in status:
        return "family_office"

    # Check for VC indicators
    vc_indicators = ["venture", "capital", "partners", "ventures"]
    if any(ind in name for ind in vc_indicators):
        return "vc"

    return "other"


def _parse_aum(row: dict) -> int | None:
    """Extract AUM from Form ADV data."""
    # Try direct AUM field first
    aum_str = row.get("ASSETS_UNDER_MANAGEMENT", row.get("AUM", ""))
    if aum_str:
        try:
            return int(float(aum_str))
        except (ValueError, TypeError):
            pass

    # Try AUM bucket
    bucket = row.get("AUMRANGE", row.get("AUM_RANGE", ""))
    if bucket and bucket in AUM_BUCKETS:
        return AUM_BUCKETS[bucket]

    return None


class SECFormADVEnricher(BaseEnricher):
    """Enrich investor records with SEC Form ADV data.

    Downloads the bulk adviser data file and matches against
    existing investors by name, or creates new investor records
    for family offices and VCs with significant AUM.
    """

    def source_name(self) -> str:
        return "sec_form_adv"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        try:
            advisers = await self._download_adviser_data()
        except Exception as e:
            error_msg = f"Failed to download Form ADV data: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result

        logger.info(f"Downloaded {len(advisers)} adviser records from SEC")

        for adviser in advisers:
            try:
                updated = await self._process_adviser(session, adviser)
                if updated:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                error_msg = f"Error processing adviser {adviser.get('FIRMNAME', '?')}: {e}"
                logger.warning(error_msg)
                result.errors.append(error_msg)
                result.records_skipped += 1

        await session.flush()
        logger.info(
            f"Form ADV enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _download_adviser_data(self) -> list[dict]:
        """Download and parse the bulk adviser CSV/ZIP file."""
        async with httpx.AsyncClient(timeout=120.0, headers=HEADERS) as client:
            resp = await client.get(ADV_CURRENT_URL, follow_redirects=True)
            resp.raise_for_status()

            advisers = []

            if resp.headers.get("content-type", "").startswith("application/zip") or ADV_CURRENT_URL.endswith(".zip"):
                zf = zipfile.ZipFile(io.BytesIO(resp.content))
                for name in zf.namelist():
                    if name.endswith(".csv") or name.endswith(".txt"):
                        with zf.open(name) as f:
                            text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                            reader = csv.DictReader(text, delimiter=",")
                            for row in reader:
                                advisers.append(dict(row))
                        break  # Use the first CSV found
            else:
                # Direct CSV response
                reader = csv.DictReader(io.StringIO(resp.text), delimiter=",")
                for row in reader:
                    advisers.append(dict(row))

            return advisers

    async def _process_adviser(self, session: AsyncSession, row: dict) -> bool:
        """Process a single adviser record — enrich existing investors only."""
        name = (row.get("FIRMNAME", "") or "").strip()
        if not name or len(name) < 3:
            return False

        aum = _parse_aum(row)
        investor_type = _classify_investor_type(row)

        # Only process family offices, VCs, or advisers with significant AUM
        if investor_type not in ("family_office", "vc") and (aum is None or aum < 10_000_000):
            return False

        crd = (row.get("CRDNUMBER", row.get("CRD", "")) or "").strip()

        investor = await find_investor_match(session, name, sec_crd=crd)

        # Enrichment-only: skip if no existing investor matched
        if investor is None:
            return False

        # Update fields
        if crd:
            investor.sec_crd = crd
        if aum:
            investor.aum = aum
        investor.type = investor_type
        investor.regulatory_status = (row.get("STATUS", "") or "").strip() or None
        investor.legal_entity_type = (row.get("TYPEOFORGANIZATION", "") or "").strip() or None

        # Location
        city = (row.get("MAINCITY", row.get("CITY", "")) or "").strip()
        state = (row.get("MAINSTATE", row.get("STATE", "")) or "").strip()
        if city and state:
            investor.hq_location = f"{city}, {state}"
        elif state:
            investor.hq_location = state

        # Website
        website = (row.get("WEBSITE", row.get("FIRMWEBSITE", "")) or "").strip()
        if website and not investor.website:
            investor.website = website

        investor.investor_category = investor_type
        investor.last_enriched_at = datetime.now(timezone.utc)
        stamp_freshness(investor, self.source_name())

        return True
