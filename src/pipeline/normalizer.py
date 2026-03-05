from slugify import slugify

from src.collectors.base import RawRound


def make_slug(name: str) -> str:
    return slugify(name, max_length=200)


def normalize_sector(sector: str | None) -> str | None:
    if not sector:
        return None
    mapping = {
        "defi": "defi",
        "defi & cefi": "defi",
        "cefi": "cefi",
        "infrastructure": "infrastructure",
        "infra": "infrastructure",
        "nft": "nft",
        "gaming": "gaming",
        "dao": "dao",
        "chain": "infrastructure",
        "cross-chain": "infrastructure",
        "wallet": "wallet",
        "exchange": "exchange",
        "cex": "exchange",
        "dex": "defi",
        "stablecoin": "stablecoin",
        "lending": "defi",
        "payments": "payments",
        "privacy": "privacy",
        "data": "data",
        "analytics": "data",
        "social": "social",
        "identity": "identity",
    }
    lower = sector.lower().strip()
    return mapping.get(lower, lower)


def normalize_chains(chains: list[str]) -> list[str]:
    mapping = {
        "ethereum": "ethereum",
        "eth": "ethereum",
        "bitcoin": "bitcoin",
        "btc": "bitcoin",
        "solana": "solana",
        "sol": "solana",
        "polygon": "polygon",
        "matic": "polygon",
        "arbitrum": "arbitrum",
        "optimism": "optimism",
        "avalanche": "avalanche",
        "avax": "avalanche",
        "base": "base",
        "bnb": "bnb",
        "bsc": "bnb",
        "cosmos": "cosmos",
        "near": "near",
        "sui": "sui",
        "aptos": "aptos",
    }
    normalized = []
    for c in chains:
        lower = c.lower().strip()
        mapped = mapping.get(lower, lower)
        if mapped not in normalized:
            normalized.append(mapped)
    return normalized


def normalize_round(raw: RawRound) -> RawRound:
    """Apply normalization to a raw round."""
    raw.sector = normalize_sector(raw.sector)
    raw.chains = normalize_chains(raw.chains)
    return raw
