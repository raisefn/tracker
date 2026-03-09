"""RSS feed collector for startup funding news.

Polls TechCrunch, Crunchbase News, and Google News RSS feeds
for funding announcements, then extracts structured data using
regex patterns (with optional LLM enhancement).

Designed to run every 15 minutes for near-real-time coverage.
"""

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime

import httpx

from src.collectors.base import BaseCollector, RawRound

logger = logging.getLogger(__name__)

RSS_FEEDS = [
    {
        "name": "TechCrunch Funding",
        "url": "https://techcrunch.com/category/fundings-exits/feed/",
    },
    {
        "name": "Crunchbase News Venture",
        "url": "https://news.crunchbase.com/sections/venture/feed/",
    },
]

# Regex patterns for extracting funding data from article titles/descriptions
AMOUNT_PATTERN = re.compile(
    r"\$(\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|k|thousand)",
    re.IGNORECASE,
)

ROUND_TYPE_PATTERNS = [
    (re.compile(r"pre-seed", re.IGNORECASE), "pre_seed"),
    (re.compile(r"seed\s+round|seed\s+funding|seed\s+stage", re.IGNORECASE), "seed"),
    (re.compile(r"series\s+a", re.IGNORECASE), "series_a"),
    (re.compile(r"series\s+b", re.IGNORECASE), "series_b"),
    (re.compile(r"series\s+c", re.IGNORECASE), "series_c"),
    (re.compile(r"series\s+d", re.IGNORECASE), "series_d"),
    (re.compile(r"series\s+e", re.IGNORECASE), "series_e"),
    (re.compile(r"seed", re.IGNORECASE), "seed"),
]

# Investor extraction patterns from article text
# "led by InvestorName" or "led by InvestorName and InvestorName"
LED_BY_PATTERN = re.compile(
    r"led\s+by\s+([A-Z][\w\s&.']+?)(?:\s+and\s+([A-Z][\w\s&.']+?))?(?:\s*[,.]|\s+with|\s+in|\s+to|\s+at|\s*$)",
    re.IGNORECASE,
)

# "with participation from X, Y, and Z" or "backed by X"
PARTICIPATION_PATTERN = re.compile(
    r"(?:with\s+participation\s+from|backed\s+by|investors?\s+include|joined\s+by)\s+(.+?)(?:\.\s|$)",
    re.IGNORECASE,
)


def _extract_investors(text: str) -> tuple[list[str], list[str]]:
    """Extract lead and other investors from article text.

    Returns (lead_investors, other_investors).
    """
    leads: list[str] = []
    others: list[str] = []

    # Extract lead investor(s)
    led_match = LED_BY_PATTERN.search(text)
    if led_match:
        lead1 = led_match.group(1).strip().rstrip(",.")
        if lead1 and len(lead1) > 1 and len(lead1) < 100:
            leads.append(lead1)
        lead2 = led_match.group(2)
        if lead2:
            lead2 = lead2.strip().rstrip(",.")
            if lead2 and len(lead2) > 1 and len(lead2) < 100:
                leads.append(lead2)

    # Extract other participants
    part_match = PARTICIPATION_PATTERN.search(text)
    if part_match:
        participants_str = part_match.group(1)
        # Split on commas and "and", then clean up leading "and "
        parts = re.split(r",\s*|\s+and\s+", participants_str)
        for p in parts:
            name = re.sub(r"^and\s+", "", p.strip()).strip().rstrip(",.")
            if name and len(name) > 1 and len(name) < 100:
                # Skip if it's actually a description, not a name
                if not any(w in name.lower() for w in ["others", "more", "various", "several"]):
                    others.append(name)

    return leads, others


# Common patterns: "CompanyName raises $XM in Series A"
RAISES_PATTERN = re.compile(
    r"^(.+?)\s+(?:raises?|secures?|closes?|lands?|nabs?|gets?|bags?|picks?\s+up|nets?|snags?)"
    r"\s+\$(\d+(?:\.\d+)?)\s*(million|mn|m|billion|bn|b|k|thousand)?",
    re.IGNORECASE,
)


def _parse_amount(amount_str: str, unit: str | None) -> int | None:
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

    return int(val) if val > 0 else None


def _extract_round_type(text: str) -> str | None:
    """Extract round type from text."""
    for pattern, round_type in ROUND_TYPE_PATTERNS:
        if pattern.search(text):
            return round_type
    return None


