"""Entity resolution for investors.

v0.4: multi-suffix stripping, token-based matching, expanded aliases.
"""

from difflib import get_close_matches

# Curated aliases for top investors (general startup + crypto).
# Maps variant names to canonical names.
INVESTOR_ALIASES: dict[str, str] = {
    # a16z variants
    "a16z": "Andreessen Horowitz",
    "a16z crypto": "Andreessen Horowitz",
    "a16z Crypto": "Andreessen Horowitz",
    "Andreessen Horowitz Crypto": "Andreessen Horowitz",
    "AH Capital Management": "Andreessen Horowitz",
    # Sequoia
    "Sequoia Capital": "Sequoia",
    "Sequoia China": "Sequoia",
    "Sequoia Capital India": "Sequoia",
    "Sequoia Heritage": "Sequoia",
    "Sequoia Capital Global Equities": "Sequoia",
    # Accel
    "Accel Partners": "Accel",
    "Accel India": "Accel",
    # Benchmark
    "Benchmark Capital": "Benchmark",
    # Bessemer
    "Bessemer Venture Partners": "Bessemer",
    "BVP": "Bessemer",
    # GV (Google Ventures)
    "Google Ventures": "GV",
    "GV (Google Ventures)": "GV",
    # Lightspeed
    "Lightspeed Venture Partners": "Lightspeed",
    "Lightspeed India Partners": "Lightspeed",
    "LSVP": "Lightspeed",
    # Greylock
    "Greylock Partners": "Greylock",
    # Index Ventures
    "Index Ventures": "Index",
    # Insight Partners
    "Insight Venture Partners": "Insight Partners",
    "Insight Partners": "Insight Partners",
    # Tiger Global
    "Tiger Global Management": "Tiger Global",
    # Coatue
    "Coatue Management": "Coatue",
    # General Catalyst
    "General Catalyst Partners": "General Catalyst",
    "GeneralCatalyst": "General Catalyst",
    "GC": "General Catalyst",
    # NEA
    "New Enterprise Associates": "NEA",
    # Founders Fund
    "Founders Fund": "Founders Fund",
    "FF": "Founders Fund",
    "Founders Fund LP": "Founders Fund",
    # Khosla
    "Khosla Ventures": "Khosla",
    # IVP
    "Institutional Venture Partners": "IVP",
    # Battery
    "Battery Ventures": "Battery",
    # Ribbit
    "Ribbit Capital": "Ribbit",
    # YC
    "Y Combinator": "Y Combinator",
    "YC": "Y Combinator",
    "Y Combinator Continuity": "Y Combinator",
    "YC Continuity": "Y Combinator",
    # SV Angel
    "SV Angel": "SV Angel",
    # First Round
    "First Round Capital": "First Round",
    # Union Square
    "Union Square Ventures": "USV",
    "USV": "USV",
    # Kleiner Perkins
    "Kleiner Perkins Caufield & Byers": "Kleiner Perkins",
    "KPCB": "Kleiner Perkins",
    # SoftBank
    "SoftBank Vision Fund": "SoftBank",
    "SoftBank Group": "SoftBank",
    "Softbank": "SoftBank",
    "SoftBank Investment Advisers": "SoftBank",
    # DST Global
    "DST Global": "DST",
    # Felicis
    "Felicis Ventures": "Felicis",
    # Spark Capital
    "Spark Capital": "Spark",
    # Craft
    "Craft Ventures": "Craft",
    # 8VC
    "8VC": "8VC",
    # Abstract
    "Abstract Ventures": "Abstract",
    # Lux
    "Lux Capital": "Lux",
    # Paradigm (crypto)
    "Paradigm Fund": "Paradigm",
    "Paradigm Operations": "Paradigm",
    # Pantera
    "Pantera Capital": "Pantera",
    "Pantera Capital Management": "Pantera",
    # Polychain
    "Polychain Capital": "Polychain",
    # DCG
    "Digital Currency Group": "DCG",
    # Coinbase
    "Coinbase Ventures": "Coinbase Ventures",
    # Binance
    "Binance Labs": "Binance Labs",
    "BinanceLabs": "Binance Labs",
    # Multicoin
    "Multicoin Capital": "Multicoin",
    # Framework
    "Framework Ventures": "Framework",
    # Dragonfly
    "Dragonfly Capital": "Dragonfly",
    "DragonflyCapital": "Dragonfly",
    "Dragonfly Capital Partners": "Dragonfly",
    # Electric Capital
    "Electric Capital": "Electric",
    # Jump
    "Jump Crypto": "Jump",
    "Jump Trading": "Jump",
    # Galaxy
    "Galaxy Digital": "Galaxy",
    "Galaxy Interactive": "Galaxy",
    # HashKey
    "HashKey Capital": "HashKey",
    # Animoca
    "Animoca Brands": "Animoca",
    # Placeholder
    "Placeholder VC": "Placeholder",
    # Additional general VCs
    "Andreessen Horowitz Growth": "Andreessen Horowitz",
    "a16z bio": "Andreessen Horowitz",
    "a16z games": "Andreessen Horowitz",
    "Thrive Capital": "Thrive",
    "Addition": "Addition",
    "Greenoaks": "Greenoaks",
    "Greenoaks Capital": "Greenoaks",
    "D1 Capital Partners": "D1 Capital",
    "D1 Capital": "D1 Capital",
    "Altimeter Capital Management": "Altimeter",
    "Altimeter Capital": "Altimeter",
    "Stripe": "Stripe",
    "Elad Gil": "Elad Gil",
}

