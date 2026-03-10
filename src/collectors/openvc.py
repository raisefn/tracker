"""OpenVC collector — free VC database with investor and portfolio data.

OpenVC provides a public API with investor profiles, portfolio companies,
and funding round data. Free tier, no auth required.
"""

import logging
from datetime import date

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

OPENVC_API = "https://api.openvc.app/v1"


class OpenVCCollector(BaseCollector):
    """Collect investor and funding data from OpenVC."""

    def source_type(self) -> str:
        return "openvc"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            try:
                rounds = await self._fetch_rounds(client)
            except Exception as e:
                logger.error(f"OpenVC collection failed: {e}")

        logger.info(f"Total: {len(rounds)} rounds from OpenVC")
        return rounds

    async def _fetch_rounds(self, client: httpx.AsyncClient) -> list[RawRound]:
        """Fetch recent funding rounds from OpenVC API."""
        rounds: list[RawRound] = []
        page = 1
        max_pages = 10

        while page <= max_pages:
            try:
                resp = await client.get(
                    f"{OPENVC_API}/rounds",
                    params={"page": page, "per_page": 100},
                )
                if resp.status_code == 404:
                    break
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (404, 403, 401):
                    logger.warning(f"OpenVC API not available (HTTP {e.response.status_code}), skipping")
                    break
                raise
            except Exception:
                break

            items = data if isinstance(data, list) else data.get("data", data.get("rounds", []))
            if not items:
                break

            for item in items:
                raw_round = self._parse_round(item)
                if raw_round:
                    rounds.append(raw_round)

            page += 1

        return rounds

    def _parse_round(self, item: dict) -> RawRound | None:
        """Parse a single OpenVC round into RawRound."""
        company_name = (
            item.get("company_name")
            or item.get("startup_name")
            or item.get("name", "")
        ).strip()
        if not company_name:
            return None

        # Parse amount
        amount = item.get("amount") or item.get("amount_usd")
        if amount:
            try:
                amount = int(float(amount))
                if amount <= 0:
                    amount = None
            except (ValueError, TypeError):
                amount = None

        # Parse round type
        round_type = item.get("round_type") or item.get("stage")
        if round_type:
            round_type = round_type.lower().replace(" ", "_").replace("-", "_")

        # Parse date
        date_str = item.get("date") or item.get("announced_date")
        round_date = date.today()
        if date_str:
            try:
                from datetime import datetime
                round_date = datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
            except Exception:
                pass

        # Extract investors
        lead_investors = []
        other_investors = []
        investors = item.get("investors", [])
        if isinstance(investors, list):
            for inv in investors:
                if isinstance(inv, dict):
                    name = inv.get("name", "").strip()
                    if name:
                        if inv.get("is_lead") or inv.get("lead"):
                            lead_investors.append(name)
                        else:
                            other_investors.append(name)
                elif isinstance(inv, str) and inv.strip():
                    other_investors.append(inv.strip())

        return RawRound(
            project_name=company_name,
            date=round_date,
            amount_usd=amount,
            round_type=round_type,
            lead_investors=lead_investors,
            other_investors=other_investors,
            project_url=item.get("website") or item.get("url"),
            raw_data={
                "source": "openvc",
                "openvc_id": item.get("id"),
            },
        )
