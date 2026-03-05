from datetime import date, datetime

import httpx

from src.collectors.base import BaseCollector, RawRound

DEFILLAMA_RAISES_URL = "https://api.llama.fi/raises"


def _normalize_round_type(raw: str | None) -> str | None:
    if not raw:
        return None
    mapping = {
        "seed": "seed",
        "pre-seed": "pre_seed",
        "series a": "series_a",
        "series b": "series_b",
        "series c": "series_c",
        "series d": "series_d",
        "strategic": "strategic",
        "private": "private",
        "public": "public",
        "undisclosed": "undisclosed",
    }
    return mapping.get(raw.lower().strip(), raw.lower().strip())


def _parse_amount(amount: float | int | None) -> int | None:
    if amount is None or amount <= 0:
        return None
    # DefiLlama reports in millions
    return int(amount * 1_000_000)


class DefiLlamaCollector(BaseCollector):
    def source_type(self) -> str:
        return "defillama"

    async def collect(self) -> list[RawRound]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.get(DEFILLAMA_RAISES_URL)
            resp.raise_for_status()
            data = resp.json()

        raises = data.get("raises", data) if isinstance(data, dict) else data
        rounds: list[RawRound] = []

        for r in raises:
            try:
                # Parse date (unix timestamp)
                ts = r.get("date")
                if ts is None:
                    continue
                round_date = date.fromtimestamp(ts) if isinstance(ts, (int, float)) else date.fromisoformat(str(ts))

                lead = r.get("leadInvestors", []) or []
                other = r.get("otherInvestors", []) or []

                rounds.append(
                    RawRound(
                        project_name=r.get("name", "Unknown"),
                        date=round_date,
                        round_type=_normalize_round_type(r.get("round")),
                        amount_usd=_parse_amount(r.get("amount")),
                        valuation_usd=_parse_amount(r.get("valuation")),
                        lead_investors=[i for i in lead if i],
                        other_investors=[i for i in other if i],
                        sector=r.get("category"),
                        category=r.get("categoryGroup"),
                        chains=r.get("chains", []) or [],
                        source_url=r.get("source"),
                        project_url=None,
                        raw_data=r,
                    )
                )
            except Exception:
                continue  # skip malformed entries, log in production

        return rounds
