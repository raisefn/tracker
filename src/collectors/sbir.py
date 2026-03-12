"""SBIR/STTR federal grant collector — early-stage startup signal.

Companies receiving SBIR Phase I/II grants are often 6-12 months away
from raising a seed round. This is predictive signal for pre-seed/seed
that almost nobody in fundraising intelligence tracks.

Source: sbir.gov public API (no auth required).
"""

import logging
from datetime import date, datetime

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

SBIR_API = "https://api.www.sbir.gov/public/api/awards"

# Map SBIR phases to round-type equivalents
PHASE_MAP = {
    "Phase I": "grant_phase_1",
    "Phase II": "grant_phase_2",
    "Phase I/Phase II": "grant_phase_1_2",
}


class SBIRCollector(BaseCollector):
    """Collect SBIR/STTR grant awards as early-stage funding signal."""

    def __init__(self, rows: int = 500):
        self.rows = rows

    def source_type(self) -> str:
        return "sbir"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=60,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            try:
                rounds = await self._fetch_awards(client)
            except Exception as e:
                logger.error(f"SBIR collection failed: {e}")

        logger.info(f"Total: {len(rounds)} awards from SBIR/STTR")
        return rounds

    async def _fetch_awards(self, client: httpx.AsyncClient) -> list[RawRound]:
        """Fetch recent SBIR/STTR awards."""
        rounds: list[RawRound] = []
        start = 0

        while start < self.rows:
            try:
                resp = await client.get(
                    SBIR_API,
                    params={
                        "rows": min(100, self.rows - start),
                        "start": start,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (404, 403, 429):
                    logger.warning(f"SBIR API returned {e.response.status_code}, stopping")
                    break
                raise
            except Exception as e:
                logger.warning(f"SBIR fetch error at offset {start}: {e}")
                break

            items = data if isinstance(data, list) else data.get("results", [])
            if not items:
                break

            for item in items:
                raw_round = self._parse_award(item)
                if raw_round:
                    rounds.append(raw_round)

            start += len(items)

        return rounds

    def _parse_award(self, item: dict) -> RawRound | None:
        """Parse a single SBIR/STTR award into RawRound."""
        company = (item.get("firm") or item.get("company") or "").strip()
        if not company:
            return None

        # Parse amount
        amount = item.get("award_amount") or item.get("amount")
        if amount:
            try:
                amount = int(float(str(amount).replace(",", "").replace("$", "")))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Parse phase to round type
        phase = item.get("phase") or item.get("program")
        round_type = PHASE_MAP.get(phase, "grant") if phase else "grant"

        # Parse date
        award_date = date.today()
        date_str = item.get("award_start_date") or item.get("award_year")
        if date_str:
            try:
                if len(str(date_str)) == 4:  # Year only
                    award_date = date(int(date_str), 1, 1)
                else:
                    award_date = datetime.fromisoformat(
                        str(date_str).replace("Z", "+00:00")
                    ).date()
            except Exception:
                pass

        # Map agency to sector hints
        agency = item.get("agency") or ""
        sector = self._infer_sector(agency, item.get("abstract") or "")

        return RawRound(
            project_name=company,
            date=award_date,
            amount_usd=amount,
            round_type=round_type,
            lead_investors=[],
            other_investors=[],
            sector=sector,
            project_url=item.get("company_url") or item.get("website"),
            raw_data={
                "source": "sbir",
                "program": item.get("program"),  # SBIR or STTR
                "phase": phase,
                "agency": agency,
                "abstract": (item.get("abstract") or "")[:500],
                "award_id": item.get("award_id") or item.get("id"),
                "state": item.get("state") or item.get("firm_state"),
                "pi_name": item.get("pi_name"),
            },
        )

    @staticmethod
    def _infer_sector(agency: str, abstract: str) -> str | None:
        """Rough sector inference from granting agency and abstract."""
        agency_lower = agency.lower()
        abstract_lower = abstract.lower()

        if "nih" in agency_lower or "health" in agency_lower:
            return "healthcare"
        if "doe" in agency_lower or "energy" in agency_lower:
            return "energy"
        if "dod" in agency_lower or "defense" in agency_lower or "darpa" in agency_lower:
            return "defense"
        if "nasa" in agency_lower:
            return "aerospace"
        if "nsf" in agency_lower:
            # NSF is broad — check abstract for hints
            if any(w in abstract_lower for w in ["blockchain", "crypto", "defi"]):
                return "blockchain"
            if any(w in abstract_lower for w in ["ai", "machine learning", "neural"]):
                return "ai"
            return "deeptech"
        if "usda" in agency_lower:
            return "agriculture"

        return None
