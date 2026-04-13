"""LinkedIn-based angel investor discovery via DuckDuckGo.

Discovers new angel investors by searching DuckDuckGo for LinkedIn profiles
that self-identify as angel investors. LinkedIn profiles are well-indexed
by DDG (unlike Wellfound/Crunchbase which block crawlers).

Strategy:
1. Search DDG for site:linkedin.com/in "angel investor" with many variations
   (sector, location, role keywords)
2. Extract name + headline + location from DDG result snippets (no LinkedIn scraping needed)
3. Create new Investor records for names not already in the DB

This avoids scraping LinkedIn directly — all data comes from DDG result snippets
which contain the person's name, headline, and often location.

Rate limit: 3 seconds between DDG requests, 30s pause on rate limit.
"""

import asyncio
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "linkedin_angel_discovery"
REQUEST_DELAY = 3
RATE_LIMIT_PAUSE = 30
MAX_INVESTORS_PER_RUN = 5000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Each DDG query returns ~20-30 results. With ~100 queries that's up to 3000 unique people.
DISCOVERY_QUERIES = [
    # Core angel investor searches
    'site:linkedin.com/in "angel investor"',
    'site:linkedin.com/in "angel investor" "startup"',
    'site:linkedin.com/in "angel investor" "seed"',
    'site:linkedin.com/in "angel investor" "pre-seed"',
    'site:linkedin.com/in "angel investor" "early stage"',
    'site:linkedin.com/in "angel investor" "portfolio"',
    'site:linkedin.com/in "angel investor" "investments"',
    'site:linkedin.com/in "seed investor"',
    'site:linkedin.com/in "pre-seed investor"',
    'site:linkedin.com/in "startup investor"',
    'site:linkedin.com/in "early-stage investor"',
    'site:linkedin.com/in "individual investor" "startups"',
    'site:linkedin.com/in "angel" "investing in"',
    'site:linkedin.com/in "syndicate lead"',
    'site:linkedin.com/in "angellist syndicate"',
    'site:linkedin.com/in "angel fund"',
    'site:linkedin.com/in "solo GP"',
    'site:linkedin.com/in "emerging fund manager"',
    'site:linkedin.com/in "micro VC"',
    'site:linkedin.com/in "venture scout"',
    'site:linkedin.com/in "operator angel"',
    'site:linkedin.com/in "founder angel"',
    'site:linkedin.com/in "angel" "50+ investments"',
    'site:linkedin.com/in "angel" "100+ investments"',
    'site:linkedin.com/in "check writer" "startups"',
    # Sector-specific
    'site:linkedin.com/in "angel investor" "fintech"',
    'site:linkedin.com/in "angel investor" "AI"',
    'site:linkedin.com/in "angel investor" "artificial intelligence"',
    'site:linkedin.com/in "angel investor" "machine learning"',
    'site:linkedin.com/in "angel investor" "crypto"',
    'site:linkedin.com/in "angel investor" "blockchain"',
    'site:linkedin.com/in "angel investor" "web3"',
    'site:linkedin.com/in "angel investor" "DeFi"',
    'site:linkedin.com/in "angel investor" "SaaS"',
    'site:linkedin.com/in "angel investor" "enterprise"',
    'site:linkedin.com/in "angel investor" "healthcare"',
    'site:linkedin.com/in "angel investor" "health tech"',
    'site:linkedin.com/in "angel investor" "biotech"',
    'site:linkedin.com/in "angel investor" "climate"',
    'site:linkedin.com/in "angel investor" "cleantech"',
    'site:linkedin.com/in "angel investor" "edtech"',
    'site:linkedin.com/in "angel investor" "consumer"',
    'site:linkedin.com/in "angel investor" "marketplace"',
    'site:linkedin.com/in "angel investor" "deep tech"',
    'site:linkedin.com/in "angel investor" "hardware"',
    'site:linkedin.com/in "angel investor" "robotics"',
    'site:linkedin.com/in "angel investor" "food tech"',
    'site:linkedin.com/in "angel investor" "proptech"',
    'site:linkedin.com/in "angel investor" "real estate tech"',
    'site:linkedin.com/in "angel investor" "insurtech"',
    'site:linkedin.com/in "angel investor" "developer tools"',
    'site:linkedin.com/in "angel investor" "cybersecurity"',
    'site:linkedin.com/in "angel investor" "gaming"',
    'site:linkedin.com/in "angel investor" "social"',
    'site:linkedin.com/in "angel investor" "creator economy"',
    'site:linkedin.com/in "angel investor" "e-commerce"',
    'site:linkedin.com/in "angel investor" "logistics"',
    'site:linkedin.com/in "angel investor" "space"',
    'site:linkedin.com/in "angel investor" "defense"',
    # Location-specific
    'site:linkedin.com/in "angel investor" "San Francisco"',
    'site:linkedin.com/in "angel investor" "New York"',
    'site:linkedin.com/in "angel investor" "Los Angeles"',
    'site:linkedin.com/in "angel investor" "Austin"',
    'site:linkedin.com/in "angel investor" "Miami"',
    'site:linkedin.com/in "angel investor" "Seattle"',
    'site:linkedin.com/in "angel investor" "Boston"',
    'site:linkedin.com/in "angel investor" "Chicago"',
    'site:linkedin.com/in "angel investor" "Denver"',
    'site:linkedin.com/in "angel investor" "San Diego"',
    'site:linkedin.com/in "angel investor" "Portland"',
    'site:linkedin.com/in "angel investor" "Atlanta"',
    'site:linkedin.com/in "angel investor" "Dallas"',
    'site:linkedin.com/in "angel investor" "Washington DC"',
    'site:linkedin.com/in "angel investor" "London"',
    'site:linkedin.com/in "angel investor" "Berlin"',
    'site:linkedin.com/in "angel investor" "Paris"',
    'site:linkedin.com/in "angel investor" "Singapore"',
    'site:linkedin.com/in "angel investor" "Tel Aviv"',
    'site:linkedin.com/in "angel investor" "Dubai"',
    'site:linkedin.com/in "angel investor" "Toronto"',
    'site:linkedin.com/in "angel investor" "Mumbai"',
    'site:linkedin.com/in "angel investor" "Bangalore"',
    'site:linkedin.com/in "angel investor" "Sydney"',
    'site:linkedin.com/in "angel investor" "Tokyo"',
    'site:linkedin.com/in "angel investor" "Seoul"',
    'site:linkedin.com/in "angel investor" "São Paulo"',
    'site:linkedin.com/in "angel investor" "Lagos"',
    'site:linkedin.com/in "angel investor" "Nairobi"',
    'site:linkedin.com/in "angel investor" "Amsterdam"',
    'site:linkedin.com/in "angel investor" "Stockholm"',
]


