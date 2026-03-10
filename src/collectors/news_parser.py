"""Shared parsing utilities for news-based funding extraction.

Used by rss_funding.py, google_news.py, and pitchbook_news.py.
"""

import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime


# --- Amount parsing ---

AMOUNT_PATTERN = re.compile(
    r"[\$€£](\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|k|thousand)",
    re.IGNORECASE,
)

# Approximate FX rates for EUR/GBP → USD
CURRENCY_MULTIPLIERS = {"$": 1.0, "€": 1.08, "£": 1.27}

CURRENCY_PATTERN = re.compile(
    r"([\$€£])(\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|k|thousand)?",
    re.IGNORECASE,
)


def parse_amount(amount_str: str, unit: str | None, currency: str = "$") -> int | None:
    """Convert amount string + unit to integer USD."""
    try:
        val = float(amount_str)
    except ValueError:
        return None

    if unit:
        unit_lower = unit.lower()
        if unit_lower in ("million", "mn", "m"):
            val *= 1_000_000
        elif unit_lower in ("billion", "bn", "b"):
            val *= 1_000_000_000
        elif unit_lower in ("thousand", "k"):
            val *= 1_000
    else:
        # If no unit and number is small, assume millions
        if val < 1000:
            val *= 1_000_000

    # Apply currency conversion
    fx = CURRENCY_MULTIPLIERS.get(currency, 1.0)
    val *= fx

    return int(val) if val > 0 else None


# --- Round type extraction ---

ROUND_TYPE_PATTERNS = [
    (re.compile(r"pre-seed", re.IGNORECASE), "pre_seed"),
    (re.compile(r"seed\s+round|seed\s+funding|seed\s+stage", re.IGNORECASE), "seed"),
    (re.compile(r"series\s+a\s+extension", re.IGNORECASE), "series_a_ext"),
    (re.compile(r"series\s+b\s+extension", re.IGNORECASE), "series_b_ext"),
    (re.compile(r"series\s+a", re.IGNORECASE), "series_a"),
    (re.compile(r"series\s+b", re.IGNORECASE), "series_b"),
    (re.compile(r"series\s+c", re.IGNORECASE), "series_c"),
    (re.compile(r"series\s+d", re.IGNORECASE), "series_d"),
    (re.compile(r"series\s+e", re.IGNORECASE), "series_e"),
    (re.compile(r"series\s+f", re.IGNORECASE), "series_f"),
    (re.compile(r"series\s+g", re.IGNORECASE), "series_g"),
    (re.compile(r"series\s+h", re.IGNORECASE), "series_h"),
    (re.compile(r"bridge\s+round|bridge\s+funding", re.IGNORECASE), "bridge"),
    (re.compile(r"growth\s+round|growth\s+equity|growth\s+funding", re.IGNORECASE), "growth"),
    (re.compile(r"venture\s+debt|debt\s+financing|debt\s+round", re.IGNORECASE), "debt"),
    (re.compile(r"extension", re.IGNORECASE), "extension"),
    (re.compile(r"seed", re.IGNORECASE), "seed"),
]


def extract_round_type(text: str) -> str | None:
    """Extract round type from text."""
    for pattern, round_type in ROUND_TYPE_PATTERNS:
        if pattern.search(text):
            return round_type
    return None


# --- Company name patterns ---

# "CompanyName raises/secures/closes/announces/completes $XM"
RAISES_PATTERN = re.compile(
    r"^(.+?)\s+(?:raises?|secures?|closes?|lands?|nabs?|gets?|bags?|picks?\s+up|nets?|snags?"
    r"|announces?|completes?|unveils?)"
    r"\s+[\$€£](\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|k|thousand)?",
    re.IGNORECASE,
)

# Prefixes to strip from extracted company names (applied repeatedly)
TITLE_PREFIXES = re.compile(
    r"^(?:exclusive:\s*|breaking:\s*|report:\s*|updated?:\s*"
    # Region/country descriptors
    r"|(?:south\s+)?korean\s+|chinese\s+|japanese\s+|indian\s+|israeli\s+"
    r"|european\s+|american\s+|british\s+|german\s+|french\s+|brazilian\s+"
    r"|singaporean\s+|hong\s+kong(?:-based)?\s+|uk(?:-based)?\s+"
    r"|us(?:-based)?\s+|london(?:-based)?\s+|sf(?:-based)?\s+"
    r"|new\s+york(?:-based)?\s+|silicon\s+valley(?:-based)?\s+"
    # Industry descriptors before company name
    r"|(?:ai|crypto|blockchain|defi|web3|nft|saas|fintech|healthtech"
    r"|biotech|edtech|proptech|insurtech|regtech|legaltech|agtech|cleantech"
    r"|medtech|deeptech|gamefi|socialfi)\s+"
    # Generic business descriptors
    r"|(?:startup|firm|company|platform|protocol|network|exchange|lender"
    r"|maker|developer|provider|unicorn|giant|player)\s+"
    r"|(?:game|gaming|data|security|payments?|lending|trading|analytics"
    r"|infrastructure|cloud|mobile|digital|virtual|enterprise)\s+)",
    re.IGNORECASE,
)

