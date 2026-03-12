"""Wellfound angel investor discovery.

Discovers new angel investors by crawling Wellfound's public directory
and people pages. Unlike the AngelList enricher (which fills in data on
known investors), this collector FINDS new investors we don't have yet.

Strategy:
1. Crawl Wellfound's role-based people directory pages
2. Search DuckDuckGo for "site:wellfound.com angel investor" with pagination
3. For each discovered profile, extract name/bio/location/twitter
4. Create new Investor records (skip if slug already exists)

Rate limit: 3 seconds between requests, stops on 403/429.
"""

import asyncio
import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "wellfound_discovery"
REQUEST_DELAY = 3
MAX_INVESTORS_PER_RUN = 500

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# DuckDuckGo search queries to find angel investor profiles on Wellfound
DISCOVERY_QUERIES = [
    'site:wellfound.com/people "angel investor"',
    'site:wellfound.com/people "angel" "investor"',
    'site:wellfound.com/people "seed investor"',
    'site:wellfound.com/people "pre-seed"',
    'site:wellfound.com/people "startup investor"',
    'site:wellfound.com/people "venture partner"',
    'site:wellfound.com/people "investing in startups"',
    'site:wellfound.com/people "angel investments"',
    'site:wellfound.com/people "check size"',
    'site:wellfound.com/people "portfolio companies"',
    # Sector-specific
    'site:wellfound.com/people "angel investor" "fintech"',
    'site:wellfound.com/people "angel investor" "AI"',
    'site:wellfound.com/people "angel investor" "crypto"',
    'site:wellfound.com/people "angel investor" "saas"',
    'site:wellfound.com/people "angel investor" "health"',
    'site:wellfound.com/people "angel investor" "climate"',
    'site:wellfound.com/people "angel investor" "biotech"',
    'site:wellfound.com/people "angel investor" "deep tech"',
    'site:wellfound.com/people "angel investor" "consumer"',
    'site:wellfound.com/people "angel investor" "marketplace"',
    # Location-specific
    'site:wellfound.com/people "angel investor" "San Francisco"',
    'site:wellfound.com/people "angel investor" "New York"',
    'site:wellfound.com/people "angel investor" "London"',
    'site:wellfound.com/people "angel investor" "Singapore"',
    'site:wellfound.com/people "angel investor" "Berlin"',
    'site:wellfound.com/people "angel investor" "Los Angeles"',
    'site:wellfound.com/people "angel investor" "Austin"',
    'site:wellfound.com/people "angel investor" "Miami"',
    'site:wellfound.com/people "angel investor" "Seattle"',
    'site:wellfound.com/people "angel investor" "Boston"',
    # Syndicate leaders
    'site:wellfound.com/people "syndicate"',
    'site:wellfound.com/people "syndicate lead"',
    'site:wellfound.com/people "angellist syndicate"',
    # Operator angels
    'site:wellfound.com/people "founder" "angel"',
    'site:wellfound.com/people "exited" "angel"',
    'site:wellfound.com/people "operator" "investor"',
]

# Direct Wellfound directory URLs to crawl
WELLFOUND_DIRECTORY_URLS = [
    "https://wellfound.com/people/investors",
    "https://wellfound.com/people?role=investor",
    "https://wellfound.com/people?role=angel",
    "https://wellfound.com/discover/people?role=investor",
]


