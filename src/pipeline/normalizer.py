from slugify import slugify

from src.collectors.base import RawRound


def make_slug(name: str) -> str:
    return slugify(name, max_length=200)


def normalize_sector(sector: str | None) -> str | None:
    if not sector:
        return None
    mapping = {
        # General startup sectors
        "saas": "saas",
        "software": "saas",
        "enterprise": "enterprise",
        "enterprise software": "enterprise",
        "b2b": "enterprise",
        "b2b saas": "enterprise",
        "fintech": "fintech",
        "financial services": "fintech",
        "financial technology": "fintech",
        "healthtech": "healthtech",
        "health": "healthtech",
        "healthcare": "healthtech",
        "health care": "healthtech",
        "biotech": "biotech",
        "biotechnology": "biotech",
        "edtech": "edtech",
        "education": "edtech",
        "ecommerce": "ecommerce",
        "e-commerce": "ecommerce",
        "commerce": "ecommerce",
        "marketplace": "marketplace",
        "consumer": "consumer",
        "consumer internet": "consumer",
        "hardware": "hardware",
        "robotics": "hardware",
        "iot": "hardware",
        "ai": "ai",
        "artificial intelligence": "ai",
        "machine learning": "ai",
        "ml": "ai",
        "deep tech": "deep-tech",
        "deeptech": "deep-tech",
        "climate": "climate",
        "cleantech": "climate",
        "clean tech": "climate",
        "energy": "climate",
        "proptech": "proptech",
        "real estate": "proptech",
        "cybersecurity": "security",
        "security": "security",
        "devtools": "devtools",
        "developer tools": "devtools",
        "infrastructure": "infrastructure",
        "infra": "infrastructure",
        "cloud": "infrastructure",
        "data": "data",
        "analytics": "data",
        "social": "social",
        "media": "media",
        "gaming": "gaming",
        "payments": "payments",
        "logistics": "logistics",
        "supply chain": "logistics",
        "foodtech": "foodtech",
        "food": "foodtech",
        "legaltech": "legaltech",
        "legal": "legaltech",
        "insurtech": "insurtech",
        "insurance": "insurtech",
        "hrtech": "hrtech",
        "hr": "hrtech",
        "spacetech": "spacetech",
        "space": "spacetech",
        # Crypto sectors (keep for backward compat)
        "defi": "defi",
        "defi & cefi": "defi",
        "cefi": "cefi",
        "nft": "nft",
        "dao": "dao",
        "chain": "infrastructure",
        "cross-chain": "infrastructure",
        "wallet": "fintech",
        "exchange": "fintech",
        "cex": "fintech",
        "dex": "defi",
        "stablecoin": "fintech",
        "lending": "fintech",
        "privacy": "security",
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