class LinkedInAngelDiscovery(BaseEnricher):
    """Discover new angel investors from LinkedIn profiles via DuckDuckGo search."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())
        created_slugs: set[str] = set()
        seen_names: set[str] = set()
        rate_limit_count = 0

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for query in DISCOVERY_QUERIES:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                if rate_limit_count >= 3:
                    logger.warning(f"[{SOURCE_KEY}] Hit DDG rate limit 3 times, stopping")
                    break

                try:
                    people = await self._search_ddg(client, query)
                    new_count = 0
                    for person in people:
                        if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                            break
                        name = person.get("name")
                        if not name or name in seen_names:
                            continue
                        seen_names.add(name)

                        try:
                            created = await self._create_investor(session, person, created_slugs)
                            if created:
                                new_count += 1
                                result.records_updated += 1
                            else:
                                result.records_skipped += 1
                        except Exception as e:
                            result.errors.append(f"{name}: {e}")

                    if new_count > 0:
                        logger.info(
                            f"[{SOURCE_KEY}] '{query[:60]}' → "
                            f"{len(people)} results, {new_count} new"
                        )

                except _RateLimitedError:
                    rate_limit_count += 1
                    logger.warning(
                        f"[{SOURCE_KEY}] DDG rate limited ({rate_limit_count}/3), "
                        f"pausing {RATE_LIMIT_PAUSE}s"
                    )
                    await asyncio.sleep(RATE_LIMIT_PAUSE)
                    continue
                except Exception as e:
                    result.errors.append(f"Query error: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()
        logger.info(
            f"[{SOURCE_KEY}] Done: {result.records_updated} new investors, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _search_ddg(self, client: httpx.AsyncClient, query: str) -> list[dict]:
        """Search DDG and extract people data from result snippets.

        Returns list of dicts with keys: name, headline, location, linkedin_url
        """
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
        )
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        people = []

        for result_div in soup.select(".result"):
            link = result_div.select_one("a.result__a")
            snippet = result_div.select_one(".result__snippet")
            if not link:
                continue

            href = link.get("href", "")
            # Extract actual URL from DDG redirect
            url_match = re.search(r"uddg=([^&]+)", href)
            if url_match:
                actual_url = urllib.parse.unquote(url_match.group(1))
            else:
                actual_url = href

            # Only LinkedIn profile URLs
            if "linkedin.com/in/" not in actual_url:
                continue

            # Extract name from link text
            # DDG shows: "FirstName LastName - Title - Location | LinkedIn"
            link_text = link.get_text(strip=True)
            name = self._extract_name_from_title(link_text)
            if not name:
                continue

            # Extract headline/description from snippet
            headline = None
            location = None
            if snippet:
                snippet_text = snippet.get_text(strip=True)
                headline = self._extract_headline(snippet_text)
                location = self._extract_location(snippet_text, link_text)

            people.append(
                {
                    "name": name,
                    "headline": headline,
                    "location": location,
                    "linkedin_url": actual_url.split("?")[0],
                }
            )

        return people

    def _extract_name_from_title(self, title: str) -> str | None:
        """Extract person name from DDG result title.

        Titles look like:
        - "John Smith - Angel Investor - San Francisco | LinkedIn"
        - "Jane Doe | LinkedIn"
        - "John Smith - Founder & Angel Investor"
        """
        if not title:
            return None

        # Remove LinkedIn suffix
        title = re.sub(r"\s*\|\s*LinkedIn\s*$", "", title, flags=re.IGNORECASE)

        # Take first part before " - "
        name = title.split(" - ")[0].strip()

        # Remove common prefixes/suffixes
        name = re.sub(r"\s*\(.*?\)\s*", "", name)  # Remove parenthetical
        name = re.sub(r",.*$", "", name)  # Remove after comma (credentials)
        name = name.strip()

        # Validate: should be 2-5 words, no numbers, reasonable length
        if not name or len(name) < 3 or len(name) > 100:
            return None

        words = name.split()
        if len(words) < 2 or len(words) > 6:
            return None

        # Must start with uppercase
        if not words[0][0].isupper():
            return None

        # No numbers (skip "2nd", "3rd" connection results)
        if re.search(r"\d", name):
            return None

        return name

    def _extract_headline(self, snippet: str) -> str | None:
        """Extract headline/description from DDG snippet."""
        if not snippet or len(snippet) < 10:
            return None

        # Limit to reasonable length
        headline = snippet[:500].strip()

        # Remove common DDG noise
        headline = re.sub(r"^View .+'s profile on LinkedIn.*?\.", "", headline).strip()
        headline = re.sub(r"^LinkedIn.*?\.", "", headline).strip()

        if len(headline) > 20:
            return headline
        return None

    def _extract_location(self, snippet: str, title: str) -> str | None:
        """Try to extract location from snippet or title."""
        # Common city patterns in LinkedIn titles: "Name - Role - City, State"
        parts = title.split(" - ")
        if len(parts) >= 3:
            candidate = parts[-1].strip()
            candidate = re.sub(r"\s*\|\s*LinkedIn\s*$", "", candidate, flags=re.IGNORECASE).strip()
            # Looks like a location (has comma or known city pattern)
            if "," in candidate and len(candidate) < 80:
                return candidate
            # Known metro areas
            metros = [
                "San Francisco",
                "New York",
                "Los Angeles",
                "Austin",
                "Miami",
                "Seattle",
                "Boston",
                "Chicago",
                "Denver",
                "London",
                "Berlin",
                "Singapore",
                "Toronto",
                "Tel Aviv",
                "Dubai",
                "Mumbai",
                "Bangalore",
                "Sydney",
                "Tokyo",
                "Seoul",
                "Paris",
                "Amsterdam",
                "Stockholm",
                "São Paulo",
                "Lagos",
                "Nairobi",
                "Portland",
                "San Diego",
                "Atlanta",
                "Dallas",
                "Washington",
            ]
            if any(metro.lower() in candidate.lower() for metro in metros):
                return candidate

        # Check snippet for "Location: X" or "Greater X Area"
        loc_match = re.search(r"Greater (\w[\w\s]+) Area", snippet)
        if loc_match:
            return loc_match.group(0)

        return None

    async def _create_investor(
        self,
        session: AsyncSession,
        person: dict,
        created_slugs: set[str],
    ) -> bool:
        """Create an Investor record if the person doesn't already exist."""
        name = person["name"]
        slug = make_slug(name)

        if not slug or slug in created_slugs:
            return False

        # Check DB
        existing = await session.execute(select(Investor.id).where(Investor.slug == slug))
        if existing.scalar_one_or_none() is not None:
            return False

        description = person.get("headline")
        location = person.get("location")
        linkedin_url = person.get("linkedin_url")

        freshness = {SOURCE_KEY: datetime.now(timezone.utc).isoformat()}
        if linkedin_url:
            freshness["linkedin_url"] = linkedin_url

        investor = Investor(
            name=name,
            slug=slug,
            type="angel",
            description=description[:2000] if description else None,
            hq_location=location[:200] if location else None,
            investor_category="angel_investor",
            source_freshness=freshness,
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        created_slugs.add(slug)
        return True


class _RateLimitedError(Exception):
    """Raised on 403/429."""
