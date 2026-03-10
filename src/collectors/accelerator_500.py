"""Collect startup data from 500 Global portfolio directory."""

import logging
from datetime import date

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

PORTFOLIO_URL = "https://500.co/founders"


class FiveHundredGlobalCollector(BaseCollector):
    """Collect startup profiles from 500 Global (formerly 500 Startups) portfolio."""

    def source_type(self) -> str:
        return "500_global"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        headers = {
            "User-Agent": "raisefn-tracker/1.0 (startup research)",
            "Accept": "text/html",
        }

        async with httpx.AsyncClient(timeout=30.0, headers=headers, follow_redirects=True) as client:
            try:
                resp = await client.get(PORTFOLIO_URL)
                if resp.status_code != 200:
                    logger.warning(f"500 Global returned {resp.status_code}")
                    return rounds

                companies = self._parse_portfolio_page(resp.text)
                for company in companies:
                    raw_round = self._to_raw_round(company)
                    if raw_round:
                        rounds.append(raw_round)

            except Exception as e:
                logger.warning(f"500 Global error: {e}")

        logger.info(f"500 Global: collected {len(rounds)} companies")
        return rounds

    def _parse_portfolio_page(self, html: str) -> list[dict]:
        """Parse the 500 Global portfolio HTML page."""
        soup = BeautifulSoup(html, "html.parser")
        companies = []

        cards = soup.select("[class*='company'], [class*='founder'], [class*='portfolio'], [class*='card']")

        if not cards:
            cards = soup.find_all("article") or soup.find_all("div", class_=lambda c: c and "item" in c.lower())

        for card in cards:
            name_el = card.find(["h2", "h3", "h4", "a"])
            if not name_el:
                continue

            name = name_el.get_text(strip=True)
            if not name or len(name) < 2:
                continue

            description = ""
            desc_el = card.find("p")
            if desc_el:
                description = desc_el.get_text(strip=True)

            link = ""
            link_el = card.find("a", href=True)
            if link_el:
                link = link_el["href"]

            location = ""
            loc_el = card.find(class_=lambda c: c and "location" in c.lower()) if card.get("class") else None
            if loc_el:
                location = loc_el.get_text(strip=True)

            companies.append({
                "name": name,
                "description": description,
                "website": link,
                "location": location,
            })

        return companies

    def _to_raw_round(self, company: dict) -> RawRound | None:
        """Convert parsed company data to a RawRound."""
        name = company.get("name", "").strip()
        if not name:
            return None

        return RawRound(
            project_name=name,
            date=date.today(),
            round_type="accelerator",
            amount_usd=150_000,
            lead_investors=["500 Global"],
            raw_data={
                "website": company.get("website", ""),
                "accelerator": "500 Global",
                "one_liner": company.get("description", "")[:500] or None,
                "location": company.get("location", ""),
                "source": "500_global",
            },
        )
