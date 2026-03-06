"""Entity resolution for investors.

v0.3: case-insensitive matching, suffix stripping, fuzzy fallback.
"""

from difflib import get_close_matches

# Curated aliases for top crypto investors.
# Maps variant names to canonical names.
INVESTOR_ALIASES: dict[str, str] = {
    # a16z variants
    "a16z": "Andreessen Horowitz",
    "a16z crypto": "Andreessen Horowitz",
    "a16z Crypto": "Andreessen Horowitz",
    "Andreessen Horowitz Crypto": "Andreessen Horowitz",
    # Paradigm
    "Paradigm Fund": "Paradigm",
    "Paradigm Operations": "Paradigm",
    # Sequoia
    "Sequoia Capital": "Sequoia",
    "Sequoia China": "Sequoia",
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
    # Electric Capital
    "Electric Capital": "Electric",
    # Jump
    "Jump Crypto": "Jump",
    "Jump Trading": "Jump",
    # Galaxy
    "Galaxy Digital": "Galaxy",
    # HashKey
    "HashKey Capital": "HashKey",
    # Animoca
    "Animoca Brands": "Animoca",
    # Placeholder
    "Placeholder VC": "Placeholder",
}

# Build case-insensitive lookup
_ALIASES_LOWER: dict[str, str] = {k.lower(): v for k, v in INVESTOR_ALIASES.items()}

# Common suffixes that don't carry identity signal
_STRIP_SUFFIXES = [
    " Capital Management", " Capital Partners", " Capital",
    " Ventures", " Labs", " Fund", " Management",
    " Crypto", " Digital", " Group", " Holdings", " Partners",
    " Inc.", " Inc", " LLC", " Ltd.", " Ltd", " GmbH", " Co.",
    " VC", " LP",
]


def _normalize(name: str) -> str:
    """Strip common suffixes to get a base name for matching."""
    n = name.strip()
    for suffix in _STRIP_SUFFIXES:
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
            break  # only strip one suffix
    return n


def resolve_investor_name(name: str, known_canonicals: list[str] | None = None) -> str:
    """Resolve an investor name to its canonical form.

    Resolution order:
    1. Exact match in alias dict
    2. Case-insensitive match in alias dict
    3. Suffix-stripped + case-insensitive match
    4. Fuzzy match against known canonical names (0.85 cutoff)
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

    # 3. Normalize (strip suffix) then try again
    normalized = _normalize(stripped)
    norm_lower = normalized.lower()
    if norm_lower != lower and norm_lower in _ALIASES_LOWER:
        return _ALIASES_LOWER[norm_lower]

    # 4. Fuzzy match against known canonical investor names
    if known_canonicals:
        # Try normalized name first, then original
        for candidate in [normalized, stripped]:
            matches = get_close_matches(candidate, known_canonicals, n=1, cutoff=0.85)
            if matches:
                return matches[0]

    return stripped
