"""Google News RSS search collector for funding announcements.

Searches Google News for startup funding articles across all sources,
catching announcements that don't appear in dedicated RSS feeds.

Designed to run every 15 minutes alongside RSS feeds.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import quote_plus

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
    parse_rss_date,
)

logger = logging.getLogger(__name__)

# Google News RSS search queries designed to catch funding announcements
SEARCH_QUERIES = [
    '"raises" "$" "million" startup funding',
    '"Series A" OR "Series B" OR "seed round" "raises" "$"',
    '"led by" "$" "million" funding round',
    '"secures" "$" "million" funding',
    '"pre-seed" OR "pre seed" "raises" "$" funding',
    '"seed funding" "$" "million" startup',
    '"closes" "$" "million" "round"',
    '"announces" "$" "million" "funding"',
]

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class GoogleNewsFundingCollector(BaseCollector):
    """Collect funding rounds from Google News search results."""

    def source_type(self) -> str:
        return "news"

    async def collect(self) -> list[RawRound]:
        """Search Google News for funding articles."""
        rounds: list[RawRound] = []
        seen_urls: set[str] = set()

        async with httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
            follow_redirects=True,
        ) as client:
            for query in SEARCH_QUERIES:
                try:
                    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
                    resp = await client.get(url)
                    resp.raise_for_status()

                    root = ET.fromstring(resp.text)
                    items = root.findall(".//item")

                    query_count = 0
                    for item in items:
                        title = item.findtext("title") or ""
                        link = item.findtext("link") or ""
                        pub_date = item.findtext("pubDate") or ""
                        description = item.findtext("description") or ""

                        # Deduplicate by URL across queries
                        if link in seen_urls:
                            continue
                        seen_urls.add(link)

                        raw_round = self._parse_article(
                            title, description, link, pub_date
                        )
                        if raw_round:
                            rounds.append(raw_round)
                            query_count += 1

                    logger.info(f"Google News query '{query[:40]}...': {query_count} rounds")
                except Exception as e:
                    logger.warning(f"Google News search failed for '{query[:30]}...': {e}")

        logger.info(f"Total: {len(rounds)} rounds from Google News")
        return rounds

    def _parse_article(
        self, title: str, description: str, link: str, pub_date: str
    ) -> RawRound | None:
        """Extract funding data from a Google News article."""
        # Google News titles often have " - SourceName" suffix
        clean_title = title.rsplit(" - ", 1)[0].strip() if " - " in title else title
        combined = f"{clean_title} {description}"

        lead_investors, other_investors = extract_investors(combined)
        valuation = extract_valuation(combined)

        match = RAISES_PATTERN.match(clean_title)
        if match:
            company_name = clean_company_name(match.group(1))
            amount = parse_amount(match.group(2), match.group(3))
            round_type = extract_round_type(combined)
            article_date = parse_rss_date(pub_date) or date.today()

            if company_name and len(company_name) > 1:
                return RawRound(
                    project_name=company_name,
                    date=article_date,
                    amount_usd=amount,
                    valuation_usd=valuation,
                    round_type=round_type,
                    lead_investors=lead_investors,
                    other_investors=other_investors,
                    source_url=link,
                    raw_data={
                        "title": title,
                        "feed": "Google News",
                        "source": "google_news",
                    },
                )

        # Fallback
        has_funding_keyword = any(kw in clean_title.lower() for kw in FUNDING_KEYWORDS)
        if has_funding_keyword:
            amount_match = AMOUNT_PATTERN.search(clean_title)
            if amount_match:
                amount = parse_amount(amount_match.group(1), amount_match.group(2))
                round_type = extract_round_type(combined)
                article_date = parse_rss_date(pub_date) or date.today()

                for kw in FUNDING_KEYWORDS:
                    idx = clean_title.lower().find(kw)
                    if idx > 0:
                        company_name = clean_company_name(clean_title[:idx])
                        if company_name and len(company_name) > 1:
                            return RawRound(
                                project_name=company_name,
                                date=article_date,
                                amount_usd=amount,
                                valuation_usd=valuation,
                                round_type=round_type,
                                lead_investors=lead_investors,
                                other_investors=other_investors,
                                source_url=link,
                                raw_data={
                                    "title": title,
                                    "feed": "Google News",
                                    "source": "google_news",
                                },
                            )

        return None