# Build case-insensitive lookup
_ALIASES_LOWER: dict[str, str] = {k.lower(): v for k, v in INVESTOR_ALIASES.items()}

# All canonical names (for fuzzy matching)
_CANONICAL_NAMES: list[str] = sorted(set(INVESTOR_ALIASES.values()))

# Common suffixes that don't carry identity signal, ordered longest first
_STRIP_SUFFIXES = [
    " Capital Management",
    " Capital Partners",
    " Venture Partners",
    " Investment Advisers",
    " Investment Management",
    " Capital",
    " Ventures",
    " Labs",
    " Fund",
    " Management",
    " Crypto",
    " Digital",
    " Group",
    " Holdings",
    " Partners",
    " Inc.",
    " Inc",
    " LLC",
    " Ltd.",
    " Ltd",
    " GmbH",
    " Co.",
    " VC",
    " LP",
]


def _normalize(name: str) -> str:
    """Strip common suffixes to get a base name for matching.

    Strips multiple suffixes (e.g., "Foo Capital Management LLC" → "Foo").
    """
    n = name.strip()
    changed = True
    while changed:
        changed = False
        for suffix in _STRIP_SUFFIXES:
            if n.lower().endswith(suffix.lower()):
                n = n[: -len(suffix)].strip()
                changed = True
                break
    return n


def resolve_investor_name(name: str, known_canonicals: list[str] | None = None) -> str:
    """Resolve an investor name to its canonical form.

    Resolution order:
    1. Exact match in alias dict
    2. Case-insensitive match in alias dict
    3. Suffix-stripped + case-insensitive match
    4. Suffix-stripped + fuzzy match against canonical names (0.85 cutoff)
    5. Return original name
    """
    stripped = name.strip()
    if not stripped:
        return stripped

    # 1. Exact match
    if stripped in INVESTOR_ALIASES:
        return INVESTOR_ALIASES[stripped]

    # 2. Case-insensitive match
    lower = stripped.lower()
    if lower in _ALIASES_LOWER:
        return _ALIASES_LOWER[lower]

    # 3. Normalize (strip suffixes) then try again
    normalized = _normalize(stripped)
    norm_lower = normalized.lower()
    if norm_lower != lower and norm_lower in _ALIASES_LOWER:
        return _ALIASES_LOWER[norm_lower]

    # 4. Fuzzy match against canonical investor names + any provided list
    candidates = _CANONICAL_NAMES + (known_canonicals or [])
    if candidates:
        for candidate in [normalized, stripped]:
            matches = get_close_matches(candidate, candidates, n=1, cutoff=0.85)
            if matches:
                return matches[0]

    return stripped