class WellfoundAngelDiscovery(BaseEnricher):
    """Discover new angel investors from Wellfound/AngelList profiles."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Track discovered URLs to avoid duplicate scrapes within a run
        discovered_urls: set[str] = set()
        created_slugs: set[str] = set()

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # Phase 1: Try Wellfound directory pages directly
            for url in WELLFOUND_DIRECTORY_URLS:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                try:
                    profile_urls = await self._crawl_directory_page(client, url)
                    discovered_urls.update(profile_urls)
                    await asyncio.sleep(REQUEST_DELAY)
                except _RateLimitedError:
                    logger.warning(f"[{SOURCE_KEY}] Rate limited on directory crawl, moving to search")
                    break
                except Exception as e:
                    logger.debug(f"[{SOURCE_KEY}] Directory page error {url}: {e}")

            # Phase 2: DuckDuckGo search discovery
            for query in DISCOVERY_QUERIES:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                try:
                    urls = await self._search_ddg(client, query)
                    new_urls = [u for u in urls if u not in discovered_urls]
                    discovered_urls.update(new_urls)
                    logger.info(f"[{SOURCE_KEY}] Query '{query[:50]}...' found {len(new_urls)} new URLs")
                except _RateLimitedError:
                    logger.warning(f"[{SOURCE_KEY}] DDG rate limited, pausing 30s")
                    await asyncio.sleep(30)
                    continue
                except Exception as e:
                    logger.debug(f"[{SOURCE_KEY}] Search error: {e}")

                await asyncio.sleep(REQUEST_DELAY)

            # Phase 3: Scrape each discovered profile
            logger.info(f"[{SOURCE_KEY}] Scraping {len(discovered_urls)} discovered profiles")

            for url in discovered_urls:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                try:
                    created = await self._scrape_and_create(
                        client, session, url, created_slugs
                    )
                    if created:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                except _RateLimitedError:
                    logger.warning(f"[{SOURCE_KEY}] Rate limited on scrape, stopping")
                    result.errors.append("Rate limited during scraping")
                    break
                except Exception as e:
                    result.errors.append(f"{url}: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()
        logger.info(
            f"[{SOURCE_KEY}] Done: {result.records_updated} new investors, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors. "
            f"Total URLs discovered: {len(discovered_urls)}"
        )
        return result

    async def _crawl_directory_page(
        self, client: httpx.AsyncClient, url: str
    ) -> list[str]:
        """Crawl a Wellfound directory/listing page for profile URLs."""
        resp = await client.get(url)
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        profile_urls = []

        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "/people/" in href and href.count("/") >= 2:
                # Normalize to absolute URL
                if href.startswith("/"):
                    href = f"https://wellfound.com{href}"
                if "wellfound.com/people/" in href:
                    # Skip generic pages
                    path = href.split("/people/")[-1].split("?")[0].split("#")[0]
                    if path and path not in ("investors", "founders", ""):
                        profile_urls.append(href.split("?")[0])

        # Also check for Next.js __NEXT_DATA__ JSON
        script = soup.find("script", id="__NEXT_DATA__")
        if script and script.string:
            try:
                data = json.loads(script.string)
                self._extract_urls_from_json(data, profile_urls)
            except (json.JSONDecodeError, TypeError):
                pass

        return profile_urls

    def _extract_urls_from_json(self, obj, urls: list, depth: int = 0) -> None:
        """Recursively extract people URLs from nested JSON data."""
        if depth > 10:
            return
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in ("slug", "permalink") and isinstance(val, str) and len(val) > 2:
                    candidate = f"https://wellfound.com/people/{val}"
                    if candidate not in urls:
                        urls.append(candidate)
                else:
                    self._extract_urls_from_json(val, urls, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_urls_from_json(item, urls, depth + 1)

    async def _search_ddg(
        self, client: httpx.AsyncClient, query: str
    ) -> list[str]:
        """Search DuckDuckGo and extract Wellfound profile URLs from results."""
        urls = []

        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
        )
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return urls

        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            # DuckDuckGo wraps URLs
            url_match = re.search(r"uddg=([^&]+)", href)
            if url_match:
                actual_url = urllib.parse.unquote(url_match.group(1))
            else:
                actual_url = href

            if "wellfound.com/people/" in actual_url:
                clean = actual_url.split("?")[0]
                path = clean.split("/people/")[-1]
                if path and path not in ("investors", "founders", ""):
                    urls.append(clean)

        return urls

    async def _scrape_and_create(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        url: str,
        created_slugs: set[str],
    ) -> bool:
        """Scrape a Wellfound profile and create an Investor if new."""
        resp = await client.get(url)
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return False

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Extract name
        name = self._extract_name(soup, html)
        if not name or len(name) < 2 or len(name) > 200:
            return False

        slug = make_slug(name)
        if not slug or slug in created_slugs:
            return False

        # Check if already exists
        existing = await session.execute(
            select(Investor.id).where(Investor.slug == slug)
        )
        if existing.scalar_one_or_none() is not None:
            return False

        # Verify this person is actually an investor
        if not self._is_investor(html):
            return False

        # Extract profile data
        description = self._extract_description(soup, html)
        location = self._extract_location(soup, html)
        twitter = self._extract_twitter(soup, html)
        website = self._extract_website(soup)

        investor = Investor(
            name=name,
            slug=slug,
            type="angel",
            description=description[:2000] if description else None,
            hq_location=location[:200] if location else None,
            twitter=twitter[:200] if twitter else None,
            website=website,
            investor_category="angel_investor",
            source_freshness={
                SOURCE_KEY: datetime.now(timezone.utc).isoformat(),
                "wellfound_url": url,
            },
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        created_slugs.add(slug)
        return True

    def _extract_name(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract person's name from the profile page."""
        # og:title meta tag
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta and meta.get("content"):
            title = meta["content"].strip()
            # Wellfound titles often have "Name - Role" or "Name | Wellfound"
            name = re.split(r"\s*[-|–—]\s*", title)[0].strip()
            if name and len(name) > 1:
                return name

        # <title> tag
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            name = re.split(r"\s*[-|–—]\s*", title)[0].strip()
            if name and len(name) > 1:
                return name

        # h1 tag (usually the name on profile pages)
        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)
            if name and 1 < len(name) < 100:
                return name

        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("name"):
                    return data["name"]
            except (json.JSONDecodeError, TypeError):
                continue

        return None

    def _is_investor(self, html: str) -> bool:
        """Check if the page content suggests this person is an investor."""
        lower = html.lower()
        investor_signals = [
            "angel investor", "angel investing", "seed investor",
            "pre-seed", "venture partner", "syndicate",
            "portfolio companies", "investments include",
            "invested in", "backed by", "startup investor",
            "check size", "investing in startups", "angel fund",
            "limited partner", "lp in", "scout",
        ]
        return any(signal in lower for signal in investor_signals)

    def _extract_description(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract bio/description."""
        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        for selector in [
            "[data-testid='bio']", ".bio", ".about-section",
            ".profile-bio", "[class*='biography']", "[class*='about']",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if len(text) > 20:
                    return text

        return None

    def _extract_location(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract location."""
        match = re.search(r'"addressLocality":\s*"([^"]+)"', html)
        if match:
            return match.group(1).strip()

        for selector in [
            "[data-testid='location']", ".location", "[class*='location']",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) < 100:
                    return text

        return None

    def _extract_twitter(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract Twitter handle."""
        for link in soup.find_all("a", href=True):
            href = link["href"]
            match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/?$", href)
            if match:
                handle = match.group(1)
                if handle.lower() not in ("share", "intent", "home", "search"):
                    return f"@{handle}"

        match = re.search(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})', html)
        if match:
            handle = match.group(1)
            if handle.lower() not in ("share", "intent", "home", "search"):
                return f"@{handle}"

        return None

    def _extract_website(self, soup: BeautifulSoup) -> str | None:
        """Extract website URL."""
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True).lower()
            if any(d in href for d in [
                "wellfound.com", "angellist.com", "twitter.com", "x.com",
                "linkedin.com", "facebook.com", "github.com",
            ]):
                continue
            if text in ("website", "site", "homepage") or "website" in text:
                if href.startswith("http"):
                    return href
        return None


class _RateLimitedError(Exception):
    """Raised on 403/429."""
