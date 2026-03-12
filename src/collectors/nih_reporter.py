"""NIH Reporter collector — biotech/healthcare SBIR grants.

The NIH RePORTER API provides structured data on all NIH-funded grants
including SBIR Phase I (R43, ~$300K) and Phase II (R44, ~$1M+).
These are the strongest pre-seed signals in healthcare/biotech.

Source: api.reporter.nih.gov (free, no auth).
"""

import logging
from datetime import date

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

NIH_API = "https://api.reporter.nih.gov/v2/projects/search"

# SBIR/STTR activity codes
SBIR_CODES = ["R43", "R44"]  # Phase I, Phase II
STTR_CODES = ["R41", "R42"]  # STTR Phase I, Phase II


class NIHReporterCollector(BaseCollector):
    """Collect NIH SBIR/STTR awards as healthcare pre-seed signal."""

    def __init__(self, fiscal_years: list[int] | None = None, max_results: int = 500):
        if fiscal_years is None:
            current_year = date.today().year
            self.fiscal_years = [current_year - 1, current_year]
        else:
            self.fiscal_years = fiscal_years
        self.max_results = max_results

    def source_type(self) -> str:
        return "nih_sbir"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []
        offset = 0

        async with httpx.AsyncClient(
            timeout=60,
            headers={
                "User-Agent": "raisefn/tracker (contact@raisefn.com)",
                "Content-Type": "application/json",
            },
        ) as client:
            while offset < self.max_results:
                try:
                    batch = await self._fetch_batch(client, offset)
                    if not batch:
                        break
                    rounds.extend(batch)
                    offset += len(batch)
                except Exception as e:
                    logger.error(f"NIH Reporter fetch failed at offset {offset}: {e}")
                    break

        logger.info(f"Total: {len(rounds)} SBIR/STTR awards from NIH")
        return rounds

    async def _fetch_batch(
        self, client: httpx.AsyncClient, offset: int
    ) -> list[RawRound]:
        """Fetch a batch of projects from NIH Reporter."""
        resp = await client.post(
            NIH_API,
            json={
                "criteria": {
                    "fiscal_years": self.fiscal_years,
                    "activity_codes": SBIR_CODES + STTR_CODES,
                },
                "limit": 50,
                "offset": offset,
                "sort_field": "project_start_date",
                "sort_order": "desc",
            },
        )
        if resp.status_code in (429, 503):
            logger.warning(f"NIH Reporter returned {resp.status_code}, stopping")
            return []
        resp.raise_for_status()

        data = resp.json()
        results = data.get("results", [])
        if not results:
            return []

        rounds: list[RawRound] = []
        for project in results:
            raw_round = self._parse_project(project)
            if raw_round:
                rounds.append(raw_round)

        return rounds

    def _parse_project(self, project: dict) -> RawRound | None:
        """Parse a single NIH project into RawRound."""
        org = project.get("organization") or {}
        company = (org.get("org_name") or "").strip()
        if not company:
            return None

        # Parse amount
        amount = project.get("award_amount")
        if amount:
            try:
                amount = int(float(amount))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Classify phase from activity code
        activity = project.get("activity_code") or ""
        if activity in ("R44", "R42"):
            round_type = "grant_phase_2"
        elif activity in ("R43", "R41"):
            round_type = "grant_phase_1"
        else:
            round_type = "grant"

        # Determine if SBIR or STTR
        program = "STTR" if activity in ("R41", "R42") else "SBIR"

        # Parse date
        award_date = date.today()
        date_str = project.get("project_start_date")
        if date_str:
            try:
                # Format: "2025-10-01T00:00:00Z"
                award_date = date.fromisoformat(date_str[:10])
            except Exception:
                pass

        # PI info
        pi = project.get("principal_investigators") or []
        pi_name = None
        if pi and isinstance(pi, list):
            first_pi = pi[0] if pi else {}
            pi_first = first_pi.get("first_name") or ""
            pi_last = first_pi.get("last_name") or ""
            if pi_first and pi_last:
                pi_name = f"{pi_first} {pi_last}"

        return RawRound(
            project_name=company,
            date=award_date,
            amount_usd=amount,
            round_type=round_type,
            lead_investors=[],
            other_investors=[],
            sector="healthcare",
            raw_data={
                "source": "nih_sbir",
                "nih_project_num": project.get("project_num"),
                "activity_code": activity,
                "program": program,
                "title": (project.get("project_title") or "")[:200],
                "abstract": (project.get("abstract_text") or "")[:500],
                "pi_name": pi_name,
                "city": org.get("org_city"),
                "state": org.get("org_state"),
                "nih_institute": (
                    project.get("agency_ic_fundings", [{}])[0].get("name")
                    if project.get("agency_ic_fundings") else None
                ),
            },
        )
