"""Messari fundraising collector — crypto-native funding rounds.

Messari tracks fundraising rounds across the crypto/web3 ecosystem with
strong coverage of rounds that DeFiLlama misses, especially for
infrastructure and middleware projects.

Source: Messari public API (no auth required for basic endpoints).
"""

import logging
from datetime import date, datetime

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

MESSARI_API = "https://data.messari.io/api/v1"


class MessariCollector(BaseCollector):
    """Collect crypto/web3 funding rounds from Messari."""

    def __init__(self, page_limit: int = 10):
        self.page_limit = page_limit

    def source_type(self) -> str:
        return "messari"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            try:
                rounds = await self._fetch_rounds(client)
            except Exception as e:
                logger.error(f"Messari collection failed: {e}")

        logger.info(f"Total: {len(rounds)} rounds from Messari")
        return rounds

    async def _fetch_rounds(self, client: httpx.AsyncClient) -> list[RawRound]:
        """Fetch fundraising events from Messari."""
        rounds: list[RawRound] = []
        page = 1

        while page <= self.page_limit:
            try:
                resp = await client.get(
                    f"{MESSARI_API}/assets",
                    params={"page": page, "limit": 100, "fields": "id,name,slug,symbol,profile"},
                )
                if resp.status_code in (404, 429):
                    if resp.status_code == 429:
                        logger.warning("Messari rate limited, stopping")
                    break
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 401):
                    logger.warning(f"Messari API returned {e.response.status_code}, skipping")
                    break
                raise
            except Exception as e:
                logger.warning(f"Messari fetch error page {page}: {e}")
                break

            assets = data.get("data", [])
            if not assets:
                break

            for asset in assets:
                parsed = self._extract_rounds(asset)
                rounds.extend(parsed)

            page += 1

        return rounds

    def _extract_rounds(self, asset: dict) -> list[RawRound]:
        """Extract funding rounds from a Messari asset profile."""
        rounds: list[RawRound] = []
        name = (asset.get("name") or "").strip()
        if not name:
            return rounds

        profile = asset.get("profile") or {}
        economics = profile.get("economics") or {}
        launch = economics.get("launch") or {}
        fundraising = launch.get("fundraising") or {}

        # Sales rounds (token sales, private sales, etc.)
        sales = fundraising.get("sales_rounds") or []
        for sale in sales:
            raw_round = self._parse_sale(name, asset, sale)
            if raw_round:
                rounds.append(raw_round)

        # General fundraising details
        fundraising_details = fundraising.get("fundraising_details") or ""
        if fundraising_details and not sales:
            # Has fundraising info but no structured rounds — capture as a single round
            amount = fundraising.get("projected_use_of_sales_proceeds_amount")
            if amount:
                try:
                    amount = int(float(amount))
                except (ValueError, TypeError):
                    amount = None

            if amount and amount > 0:
                rounds.append(
                    RawRound(
                        project_name=name,
                        date=date.today(),
                        amount_usd=amount,
                        round_type="token_sale",
                        sector="blockchain",
                        project_url=(
                            profile.get("general", {}).get("overview", {})
                            .get("official_links", [{}])[0].get("link")
                            if profile.get("general", {}).get("overview", {}).get("official_links")
                            else None
                        ),
                        raw_data={
                            "source": "messari",
                            "messari_id": asset.get("id"),
                            "symbol": asset.get("symbol"),
                            "slug": asset.get("slug"),
                        },
                    )
                )

        return rounds

    def _parse_sale(self, project_name: str, asset: dict, sale: dict) -> RawRound | None:
        """Parse a single Messari sales round."""
        title = sale.get("title") or ""

        # Parse amount
        amount = sale.get("amount_collected_usd") or sale.get("native_amount")
        if amount:
            try:
                amount = int(float(amount))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Parse round type from title
        round_type = self._classify_round(title)

        # Parse date
        round_date = date.today()
        date_str = sale.get("start_date") or sale.get("end_date")
        if date_str:
            try:
                round_date = datetime.fromisoformat(
                    str(date_str).replace("Z", "+00:00")
                ).date()
            except Exception:
                pass

        return RawRound(
            project_name=project_name,
            date=round_date,
            amount_usd=amount,
            round_type=round_type,
            sector="blockchain",
            raw_data={
                "source": "messari",
                "messari_id": asset.get("id"),
                "symbol": asset.get("symbol"),
                "slug": asset.get("slug"),
                "sale_title": title,
                "is_kyc_required": sale.get("is_kyc_required"),
                "restricted_jurisdictions": sale.get("restricted_jurisdictions"),
            },
        )

    @staticmethod
    def _classify_round(title: str) -> str:
        """Classify a Messari sale title into a round type."""
        lower = title.lower()
        if "seed" in lower:
            return "seed"
        if "private" in lower:
            return "private_sale"
        if "public" in lower or "ico" in lower:
            return "public_sale"
        if "series" in lower:
            return lower.replace(" ", "_")
        if "strategic" in lower:
            return "strategic"
        return "token_sale"
