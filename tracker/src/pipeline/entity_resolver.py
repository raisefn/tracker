"""Entity resolution for investors.

MVP approach: exact match against alias table + curated top investor aliases.
Scale approach (v0.3+): fuzzy matching, co-occurrence patterns.
"""

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


def resolve_investor_name(name: str) -> str:
    """Resolve an investor name to its canonical form."""
    stripped = name.strip()
    return INVESTOR_ALIASES.get(stripped, stripped)
