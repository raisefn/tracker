"""SEC 13F filing collector for institutional investor holdings.

Institutional investment managers with >$100M AUM must file quarterly
13F reports disclosing their equity holdings. This includes hedge funds,
mutual funds, insurance companies, and many family offices.

Data source: https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets
EDGAR API: https://data.sec.gov/submissions/CIK##########.json
"""

import io
import logging
import zipfile
from datetime import datetime, timezone

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import (
    BaseEnricher,
    EnrichmentResult,
    find_investor_match,
    stamp_freshness,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "raisefn/tracker contact@raisefn.com",
    "Accept-Encoding": "gzip, deflate",
}

# 13F data sets are published quarterly
# Format: https://www.sec.gov/files/structureddata/data/form-13f-data-sets/2024q4_form13f.zip
SEC_13F_BASE = "https://www.sec.gov/files/structureddata/data/form-13f-data-sets"

# EDGAR submissions API for individual filer lookup
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions"


def _latest_quarter() -> str:
    """Get the latest available 13F quarter string (e.g., '2025q3')."""
    now = datetime.now()
    # 13F is filed ~45 days after quarter end, so lag by one quarter
    year = now.year
    quarter = (now.month - 1) // 3  # 0-indexed current quarter
    if quarter == 0:
        return f"{year - 1}q4"
    return f"{year}q{quarter}"


class SEC13FEnricher(BaseEnricher):
    """Enrich investor records with SEC 13F institutional holdings data.

    Downloads the quarterly 13F data set and extracts filer-level
    aggregates: total portfolio value, number of holdings, and top
    positions by value.
    """

    def source_name(self) -> str:
        return "sec_13f"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        try:
            filers = await self._download_13f_data()
        except Exception as e:
            error_msg = f"Failed to download 13F data: {e}"
            logger.error(error_msg)
            result.errors.append(error_msg)
            return result

        logger.info(f"Downloaded {len(filers)} 13F filer records")

        for cik, filer_data in filers.items():
            try:
                updated = await self._process_filer(session, cik, filer_data)
                if updated:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                error_msg = f"Error processing 13F filer {cik}: {e}"
                logger.warning(error_msg)
                result.errors.append(error_msg)
                result.records_skipped += 1

        await session.flush()
        logger.info(
            f"13F enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _download_13f_data(self) -> dict[str, dict]:
        """Download the latest quarterly 13F data set and aggregate by filer."""
        quarter = _latest_quarter()
        url = f"{SEC_13F_BASE}/{quarter}_form13f.zip"

        async with httpx.AsyncClient(timeout=120.0, headers=HEADERS) as client:
            resp = await client.get(url, follow_redirects=True)
            resp.raise_for_status()

            filers: dict[str, dict] = {}

            zf = zipfile.ZipFile(io.BytesIO(resp.content))

            # Parse SUBMISSION.tsv for filer metadata
            submission_file = None
            infotable_file = None
            for name in zf.namelist():
                lower = name.lower()
                if "submission" in lower:
                    submission_file = name
                elif "infotable" in lower:
                    infotable_file = name

            # Read submission data (filer info)
            if submission_file:
                with zf.open(submission_file) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                    header = text.readline().strip().split("\t")
                    for line in text:
                        parts = line.strip().split("\t")
                        if len(parts) < len(header):
                            continue
                        row = dict(zip(header, parts))
                        cik = row.get("CIK", "").strip()
                        if cik:
                            filers[cik] = {
                                "name": row.get(
                                    "FILINGMANAGER_NAME",
                                    row.get("COMPANYNAME", ""),
                                ).strip(),
                                "cik": cik,
                                "report_date": row.get("REPORTCALENDARORQUARTER", "").strip(),
                                "holdings": [],
                                "total_value": 0,
                            }

            # Read holdings data (infotable)
            if infotable_file and filers:
                with zf.open(infotable_file) as f:
                    text = io.TextIOWrapper(f, encoding="utf-8", errors="replace")
                    header = text.readline().strip().split("\t")
                    for line in text:
                        parts = line.strip().split("\t")
                        if len(parts) < len(header):
                            continue
                        row = dict(zip(header, parts))
                        cik = row.get("CIK", row.get("ACCESSION_NUMBER", "")[:10]).strip()

                        # Try to find the filer
                        if cik not in filers:
                            continue

                        try:
                            # 13F values in thousands
                            value = int(
                                float(row.get("VALUE", "0")) * 1000
                            )
                        except (ValueError, TypeError):
                            value = 0

                        issuer = row.get("NAMEOFISSUER", "").strip()
                        if issuer and value > 0:
                            filers[cik]["holdings"].append({
                                "issuer": issuer,
                                "value": value,
                                "cusip": row.get("CUSIP", "").strip(),
                            })
                            filers[cik]["total_value"] += value

            # Sort holdings by value and keep top 10 for each filer
            for cik in filers:
                holdings = filers[cik]["holdings"]
                holdings.sort(key=lambda h: h["value"], reverse=True)
                filers[cik]["top_holdings"] = holdings[:10]
                filers[cik]["num_holdings"] = len(holdings)

            return filers

    async def _process_filer(self, session: AsyncSession, cik: str, data: dict) -> bool:
        """Process a single 13F filer — enrich existing investors only."""
        name = data.get("name", "").strip()
        if not name or len(name) < 3:
            return False

        # Skip very small portfolios
        total_value = data.get("total_value", 0)
        if total_value < 1_000_000:  # Less than $1M portfolio
            return False

        investor = await find_investor_match(session, name, sec_cik=cik)

        # Enrichment-only: skip if no existing investor matched
        if investor is None:
            return False

        # Update 13F fields
        investor.sec_cik = cik
        investor.portfolio_value = total_value
        investor.num_holdings = data.get("num_holdings", 0)
        investor.last_13f_date = data.get("report_date")

        # Top holdings as JSON
        top = data.get("top_holdings", [])
        if top:
            investor.top_holdings = [
                {"issuer": h["issuer"], "value": h["value"]}
                for h in top
            ]

        # Classify type if not already set
        if not investor.type:
            investor.type = "vc"  # Will be refined by Form ADV enricher

        investor.last_enriched_at = datetime.now(timezone.utc)
        stamp_freshness(investor, self.source_name())

        return True
