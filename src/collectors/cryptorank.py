"""CryptoRank fundraising collector — crypto funding rounds with investor data.

CryptoRank tracks 10,000+ crypto/web3 funding rounds with full investor
details, categories, and amounts. Data is extracted from their public
Next.js pages via embedded __NEXT_DATA__ JSON.

Source: cryptorank.io (public pages, no auth required).
"""

import json
import logging
from datetime import date, datetime

import httpx
from bs4 import BeautifulSoup

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

CRYPTORANK_URL = "https://cryptorank.io/funding-rounds"

# Map CryptoRank stage names to our round types
STAGE_MAP = {
    "SEED": "seed",
    "PRE_SEED": "pre_seed",
    "SERIES_A": "series_a",
    "SERIES_B": "series_b",
    "SERIES_C": "series_c",
    "SERIES_D": "series_d",
    "STRATEGIC": "strategic",
    "PRIVATE": "private_sale",
    "PUBLIC": "public_sale",
    "ICO": "ico",
    "IEO": "ieo",
    "IDO": "ido",
    "UNDISCLOSED": "undisclosed",
    "GRANT": "grant",
}


class CryptoRankCollector(BaseCollector):
    """Collect crypto/web3 funding rounds from CryptoRank."""

    def source_type(self) -> str:
        return "cryptorank"

    async def collect(self) -> list[RawRound]:
        """Fetch the most recent funding rounds from CryptoRank.

        The __NEXT_DATA__ payload always contains the latest ~20 rounds.
        Pagination is client-side only (no public API), so we extract
        what's available on the initial page load. The ingestion pipeline
        handles deduplication against existing rounds.
        """
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            },
        ) as client:
            try:
                resp = await client.get(CRYPTORANK_URL)
                if resp.status_code in (403, 429):
                    logger.warning(f"CryptoRank returned {resp.status_code}")
                    return []
                resp.raise_for_status()
            except Exception as e:
                logger.error(f"CryptoRank fetch failed: {e}")
                return []

        soup = BeautifulSoup(resp.text, "html.parser")
        next_data = soup.find("script", id="__NEXT_DATA__")
        if not next_data or not next_data.string:
            logger.warning("CryptoRank: no __NEXT_DATA__ found")
            return []

        data = json.loads(next_data.string)
        items = (
            data.get("props", {})
            .get("pageProps", {})
            .get("fallbackRounds", {})
            .get("data", [])
        )

        rounds: list[RawRound] = []
        for item in items:
            raw_round = self._parse_round(item)
            if raw_round:
                rounds.append(raw_round)

        logger.info(f"Total: {len(rounds)} rounds from CryptoRank")
        return rounds

    def _parse_round(self, item: dict) -> RawRound | None:
        """Parse a single CryptoRank round."""
        name = (item.get("name") or "").strip()
        if not name:
            return None

        # Skip hidden or auth-protected rounds
        if item.get("isHidden") or item.get("isAuthProtected"):
            return None

        # Parse amount
        amount = item.get("raise")
        if amount:
            try:
                amount = int(float(amount))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Parse round type
        stage = item.get("stage") or ""
        fallback = stage.lower().replace(" ", "_") if stage else "undisclosed"
        round_type = STAGE_MAP.get(stage, fallback)

        # Parse date
        round_date = date.today()
        date_str = item.get("date")
        if date_str:
            try:
                round_date = datetime.fromisoformat(
                    date_str.replace("Z", "+00:00")
                ).date()
            except Exception:
                pass

        # Extract investors
        lead_investors: list[str] = []
        other_investors: list[str] = []
        for fund in item.get("funds") or []:
            fund_name = (fund.get("name") or "").strip()
            if not fund_name:
                continue
            if fund.get("type") == "LEAD":
                lead_investors.append(fund_name)
            else:
                other_investors.append(fund_name)

        return RawRound(
            project_name=name,
            date=round_date,
            amount_usd=amount,
            round_type=round_type,
            lead_investors=lead_investors,
            other_investors=other_investors,
            sector="blockchain",
            raw_data={
                "source": "cryptorank",
                "cryptorank_key": item.get("key"),
                "symbol": item.get("symbol"),
                "country": item.get("country"),
                "twitter_score": item.get("twitterScore"),
            },
        )