# Boundary words — text after these is noise, not part of the company name
_BOUNDARY_PATTERN = re.compile(
    r"\s*[,–—]\s+|\s+(?:formerly|aka|previously|now\s+known\s+as|f/k/a)\s+",
    re.IGNORECASE,
)

FUNDING_KEYWORDS = [
    "raises", "secures", "closes", "funding", "raised", "round",
    "announces", "completes", "unveils",
]

# Max reasonable length for a company name
_MAX_NAME_LEN = 60


def clean_company_name(name: str) -> str:
    """Clean up extracted company name.

    Strips news headline prefixes (region, industry, generic descriptors),
    stops at boundary words, and caps length.
    """
    # Strip punctuation wrapping
    name = name.strip("',\":-–—").strip()

    # Cut at boundary words (commas, dashes, "formerly", etc.)
    name = _BOUNDARY_PATTERN.split(name)[0].strip()

    # Repeatedly strip prefix patterns (handles "South Korean AI game firm Verse8")
    prev = None
    while prev != name:
        prev = name
        name = TITLE_PREFIXES.sub("", name).strip()

    # If still too long, take the last capitalized word(s) as the likely company name
    if len(name) > _MAX_NAME_LEN:
        # Try to find the last proper noun chunk
        words = name.split()
        # Walk backwards to find the start of the final proper noun phrase
        start = len(words) - 1
        while start > 0 and words[start - 1][0:1].isupper():
            start -= 1
        candidate = " ".join(words[start:])
        if len(candidate) >= 2:
            name = candidate

    # Final cleanup
    name = name.strip("',\":-–—").strip()
    return name


# --- Investor extraction ---

LED_BY_PATTERN = re.compile(
    r"led\s+by\s+([A-Z][\w\s&.']+?)(?:\s+and\s+([A-Z][\w\s&.']+?))?(?:\s*[,.]|\s+with|\s+in|\s+to|\s+at|\s*$)",
    re.IGNORECASE,
)

PARTICIPATION_PATTERN = re.compile(
    r"(?:with\s+participation\s+from|backed\s+by|investors?\s+include|joined\s+by)\s+(.+?)(?:\.\s|$)",
    re.IGNORECASE,
)


def extract_investors(text: str) -> tuple[list[str], list[str]]:
    """Extract lead and other investors from article text.

    Returns (lead_investors, other_investors).
    """
    leads: list[str] = []
    others: list[str] = []

    led_match = LED_BY_PATTERN.search(text)
    if led_match:
        lead1 = led_match.group(1).strip().rstrip(",.")
        if lead1 and 1 < len(lead1) < 100:
            leads.append(lead1)
        lead2 = led_match.group(2)
        if lead2:
            lead2 = lead2.strip().rstrip(",.")
            if lead2 and 1 < len(lead2) < 100:
                leads.append(lead2)

    part_match = PARTICIPATION_PATTERN.search(text)
    if part_match:
        participants_str = part_match.group(1)
        parts = re.split(r",\s*|\s+and\s+", participants_str)
        for p in parts:
            name = re.sub(r"^and\s+", "", p.strip()).strip().rstrip(",.")
            if name and 1 < len(name) < 100:
                if not any(w in name.lower() for w in ["others", "more", "various", "several"]):
                    others.append(name)

    return leads, others


# --- Valuation extraction ---

VALUATION_PATTERN = re.compile(
    r"(?:at\s+a|valued\s+at|valuation\s+of)\s+[\$€£](\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b)",
    re.IGNORECASE,
)


def extract_valuation(text: str) -> int | None:
    """Extract valuation from text like 'at a $1.5B valuation'."""
    match = VALUATION_PATTERN.search(text)
    if match:
        return parse_amount(match.group(1), match.group(2))
    return None


# --- Date parsing ---

def parse_rss_date(date_str: str) -> date | None:
    """Parse RSS pubDate format."""
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
    except Exception:
        return None
