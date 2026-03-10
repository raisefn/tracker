"""Y Combinator company directory collector.

Fetches structured startup data from the yc-oss API:
https://yc-oss.github.io/api/companies/all.json

5,690+ companies with name, URL, batch, industry, tags, one-liner,
status, team size, and location. No auth required.
"""

import logging
from datetime import date

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

YC_API_URL = "https://yc-oss.github.io/api/companies/all.json"

# Map YC industries to our sector taxonomy
YC_INDUSTRY_MAP = {
    "B2B": "enterprise",
    "B2B Software and Services": "enterprise",
    "SaaS": "saas",
    "Enterprise Software": "enterprise",
    "Fintech": "fintech",
    "Healthcare": "healthtech",
    "Health Tech": "healthtech",
    "Biotech": "biotech",
    "Education": "edtech",
    "Consumer": "consumer",
    "E-Commerce": "ecommerce",
    "Marketplace": "marketplace",
    "Developer Tools": "devtools",
    "Infrastructure": "infrastructure",
    "AI": "ai",
    "Artificial Intelligence": "ai",
    "Machine Learning": "ai",
    "Hardware": "hardware",
    "Robotics": "hardware",
    "Real Estate": "proptech",
    "Climate": "climate",
    "Energy": "climate",
    "Security": "security",
    "Crypto / Web3": "defi",
    "Gaming": "gaming",
    "Social": "social",
    "Media": "media",
    "Food and Beverage": "foodtech",
    "Logistics": "logistics",
    "Insurance": "insurtech",
    "Legal": "legaltech",
    "HR Tech": "hrtech",
    "Government": "enterprise",
}


def _batch_to_date(batch: str | None) -> date:
    """Convert YC batch code (e.g., 'W2024', 'S2023') to approximate date."""
    if not batch:
        return date.today()
    try:
        season = batch[0].upper()
        year = int(batch[1:])
        month = 1 if season == "W" else 6
        return date(year, month, 1)
    except (ValueError, IndexError):
        return date.today()


class YCDirectoryCollector(BaseCollector):
    """Collect company profiles from the Y Combinator directory.

    Note: This creates projects without rounds. Rounds are linked
    later via SEC EDGAR or news ingestion + entity matching.
    The RawRound is used as a vehicle to get projects into the system
    with amount_usd=None (no round data).
    """

    def source_type(self) -> str:
        return "yc_directory"

    async def collect(self) -> list[RawRound]:
        """Fetch all YC companies."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(YC_API_URL)
            resp.raise_for_status()
            companies = resp.json()

        logger.info(f"Fetched {len(companies)} companies from YC directory")

        rounds: list[RawRound] = []
        for company in companies:
            name = company.get("name", "").strip()
            if not name:
                continue

            # Skip dead companies
            status = company.get("status", "").lower()
            if status in ("dead", "acquired"):
                continue

            batch = company.get("batch")
            industry = company.get("industry", "")
            sector = YC_INDUSTRY_MAP.get(industry)

            rounds.append(RawRound(
                project_name=name,
                date=_batch_to_date(batch),
                project_url=company.get("url"),
                sector=sector,
                raw_data={
                    "source": "yc_directory",
                    "batch": batch,
                    "industry": industry,
                    "tags": company.get("tags", []),
                    "one_liner": company.get("one_liner"),
                    "team_size": company.get("team_size"),
                    "location": company.get("location"),
                    "status": company.get("status"),
                    "accelerator": "Y Combinator",
                    "accelerator_batch": batch,
                },
            ))

        logger.info(f"Parsed {len(rounds)} active companies from YC directory")
        return rounds