def _parse_rss_date(date_str: str) -> date | None:
    """Parse RSS pubDate format."""
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        pass
    # Try ISO format
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00")).date()
    except Exception:
        return None


class RSSFundingCollector(BaseCollector):
    """Collect funding rounds from RSS feeds."""

    def source_type(self) -> str:
        return "news"

    async def collect(self) -> list[RawRound]:
        """Fetch and parse all RSS feeds."""
        rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            for feed in RSS_FEEDS:
                try:
                    feed_rounds = await self._fetch_feed(client, feed)
                    rounds.extend(feed_rounds)
                    logger.info(f"{feed['name']}: {len(feed_rounds)} rounds extracted")
                except Exception as e:
                    logger.error(f"Failed to fetch {feed['name']}: {e}")

        logger.info(f"Total: {len(rounds)} rounds from RSS feeds")
        return rounds

    async def _fetch_feed(self, client: httpx.AsyncClient, feed: dict) -> list[RawRound]:
        """Fetch and parse a single RSS feed."""
        resp = await client.get(feed["url"])
        resp.raise_for_status()

        rounds: list[RawRound] = []
        root = ET.fromstring(resp.text)

        # Handle both RSS 2.0 and Atom feeds
        items = root.findall(".//item") or root.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

        for item in items:
            title = (
                item.findtext("title")
                or item.findtext("{http://www.w3.org/2005/Atom}title")
                or ""
            )
            link = (
                item.findtext("link")
                or (item.find("{http://www.w3.org/2005/Atom}link") or {}).get("href", "")
                or ""
            )
            pub_date = (
                item.findtext("pubDate")
                or item.findtext("{http://www.w3.org/2005/Atom}published")
                or ""
            )
            description = (
                item.findtext("description")
                or item.findtext("{http://www.w3.org/2005/Atom}summary")
                or ""
            )

            # Try to extract funding data from title
            raw_round = self._parse_funding_article(
                title, description, link, pub_date, feed["name"]
            )
            if raw_round:
                rounds.append(raw_round)

        return rounds

    def _parse_funding_article(
        self,
        title: str,
        description: str,
        link: str,
        pub_date: str,
        feed_name: str,
    ) -> RawRound | None:
        """Extract structured funding data from an article title/description."""
        combined = f"{title} {description}"

        # Extract investors from combined title + description
        lead_investors, other_investors = _extract_investors(combined)

        # Try the "X raises $Y" pattern first
        match = RAISES_PATTERN.match(title)
        if match:
            company_name = match.group(1).strip()
            amount = _parse_amount(match.group(2), match.group(3))
            round_type = _extract_round_type(combined)
            article_date = _parse_rss_date(pub_date) or date.today()

            # Clean up company name (remove trailing commas, quotes, etc.)
            company_name = company_name.strip("',\"").strip()

            if company_name and len(company_name) > 1:
                return RawRound(
                    project_name=company_name,
                    date=article_date,
                    amount_usd=amount,
                    round_type=round_type,
                    lead_investors=lead_investors,
                    other_investors=other_investors,
                    source_url=link,
                    raw_data={
                        "title": title,
                        "feed": feed_name,
                        "source": "rss",
                    },
                )

        # Fallback: look for amount pattern anywhere in title with funding keywords
        funding_keywords = ["raises", "secures", "closes", "funding", "raised", "round"]
        has_funding_keyword = any(kw in title.lower() for kw in funding_keywords)

        if has_funding_keyword:
            amount_match = AMOUNT_PATTERN.search(title)
            if amount_match:
                amount = _parse_amount(amount_match.group(1), amount_match.group(2))
                round_type = _extract_round_type(combined)
                article_date = _parse_rss_date(pub_date) or date.today()

                # Try to get company name (text before the funding verb)
                for kw in funding_keywords:
                    idx = title.lower().find(kw)
                    if idx > 0:
                        company_name = title[:idx].strip().strip("',\":-").strip()
                        if company_name and len(company_name) > 1:
                            return RawRound(
                                project_name=company_name,
                                date=article_date,
                                amount_usd=amount,
                                round_type=round_type,
                                lead_investors=lead_investors,
                                other_investors=other_investors,
                                source_url=link,
                                raw_data={
                                    "title": title,
                                    "feed": feed_name,
                                    "source": "rss",
                                },
                            )

        return None
