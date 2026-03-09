"""SEC EDGAR Form D collector.

Collects startup fundraising data from SEC Form D filings.
Every US company raising under Regulation D must file Form D,
disclosing offering details, amounts, and related persons.

Two modes:
1. Bulk import: Download quarterly CSV data sets (historical backfill)
2. EFTS search: Real-time search for recent Form D filings (ongoing polling)
"""

import io
import logging
import zipfile
from datetime import date, datetime, timedelta

import httpx

from src.collectors.base import BaseCollector, RawFounder, RawRound

logger = logging.getLogger(__name__)

EDGAR_HEADERS = {
    "User-Agent": "raisefn/tracker contact@raisefn.com",
    "Accept-Encoding": "gzip, deflate",
}

# SEC industry group codes → our sector taxonomy
SEC_INDUSTRY_MAP = {
    "Health Care": "healthtech",
    "Biotechnology": "biotech",
    "Pharmaceuticals": "biotech",
    "Technology": "saas",
    "Banking & Financial Services": "fintech",
    "Insurance": "insurtech",
    "Real Estate": "proptech",
    "Energy": "climate",
    "Retailing": "ecommerce",
    "Restaurants": "foodtech",
    "Agriculture": "climate",
    "Manufacturing": "hardware",
    "Electric Utilities": "climate",
    "Telecommunications": "infrastructure",
    "Commercial": "enterprise",
    "Construction": "proptech",
    "REITS & Finance": "fintech",
    "Travel & Leisure": "consumer",
    "Other": None,
    "Pooled Investment Fund": None,
}

# SEC revenue range codes
SEC_REVENUE_RANGES = {
    "Decline to Disclose": None,
    "No Revenues": "$0",
    "Not Applicable": None,
    "$1 - $1,000,000": "$0-1M",
    "$1,000,001 - $5,000,000": "$1-5M",
    "$5,000,001 - $25,000,000": "$5-25M",
    "$25,000,001 - $100,000,000": "$25-100M",
    "Over $100,000,000": "$100M+",
}


def _parse_date(date_str: str | None) -> date | None:
    """Parse SEC date formats (YYYY-MM-DD or MM-DD-YYYY)."""
    if not date_str or date_str.strip() == "":
        return None
    date_str = date_str.strip()
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(amount_str: str | None) -> int | None:
    """Parse SEC amount field to integer USD."""
    if not amount_str or amount_str.strip() == "":
        return None
    try:
        val = float(amount_str.strip().replace(",", ""))
        if val <= 0:
            return None
        return int(val)
    except (ValueError, TypeError):
        return None


class SECEdgarCollector(BaseCollector):
    """Collect Form D filings from SEC EDGAR.

    Uses the EFTS full-text search API for recent filings.
    For historical bulk import, use SECEdgarBulkCollector.
    """

    def __init__(self, days_back: int = 7):
        self.days_back = days_back

    def source_type(self) -> str:
        return "sec_edgar"

    async def collect(self) -> list[RawRound]:
        """Fetch recent Form D filings via EFTS search."""
        rounds: list[RawRound] = []
        date_from = (date.today() - timedelta(days=self.days_back)).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(headers=EDGAR_HEADERS, timeout=30) as client:
            # Search for Form D filings
            url = "https://efts.sec.gov/LATEST/search-index"
            params = {
                "q": "*",
                "forms": "D,D/A",
                "dateRange": "custom",
                "startdt": date_from,
                "enddt": date.today().strftime("%Y-%m-%d"),
            }

            try:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"EFTS search failed: {e}")
                # Fall back to daily index
                return await self._collect_from_daily_index(client)

            hits = data.get("hits", {}).get("hits", [])
            logger.info(f"EFTS returned {len(hits)} Form D filings")

            for hit in hits:
                source = hit.get("_source", {})
                raw_round = self._parse_efts_hit(source)
                if raw_round:
                    rounds.append(raw_round)

        logger.info(f"Parsed {len(rounds)} rounds from EFTS search")
        return rounds

    async def _collect_from_daily_index(self, client: httpx.AsyncClient) -> list[RawRound]:
        """Fallback: fetch from EDGAR daily index."""
        rounds: list[RawRound] = []
        today = date.today()

        for days_ago in range(self.days_back):
            check_date = today - timedelta(days=days_ago)
            # EDGAR daily index URL pattern
            url = (
                f"https://www.sec.gov/Archives/edgar/daily-index/"
                f"{check_date.year}/QTR{(check_date.month - 1) // 3 + 1}/"
                f"form.{check_date.strftime('%Y%m%d')}.idx"
            )

            try:
                resp = await client.get(url)
                if resp.status_code == 404:
                    continue  # No filings on weekends/holidays
                resp.raise_for_status()

                for line in resp.text.splitlines():
                    if "D " in line or "D/A" in line:
                        parts = line.split("|")
                        if len(parts) >= 5:
                            company_name = parts[1].strip()
                            filing_date = _parse_date(parts[3].strip())
                            accession = parts[4].strip()

                            if company_name and filing_date:
                                rounds.append(RawRound(
                                    project_name=company_name,
                                    date=filing_date,
                                    source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&company={company_name}&type=D&dateb=&owner=include&count=10",
                                    raw_data={
                                        "accession_number": accession,
                                        "source": "edgar_daily_index",
                                    },
                                ))
            except Exception as e:
                logger.warning(f"Failed to fetch daily index for {check_date}: {e}")

        return rounds

    def _parse_efts_hit(self, source: dict) -> RawRound | None:
        """Parse a single EFTS search result into a RawRound."""
        company_name = source.get("display_names", [None])[0] or source.get("entity_name")
        if not company_name:
            return None

        filing_date = _parse_date(source.get("file_date"))
        if not filing_date:
            return None

        return RawRound(
            project_name=company_name.strip(),
            date=filing_date,
            source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={source.get('entity_id', '')}&type=D",
            raw_data={
                "cik": source.get("entity_id"),
                "accession_number": source.get("adsh"),
                "form_type": source.get("form_type"),
                "source": "efts_search",
            },
        )


