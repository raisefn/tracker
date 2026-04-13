"""Hacker News collector for funding announcements via Algolia API.

Searches HN stories and comments for startup funding announcements.
The Algolia API is free, public, and has excellent coverage of tech funding.

API docs: https://hn.algolia.com/api
"""

import logging
import re
from datetime import date, datetime, timedelta

import httpx

from src.collectors.base import BaseCollector, RawRound
from src.collectors.news_parser import (
    AMOUNT_PATTERN,
    FUNDING_KEYWORDS,
    RAISES_PATTERN,
    clean_company_name,
    extract_investors,
    extract_round_type,
    extract_valuation,
    parse_amount,
)

logger = logging.getLogger(__name__)

HN_ALGOLIA_API = "https://hn.algolia.com/api/v1"

# Search queries to find funding stories
SEARCH_QUERIES = [
    "raises million funding",
    "Series A",
    "Series B",
    "Series C",
    "seed round million",
    "pre-seed funding",
    "raises $",
    "funding round million",
    "secures million funding",
    "closes million round",
    "venture funding",
    "YC backed raises",
]

# Additional title-only patterns for HN posts
HN_FUNDING_PATTERN = re.compile(
    r"(.+?)\s+(?:raises?|secures?|closes?|gets?|lands?|nabs?|bags?)\s+\$?([\d,.]+)\s*(million|billion|m|b|mn|MM|k)\b",
    re.IGNORECASE,
)


class HackerNewsFundingCollector(BaseCollector):
    """Collect funding rounds from Hacker News via Algolia search API."""

    def source_type(self) -> str:
        return "news"

    def __init__(self, days: int = 365 * 5):
        """Initialize with lookback window. Default 5 years for historical backfill."""
        self.days = days

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []
        seen_ids: set[str] = set()

        since = int((datetime.now() - timedelta(days=self.days)).timestamp())

        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
        ) as client:
            for query in SEARCH_QUERIES:
                try:
                    # Paginate through results (Algolia allows up to 1000 per query)
                    page = 0
                    max_pages = 20  # 20 pages * 50 = 1000 results max
                    query_count = 0

                    while page < max_pages:
                        resp = await client.get(
                            f"{HN_ALGOLIA_API}/search",
                            params={
                                "query": query,
                                "tags": "story",
                                "numericFilters": f"created_at_i>{since}",
                                "hitsPerPage": 50,
                                "page": page,
                            },
                        )
                        resp.raise_for_status()
                        data = resp.json()

                        hits = data.get("hits", [])
                        if not hits:
                            break

                        for hit in hits:
                            object_id = hit.get("objectID", "")
                            if object_id in seen_ids:
                                continue
                            seen_ids.add(object_id)

                            raw_round = self._parse_hit(hit)
                            if raw_round:
                                rounds.append(raw_round)
                                query_count += 1

                        page += 1

                    logger.info(f"HN query '{query[:30]}': {query_count} rounds ({page} pages)")
                except Exception as e:
                    logger.warning(f"HN search failed for '{query[:30]}': {e}")

        logger.info(f"Total: {len(rounds)} rounds from Hacker News")
        return rounds

    def _parse_hit(self, hit: dict) -> RawRound | None:
        """Parse a single HN hit into a RawRound."""
        title = hit.get("title", "") or ""
        url = hit.get("url", "") or ""
        story_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"

        # Parse date
        created_at = hit.get("created_at", "")
        try:
            article_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
        except Exception:
            article_date = date.today()

        combined = title
        lead_investors, other_investors = extract_investors(combined)
        valuation = extract_valuation(combined)

        # Try standard raises pattern
        match = RAISES_PATTERN.match(title)
        if match:
            company_name = clean_company_name(match.group(1))
            amount = parse_amount(match.group(2), match.group(3))
            round_type = extract_round_type(combined)

            if company_name and len(company_name) > 1:
                return RawRound(
                    project_name=company_name,
                    date=article_date,
                    amount_usd=amount,
                    valuation_usd=valuation,
                    round_type=round_type,
                    lead_investors=lead_investors,
                    other_investors=other_investors,
                    source_url=url or story_url,
                    raw_data={
                        "title": title,
                        "hn_id": hit.get("objectID"),
                        "points": hit.get("points"),
                        "num_comments": hit.get("num_comments"),
                        "feed": "Hacker News",
                        "source": "hackernews",
                    },
                )

        # Try HN-specific pattern
        hn_match = HN_FUNDING_PATTERN.match(title)
        if hn_match:
            company_name = clean_company_name(hn_match.group(1))
            amount = parse_amount(hn_match.group(2), hn_match.group(3))
            round_type = extract_round_type(combined)

            if company_name and len(company_name) > 1:
                return RawRound(
                    project_name=company_name,
                    date=article_date,
                    amount_usd=amount,
                    valuation_usd=valuation,
                    round_type=round_type,
                    lead_investors=lead_investors,
                    other_investors=other_investors,
                    source_url=url or story_url,
                    raw_data={
                        "title": title,
                        "hn_id": hit.get("objectID"),
                        "points": hit.get("points"),
                        "num_comments": hit.get("num_comments"),
                        "feed": "Hacker News",
                        "source": "hackernews",
                    },
                )

        # Fallback: keyword match
        has_funding_keyword = any(kw in title.lower() for kw in FUNDING_KEYWORDS)
        if has_funding_keyword:
            amount_match = AMOUNT_PATTERN.search(title)
            if amount_match:
                amount = parse_amount(amount_match.group(1), amount_match.group(2))
                round_type = extract_round_type(combined)

                for kw in FUNDING_KEYWORDS:
                    idx = title.lower().find(kw)
                    if idx > 0:
                        company_name = clean_company_name(title[:idx])
                        if company_name and len(company_name) > 1:
                            return RawRound(
                                project_name=company_name,
                                date=article_date,
                                amount_usd=amount,
                                valuation_usd=valuation,
                                round_type=round_type,
                                lead_investors=lead_investors,
                                other_investors=other_investors,
                                source_url=url or story_url,
                                raw_data={
                                    "title": title,
                                    "hn_id": hit.get("objectID"),
                                    "points": hit.get("points"),
                                    "num_comments": hit.get("num_comments"),
                                    "feed": "Hacker News",
                                    "source": "hackernews",
                                },
                            )

        return None
