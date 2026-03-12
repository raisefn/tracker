"""NSF Awards collector — SBIR/STTR grants as pre-seed signal.

The NSF Awards API provides structured data on all NSF-funded grants
including SBIR Phase I ($305K typical) and Phase II ($1M+ typical).
Companies receiving these are often 6-12 months from raising seed.

Source: api.nsf.gov (free, no auth, well-documented).
"""

import logging
from datetime import date, datetime

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

NSF_API = "https://api.nsf.gov/services/v1/awards.json"
FIELDS = (
    "id,title,agency,awardeeName,awardeeCity,awardeeStateCode,"
    "startDate,expDate,fundsObligatedAmt,abstractText,"
    "piFirstName,piLastName"
)


class NSFAwardsCollector(BaseCollector):
    """Collect NSF SBIR/STTR awards as early-stage funding signal."""

    def __init__(self, months_back: int = 6, max_results: int = 500):
        self.months_back = months_back
        self.max_results = max_results

    def source_type(self) -> str:
        return "nsf_sbir"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []
        offset = 1  # NSF API is 1-indexed

        # Build date range
        today = date.today()
        start_month = today.month - self.months_back
        start_year = today.year
        while start_month <= 0:
            start_month += 12
            start_year -= 1
        date_start = f"{start_month:02d}/01/{start_year}"

        async with httpx.AsyncClient(
            timeout=60,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            while offset <= self.max_results:
                try:
                    batch = await self._fetch_batch(client, date_start, offset)
                    if not batch:
                        break
                    rounds.extend(batch)
                    offset += len(batch)
                except Exception as e:
                    logger.error(f"NSF Awards fetch failed at offset {offset}: {e}")
                    break

        logger.info(f"Total: {len(rounds)} SBIR/STTR awards from NSF")
        return rounds

    async def _fetch_batch(
        self, client: httpx.AsyncClient, date_start: str, offset: int
    ) -> list[RawRound]:
        """Fetch a batch of awards from the NSF API."""
        resp = await client.get(
            NSF_API,
            params={
                "keyword": "SBIR OR STTR",
                "printFields": FIELDS,
                "dateStart": date_start,
                "offset": offset,
                "rpp": 25,
            },
        )
        if resp.status_code in (429, 503):
            logger.warning(f"NSF API returned {resp.status_code}, stopping")
            return []
        resp.raise_for_status()

        data = resp.json()
        awards = data.get("response", {}).get("award", [])
        if not awards:
            return []

        rounds: list[RawRound] = []
        for award in awards:
            raw_round = self._parse_award(award)
            if raw_round:
                rounds.append(raw_round)

        return rounds

    def _parse_award(self, award: dict) -> RawRound | None:
        """Parse a single NSF award into RawRound."""
        company = (award.get("awardeeName") or "").strip()
        if not company:
            return None

        # Parse amount
        amount = award.get("fundsObligatedAmt")
        if amount:
            try:
                amount = int(float(str(amount).replace(",", "")))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Classify phase from title
        title = award.get("title") or ""
        round_type = "grant"
        title_lower = title.lower()
        if "phase ii" in title_lower or "phase 2" in title_lower:
            round_type = "grant_phase_2"
        elif "phase i" in title_lower or "phase 1" in title_lower:
            round_type = "grant_phase_1"

        # Parse date
        award_date = date.today()
        date_str = award.get("startDate")
        if date_str:
            try:
                award_date = datetime.strptime(date_str, "%m/%d/%Y").date()
            except Exception:
                pass

        # Infer sector from title/abstract
        abstract = award.get("abstractText") or ""
        sector = self._infer_sector(title, abstract)

        # Build PI name
        pi_name = None
        pi_first = award.get("piFirstName") or ""
        pi_last = award.get("piLastName") or ""
        if pi_first and pi_last:
            pi_name = f"{pi_first} {pi_last}"

        return RawRound(
            project_name=company,
            date=award_date,
            amount_usd=amount,
            round_type=round_type,
            lead_investors=[],
            other_investors=[],
            sector=sector,
            raw_data={
                "source": "nsf_sbir",
                "nsf_award_id": award.get("id"),
                "title": title[:200],
                "abstract": abstract[:500],
                "pi_name": pi_name,
                "city": award.get("awardeeCity"),
                "state": award.get("awardeeStateCode"),
            },
        )

    @staticmethod
    def _infer_sector(title: str, abstract: str) -> str | None:
        """Infer sector from award title and abstract."""
        text = (title + " " + abstract).lower()

        if any(w in text for w in ["blockchain", "crypto", "defi", "web3"]):
            return "blockchain"
        _ai = ["machine learning", "artificial intelligence", "neural network",
               "deep learning", "nlp", "computer vision"]
        if any(w in text for w in _ai):
            return "ai"
        _health = ["biotech", "therapeutic", "drug", "clinical",
                    "pharmaceutical", "genomic"]
        if any(w in text for w in _health):
            return "healthcare"
        _energy = ["solar", "battery", "renewable", "clean energy",
                    "carbon capture"]
        if any(w in text for w in _energy):
            return "energy"
        if any(w in text for w in ["quantum", "photon", "semiconductor"]):
            return "deeptech"
        if any(w in text for w in ["satellite", "spacecraft", "orbital", "launch vehicle"]):
            return "aerospace"
        if any(w in text for w in ["cyber", "encryption", "authentication"]):
            return "security"

        return "deeptech"  # NSF default