class SECEdgarBulkCollector(BaseCollector):
    """Bulk import Form D filings from SEC quarterly data sets.

    Downloads ZIP files containing tab-delimited data from:
    https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets

    Each ZIP contains 6 files joined by ACCESSIONNUMBER:
    - SUBMISSIONS.tsv: Company info, filing dates
    - OFFERINGS.tsv: Offering amounts, industry, revenue range
    - RECIPIENTS.tsv: State, date of first sale
    - RELATED_PERSONS.tsv: Executives and directors (potential investors)
    - SIGNATURE.tsv: Signatory info
    - SALES_COMP.tsv: Sales compensation (placement agents)
    """

    def __init__(self, year: int | None = None, quarter: int | None = None):
        """If year/quarter not specified, fetches the most recent quarter."""
        self.year = year
        self.quarter = quarter

    def source_type(self) -> str:
        return "sec_edgar"

    async def collect(self) -> list[RawRound]:
        """Download and parse a quarterly Form D data set."""
        if self.year and self.quarter:
            year, quarter = self.year, self.quarter
        else:
            # Default to most recent complete quarter
            now = date.today()
            quarter = (now.month - 1) // 3  # Previous quarter
            year = now.year if quarter > 0 else now.year - 1
            quarter = quarter if quarter > 0 else 4

        url = f"https://www.sec.gov/files/data/form-d-data-sets/{year}q{quarter}_d.zip"
        logger.info(f"Downloading SEC EDGAR bulk data: {url}")

        async with httpx.AsyncClient(headers=EDGAR_HEADERS, timeout=120) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        return self._parse_zip(resp.content)

    def _parse_zip(self, zip_bytes: bytes) -> list[RawRound]:
        """Parse the quarterly ZIP file into RawRound objects."""
        rounds: list[RawRound] = []

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            file_names = zf.namelist()
            logger.info(f"ZIP contains: {file_names}")

            # Read all files into dicts keyed by ACCESSIONNUMBER
            submissions = self._read_tsv(zf, "SUBMISSIONS.tsv")
            offerings = self._read_tsv(zf, "OFFERINGS.tsv")
            related = self._read_tsv(zf, "RELATED_PERSONS.tsv")

            # Group related persons by accession number
            related_by_accession: dict[str, list[dict]] = {}
            for person in related:
                acc = person.get("ACCESSIONNUMBER", "")
                related_by_accession.setdefault(acc, []).append(person)

            # Group offerings by accession number
            offerings_by_accession: dict[str, list[dict]] = {}
            for offering in offerings:
                acc = offering.get("ACCESSIONNUMBER", "")
                offerings_by_accession.setdefault(acc, []).append(offering)

            # Build rounds from submissions
            for sub in submissions:
                accession = sub.get("ACCESSIONNUMBER", "")
                company_name = sub.get("ENTITYNAME", "").strip()
                if not company_name:
                    continue

                # Get offering data
                off_list = offerings_by_accession.get(accession, [])
                offering = off_list[0] if off_list else {}

                # Parse amount
                amount = _parse_amount(offering.get("TOTALOFFERINGAMOUNT"))
                amount_sold = _parse_amount(offering.get("TOTAMOUNTSOLD"))
                # Use amount sold if total offering is indefinite
                final_amount = amount or amount_sold

                # Parse date
                filing_date = _parse_date(sub.get("FILEDDATE"))
                sale_date = _parse_date(offering.get("DATEOFFIRSTSALE"))
                round_date = sale_date or filing_date
                if not round_date:
                    continue

                # Skip pooled investment funds (not startups)
                industry = offering.get("INDUSTRYGROUPTYPE", "")
                if industry == "Pooled Investment Fund":
                    continue

                # Get related persons — extract names AND roles
                persons = related_by_accession.get(accession, [])
                founders: list[RawFounder] = []
                executive_names: list[str] = []
                for p in persons:
                    name = p.get("RELATEDPERSONNAME", "").strip()
                    if not name:
                        continue
                    executive_names.append(name)
                    # Extract relationship type (Executive Officer, Director, Promoter)
                    relationship = p.get("RELATEDPERSONRELATIONSHIP", "").strip()
                    role = self._map_sec_role(relationship)
                    founders.append(RawFounder(name=name, role=role))

                # Map SEC industry to our sectors
                sector = SEC_INDUSTRY_MAP.get(industry)

                # Determine round type from exemption
                exemption = offering.get("FEDERALEXEMPTIONSEXCLUSIONS", "")
                round_type = self._infer_round_type(exemption, final_amount)

                # Revenue range
                revenue_range = SEC_REVENUE_RANGES.get(
                    offering.get("REVENUERANGE", ""), None
                )

                state = sub.get("STATEORCOUNTRY", "")
                cik = sub.get("CIK", "")

                rounds.append(RawRound(
                    project_name=company_name,
                    date=round_date,
                    amount_usd=final_amount,
                    round_type=round_type,
                    sector=sector,
                    founders=founders,
                    source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=D",
                    raw_data={
                        "cik": cik,
                        "accession_number": accession,
                        "state": state,
                        "industry_group": industry,
                        "revenue_range": revenue_range,
                        "federal_exemptions": exemption,
                        "executives": executive_names,
                        "total_offering_amount": str(amount) if amount else None,
                        "total_amount_sold": str(amount_sold) if amount_sold else None,
                        "date_of_first_sale": str(sale_date) if sale_date else None,
                        "source": "edgar_bulk",
                    },
                ))

        logger.info(f"Parsed {len(rounds)} rounds from SEC EDGAR bulk data")
        return rounds

    def _read_tsv(self, zf: zipfile.ZipFile, filename: str) -> list[dict]:
        """Read a TSV file from the ZIP into a list of dicts."""
        # Try with and without case sensitivity
        for name in zf.namelist():
            if name.upper().endswith(filename.upper()):
                with zf.open(name) as f:
                    content = f.read().decode("utf-8", errors="replace")
                    lines = content.strip().split("\n")
                    if len(lines) < 2:
                        return []
                    headers = lines[0].split("\t")
                    rows = []
                    for line in lines[1:]:
                        values = line.split("\t")
                        row = dict(zip(headers, values))
                        rows.append(row)
                    return rows
        logger.warning(f"File {filename} not found in ZIP")
        return []

    @staticmethod
    def _map_sec_role(relationship: str) -> str | None:
        """Map SEC relationship type to a human-readable role."""
        if not relationship:
            return None
        r = relationship.lower()
        if "executive officer" in r:
            return "Executive Officer"
        if "director" in r:
            return "Director"
        if "promoter" in r:
            return "Promoter"
        return relationship.strip() or None

    def _infer_round_type(self, exemption: str, amount: int | None) -> str | None:
        """Infer round type from SEC exemption and amount."""
        if not amount:
            return None
        # Rough heuristics based on amount
        if amount < 2_000_000:
            return "pre_seed"
        elif amount < 10_000_000:
            return "seed"
        elif amount < 30_000_000:
            return "series_a"
        elif amount < 80_000_000:
            return "series_b"
        elif amount < 200_000_000:
            return "series_c"
        else:
            return "private"
