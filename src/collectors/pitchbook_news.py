"""PitchBook News RSS collector for funding announcements.

PitchBook's data is paywalled but their news coverage is free via RSS.
Extracts funding rounds from their public news articles.
"""

import logging
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from src.collectors.base import BaseCollector, RawRound
from src.collectors.news_parser import (
    AMOUNT_PATTERN,
    FUNDING_KEYWORDS,
    RAISES_PATTERN,
    classify_sector,
    clean_company_name,
    extract_investors,
    extract_round_type,
    extract_valuation,
    parse_amount,
    parse_rss_date,
)

logger = logging.getLogger(__name__)

PITCHBOOK_FEEDS = [
    {
        "name": "PitchBook News",
        "url": "https://pitchbook.com/rss/news",
    },
]


class PitchBookNewsCollector(BaseCollector):
    """Collect funding rounds from PitchBook news RSS."""

    def source_type(self) -> str:
        return "news"

    async def collect(self) -> list[RawRound]:
        rounds: list[RawRound] = []

        async with httpx.AsyncClient(
            timeout=15,
            headers={"User-Agent": "raisefn/tracker (contact@raisefn.com)"},
            follow_redirects=True,
        ) as client:
            for feed in PITCHBOOK_FEEDS:
                try:
                    resp = await client.get(feed["url"])
                    resp.raise_for_status()

                    root = ET.fromstring(resp.text)
                    items = root.findall(".//item") or root.findall(
                        ".//{http://www.w3.org/2005/Atom}entry"
                    )

                    feed_count = 0
                    for item in items:
                        title = item.findtext("title") or ""
                        link = item.findtext("link") or ""
                        pub_date = item.findtext("pubDate") or ""
                        description = item.findtext("description") or ""

                        raw_round = self._parse_article(title, description, link, pub_date)
                        if raw_round:
                            rounds.append(raw_round)
                            feed_count += 1

                    logger.info(f"{feed['name']}: {feed_count} rounds extracted")
                except Exception as e:
                    logger.warning(f"Failed to fetch {feed['name']}: {e}")

        logger.info(f"Total: {len(rounds)} rounds from PitchBook News")
        return rounds

    def _parse_article(
        self, title: str, description: str, link: str, pub_date: str
    ) -> RawRound | None:
        combined = f"{title} {description}"
        lead_investors, other_investors = extract_investors(combined)
        valuation = extract_valuation(combined)
        sector = classify_sector(title, description, "PitchBook News")

        match = RAISES_PATTERN.match(title)
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
                    sector=sector,
                    lead_investors=lead_investors,
                    other_investors=other_investors,
                    source_url=link,
                    raw_data={
                        "title": title,
                        "feed": "PitchBook News",
                        "source": "pitchbook_news",
                    },
                )

        has_funding_keyword = any(kw in title.lower() for kw in FUNDING_KEYWORDS)
        if has_funding_keyword:
            amount_match = AMOUNT_PATTERN.search(title)
            if amount_match:
                amount = parse_amount(amount_match.group(1), amount_match.group(2))
                round_type = extract_round_type(combined)
                article_date = parse_rss_date(pub_date) or date.today()

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
                                sector=sector,
                                lead_investors=lead_investors,
                                other_investors=other_investors,
                                source_url=link,
                                raw_data={
                                    "title": title,
                                    "feed": "PitchBook News",
                                    "source": "pitchbook_news",
                                },
                            )

        return None
