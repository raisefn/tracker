"""RSS feed collector for startup funding news.

Polls major tech/startup news RSS feeds for funding announcements,
then extracts structured data using shared regex patterns.

Designed to run every 15 minutes for near-real-time coverage.
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
    clean_company_name,
    extract_investors,
    extract_round_type,
    extract_valuation,
    parse_amount,
    parse_rss_date,
)

logger = logging.getLogger(__name__)

# Keep _extract_investors as a re-export for backwards compat with tests
_extract_investors = extract_investors

RSS_FEEDS = [
    # --- General startup funding ---
    {
        "name": "TechCrunch",
        "url": "https://techcrunch.com/feed/",
    },
    {
        "name": "Crunchbase News Venture",
        "url": "https://news.crunchbase.com/sections/venture/feed/",
    },
    {
        "name": "VentureBeat",
        "url": "https://venturebeat.com/feed/",
    },
    {
        "name": "Forbes Innovation",
        "url": "https://www.forbes.com/innovation/feed/",
    },
    {
        "name": "Axios",
        "url": "https://api.axios.com/feed/",
    },
    {
        "name": "SaaStr",
        "url": "https://www.saastr.com/feed/",
    },
    # --- Crypto-specific ---
    {
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
    },
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/?outputType=xml&_website=coindesk",
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
    },
    {
        "name": "DL News",
        "url": "https://www.dlnews.com/arc/outboundfeeds/rss/",
    },
    {
        "name": "Blockworks",
        "url": "https://blockworks.com/feed",
    },
    # --- Regional / ecosystem ---
    {
        "name": "EU-Startups",
        "url": "https://www.eu-startups.com/feed",
    },
    {
        "name": "TechCrunch Venture",
        "url": "https://techcrunch.com/category/venture/feed/",
    },
    {
        "name": "TechFundingNews",
        "url": "https://techfundingnews.com/feed/",
    },
    {
        "name": "Sifted",
        "url": "https://sifted.eu/feed",
    },
    # --- Additional general startup ---
    {
        "name": "Wired Business",
        "url": "https://www.wired.com/feed/category/business/latest/rss",
    },
    {
        "name": "Finsmes",
        "url": "https://www.finsmes.com/feed",
    },
    {
        "name": "AlleyWatch",
        "url": "https://www.alleywatch.com/feed/",
    },
    {
        "name": "ArcticStartup",
        "url": "https://arcticstartup.com/feed/",
    },
    {
        "name": "Silicon Republic",
        "url": "https://www.siliconrepublic.com/feed",
    },
    {
        "name": "UKTN",
        "url": "https://www.uktech.news/feed",
    },
    {
        "name": "Tech.eu",
        "url": "https://tech.eu/feed/",
    },
    {
        "name": "Pulse 2.0",
        "url": "https://pulse2.com/feed/",
    },
    # --- Fintech-specific ---
    {
        "name": "Finextra",
        "url": "https://www.finextra.com/rss/headlines.aspx",
    },
    {
        "name": "Finovate",
        "url": "https://finovate.com/feed/",
    },
    # --- Biotech/healthtech ---
    {
        "name": "FierceBiotech",
        "url": "https://www.fiercebiotech.com/rss/xml",
    },
    {
        "name": "MobiHealthNews",
        "url": "https://www.mobihealthnews.com/feed",
    },
    # --- Climate/cleantech ---
    {
        "name": "CleanTechnica",
        "url": "https://cleantechnica.com/feed/",
    },
    # --- India/Asia ---
    {
        "name": "Inc42",
        "url": "https://inc42.com/feed/",
    },
    {
        "name": "YourStory",
        "url": "https://yourstory.com/feed",
    },
    {
        "name": "e27",
        "url": "https://e27.co/feed/",
    },
    # --- Latin America ---
    # --- Africa ---
    {
        "name": "TechCabal",
        "url": "https://techcabal.com/feed/",
    },
    {
        "name": "Disrupt Africa",
        "url": "https://disrupt-africa.com/feed/",
    },
    # --- Additional high-quality sources ---
    {
        "name": "Crunchbase News Daily",
        "url": "https://news.crunchbase.com/feed/",
    },
    {
        "name": "PEHub",
        "url": "https://www.pehub.com/feed/",
    },
    {
        "name": "GeekWire",
        "url": "https://www.geekwire.com/feed/",
    },
    {
        "name": "TechStartups",
        "url": "https://techstartups.com/feed/",
    },
    {
        "name": "BetaKit",
        "url": "https://betakit.com/feed/",
    },
    {
        "name": "FinanceMagnates",
        "url": "https://www.financemagnates.com/feed/",
    },
    {
        "name": "The Real Deal",
        "url": "https://therealdeal.com/feed/",
    },
    {
        "name": "PYMNTS",
        "url": "https://www.pymnts.com/feed/",
    },
    {
        "name": "CB Insights",
        "url": "https://www.cbinsights.com/research/feed/",
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
    },
    {
        "name": "Benzinga",
        "url": "https://www.benzinga.com/feed",
    },
    {
        "name": "Silicon Angle",
        "url": "https://siliconangle.com/feed/",
    },
    {
        "name": "Startup Daily Australia",
        "url": "https://www.startupdaily.net/feed/",
    },
    {
        "name": "TNW",
        "url": "https://thenextweb.com/feed",
    },
]


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
            follow_redirects=True,
        ) as client:
            for feed in RSS_FEEDS:
                try:
                    feed_rounds = await self._fetch_feed(client, feed)
                    rounds.extend(feed_rounds)
                    logger.info(f"{feed['name']}: {len(feed_rounds)} rounds extracted")
                except Exception as e:
                    logger.warning(f"Failed to fetch {feed['name']}: {e}")

        logger.info(f"Total: {len(rounds)} rounds from RSS feeds")
        return rounds

    async def _fetch_feed(self, client: httpx.AsyncClient, feed: dict) -> list[RawRound]:
        """Fetch and parse a single RSS feed."""
        resp = await client.get(feed["url"])
        resp.raise_for_status()

        rounds: list[RawRound] = []
        root = ET.fromstring(resp.text)

        # Handle both RSS 2.0 and Atom feeds
        items = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")

        for item in items:
            title = (
                item.findtext("title") or item.findtext("{http://www.w3.org/2005/Atom}title") or ""
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

        lead_investors, other_investors = extract_investors(combined)
        valuation = extract_valuation(combined)

        # Try the "X raises $Y" pattern first
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
