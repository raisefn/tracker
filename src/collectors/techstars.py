"""Collect startup data from Techstars portfolio directory."""

import logging
from datetime import date

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

# Techstars has a public API backing their portfolio page
TECHSTARS_API = "https://www.techstars.com/api/companies"


class TechstarsCollector(BaseCollector):
    """Collect startup profiles from Techstars portfolio."""

    def source_type(self) -> str:
        return "techstars"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        headers = {"User-Agent": "raisefn-tracker/1.0 (startup research)"}
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            page = 0
            while True:
                try:
                    resp = await client.get(
                        TECHSTARS_API,
                        params={"page": page, "pageSize": 100},
                    )
                    if resp.status_code != 200:
                        logger.warning(f"Techstars API returned {resp.status_code} on page {page}")
                        break

                    data = resp.json()
                    companies = (
                        data
                        if isinstance(data, list)
                        else data.get("companies", data.get("results", []))
                    )

                    if not companies:
                        break

                    for company in companies:
                        raw_round = self._parse_company(company)
                        if raw_round:
                            rounds.append(raw_round)

                    page += 1

                    if page > 100:
                        break

                except Exception as e:
                    logger.warning(f"Techstars page {page} error: {e}")
                    break

        logger.info(f"Techstars: collected {len(rounds)} companies")
        return rounds

    def _parse_company(self, company: dict) -> RawRound | None:
        """Parse a Techstars company entry into a RawRound."""
        name = company.get("name", "").strip()
        if not name:
            return None

        program = company.get("program", company.get("accelerator_name", ""))
        batch_year = company.get("year", company.get("batch_year", ""))
        batch_season = company.get("season", company.get("batch_season", ""))
        batch = ""
        if batch_season and batch_year:
            batch = f"{batch_season} {batch_year}"
        elif batch_year:
            batch = str(batch_year)

        round_date = date.today()
        if batch_year:
            try:
                round_date = date(int(batch_year), 6, 1)
            except (ValueError, TypeError):
                pass

        website = company.get("url", company.get("website", ""))
        description = company.get("description", company.get("blurb", ""))
        location = company.get("location", company.get("city", ""))

        return RawRound(
            project_name=name,
            date=round_date,
            round_type="accelerator",
            amount_usd=120_000,
            lead_investors=["Techstars"],
            raw_data={
                "website": website,
                "accelerator": f"Techstars {program}" if program else "Techstars",
                "accelerator_batch": batch,
                "one_liner": description[:500] if description else None,
                "location": location,
                "source": "techstars",
            },
        )
