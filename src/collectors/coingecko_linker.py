"""Link projects to CoinGecko IDs by matching names and symbols.

Uses the free /coins/list endpoint (single call, no rate limit concerns)
to build a lookup table, then fuzzy-matches against project names.
This unlocks CoinGecko market data, community data, and Etherscan enrichment.
"""

import logging

import httpx
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

COINGECKO_LIST_URL = "https://api.coingecko.com/api/v3/coins/list"

# Common words that produce false-positive matches
BLOCKLIST = frozenset(
    {
        "the",
        "app",
        "pay",
        "one",
        "go",
        "now",
        "home",
        "hub",
        "cash",
        "safe",
        "link",
        "swap",
        "core",
        "open",
        "play",
        "mint",
        "fund",
        "gold",
        "real",
        "edge",
        "key",
        "arc",
        "rise",
        "nest",
        "dash",
        "ion",
        "via",
        "atlas",
        "pulse",
        "wave",
        "shift",
        "bridge",
        "shield",
        "spark",
        "flash",
        "storm",
        "global",
        "prime",
        "alpha",
        "beta",
        "delta",
        "sigma",
        "omega",
        "capital",
        "ventures",
        "labs",
        "protocol",
        "finance",
        "network",
        "digital",
        "technologies",
        "solutions",
        "systems",
        "group",
        "inc",
        "token",
        "coin",
        "chain",
        "block",
        "crypto",
        "defi",
        "dao",
        "nft",
    }
)


class CoinGeckoLinker(BaseEnricher):
    def source_name(self) -> str:
        return "coingecko_linker"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Fetch full coin list from CoinGecko (single request)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(COINGECKO_LIST_URL)
            resp.raise_for_status()
            coins = resp.json()

        logger.info(f"CoinGecko coins list: {len(coins)} coins")

        # Build lookup maps
        # id is unique, name and symbol can collide
        name_map: dict[str, dict] = {}  # lowercase name → coin
        symbol_map: dict[str, list[dict]] = {}  # lowercase symbol → [coins]

        for coin in coins:
            name_lower = coin.get("name", "").lower().strip()
            symbol_lower = coin.get("symbol", "").lower().strip()

            # Only keep first match per name (CoinGecko list is ordered by relevance)
            if name_lower and name_lower not in name_map:
                name_map[name_lower] = coin

            if symbol_lower:
                symbol_map.setdefault(symbol_lower, []).append(coin)

        # Get projects that don't already have a coingecko_id
        projects = (
            (await session.execute(select(Project).where(Project.coingecko_id.is_(None))))
            .scalars()
            .all()
        )

        logger.info(f"Projects without coingecko_id: {len(projects)}")

        linked = 0
        for project in projects:
            coin = self._match_project(project, name_map, symbol_map)
            if coin:
                project.coingecko_id = coin["id"]
                if not project.token_symbol:
                    project.token_symbol = coin.get("symbol", "").upper()
                stamp_freshness(project, self.source_name())
                linked += 1
                result.records_updated += 1
            else:
                result.records_skipped += 1

        await session.flush()
        logger.info(f"CoinGecko linker: {linked} projects linked to coin IDs")
        return result

    def _match_project(
        self,
        project: Project,
        name_map: dict[str, dict],
        symbol_map: dict[str, list[dict]],
    ) -> dict | None:
        """Try to match a project to a CoinGecko coin."""
        name_lower = project.name.lower().strip()

        # Skip very short names (high false positive risk)
        if len(name_lower) < 3:
            return None

        # Skip if name is a common word
        if name_lower in BLOCKLIST:
            return None

        # 1. Exact name match (highest confidence)
        coin = name_map.get(name_lower)
        if coin:
            return coin

        # 2. Try with common suffixes stripped
        for suffix in [
            " protocol",
            " finance",
            " network",
            " token",
            " dao",
            " labs",
            " ai",
            " io",
            " app",
            " chain",
            " exchange",
        ]:
            if name_lower.endswith(suffix):
                base = name_lower[: -len(suffix)].strip()
                if base and base not in BLOCKLIST and len(base) >= 3:
                    coin = name_map.get(base)
                    if coin:
                        return coin

        # 3. Try adding common suffixes (project "Uniswap" → coin "Uniswap Protocol Token")
        # Skip this — too many false positives

        # 4. Token symbol match (only if symbol is unique and project has one)
        if project.token_symbol:
            sym = project.token_symbol.lower().strip()
            matches = symbol_map.get(sym, [])
            if len(matches) == 1:
                # Unique symbol — verify name similarity
                coin_name = matches[0].get("name", "").lower()
                slug = slugify(project.name, max_length=200)
                coin_slug = slugify(coin_name, max_length=200)
                if slug == coin_slug or name_lower in coin_name or coin_name in name_lower:
                    return matches[0]

        return None
