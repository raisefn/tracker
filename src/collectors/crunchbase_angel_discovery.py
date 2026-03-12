"""Crunchbase angel investor discovery.

Discovers new angel investors by crawling Crunchbase's public hub pages
and searching for angel investor profiles. Unlike the Crunchbase enricher
(which fills in data on known investors), this collector FINDS new ones.

Strategy:
1. Crawl Crunchbase hub pages (/hub/angel-investors, regional variants)
2. Search DuckDuckGo for "site:crunchbase.com/person angel investor" with variations
3. For each discovered profile, extract name/bio/location/investments
4. Create new Investor records (skip if slug already exists)

Rate limit: 4 seconds between requests, stops on 403/429.
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

SOURCE_KEY = "crunchbase_discovery"
REQUEST_DELAY = 4
MAX_INVESTORS_PER_RUN = 5000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Crunchbase hub pages listing angel investors
HUB_PAGES = [
    "https://www.crunchbase.com/hub/angel-investors",
    "https://www.crunchbase.com/hub/united-states-angel-investors",
    "https://www.crunchbase.com/hub/san-francisco-angel-investors",
    "https://www.crunchbase.com/hub/new-york-angel-investors",
    "https://www.crunchbase.com/hub/london-angel-investors",
    "https://www.crunchbase.com/hub/silicon-valley-angel-investors",
    "https://www.crunchbase.com/hub/los-angeles-angel-investors",
    "https://www.crunchbase.com/hub/boston-angel-investors",
    "https://www.crunchbase.com/hub/seattle-angel-investors",
    "https://www.crunchbase.com/hub/chicago-angel-investors",
    "https://www.crunchbase.com/hub/austin-angel-investors",
    "https://www.crunchbase.com/hub/miami-angel-investors",
    "https://www.crunchbase.com/hub/europe-angel-investors",
    "https://www.crunchbase.com/hub/asia-angel-investors",
    "https://www.crunchbase.com/hub/india-angel-investors",
    "https://www.crunchbase.com/hub/singapore-angel-investors",
    "https://www.crunchbase.com/hub/berlin-angel-investors",
    # Micro-VCs and seed funds (often operate like angels)
    "https://www.crunchbase.com/hub/micro-venture-capital-firms",
    "https://www.crunchbase.com/hub/seed-stage-venture-capital-firms",
    "https://www.crunchbase.com/hub/pre-seed-venture-capital-firms",
]

# DuckDuckGo queries to discover angel profiles on Crunchbase
DISCOVERY_QUERIES = [
    # Person profiles
    'site:crunchbase.com/person "angel investor"',
    'site:crunchbase.com/person "angel" "investments"',
    'site:crunchbase.com/person "seed investor"',
    'site:crunchbase.com/person "pre-seed"',
    'site:crunchbase.com/person "startup investor"',
    'site:crunchbase.com/person "angel investing"',
    'site:crunchbase.com/person "syndicate lead"',
    'site:crunchbase.com/person "angel fund"',
    'site:crunchbase.com/person "scout" "investor"',
    'site:crunchbase.com/person "operator" "investor"',
    # Sector-specific person searches
    'site:crunchbase.com/person "angel investor" "fintech"',
    'site:crunchbase.com/person "angel investor" "artificial intelligence"',
    'site:crunchbase.com/person "angel investor" "crypto"',
    'site:crunchbase.com/person "angel investor" "blockchain"',
    'site:crunchbase.com/person "angel investor" "saas"',
    'site:crunchbase.com/person "angel investor" "healthcare"',
    'site:crunchbase.com/person "angel investor" "biotech"',
    'site:crunchbase.com/person "angel investor" "climate"',
    'site:crunchbase.com/person "angel investor" "edtech"',
    'site:crunchbase.com/person "angel investor" "consumer"',
    'site:crunchbase.com/person "angel investor" "marketplace"',
    'site:crunchbase.com/person "angel investor" "deep tech"',
    'site:crunchbase.com/person "angel investor" "web3"',
    'site:crunchbase.com/person "angel investor" "defi"',
    # Location-specific
    'site:crunchbase.com/person "angel investor" "San Francisco"',
    'site:crunchbase.com/person "angel investor" "New York"',
    'site:crunchbase.com/person "angel investor" "London"',
    'site:crunchbase.com/person "angel investor" "Singapore"',
    'site:crunchbase.com/person "angel investor" "Berlin"',
    'site:crunchbase.com/person "angel investor" "Los Angeles"',
    'site:crunchbase.com/person "angel investor" "Austin"',
    'site:crunchbase.com/person "angel investor" "Miami"',
    'site:crunchbase.com/person "angel investor" "Seattle"',
    'site:crunchbase.com/person "angel investor" "Boston"',
    'site:crunchbase.com/person "angel investor" "Tel Aviv"',
    'site:crunchbase.com/person "angel investor" "Dubai"',
    'site:crunchbase.com/person "angel investor" "Toronto"',
    'site:crunchbase.com/person "angel investor" "Paris"',
    # Organization profiles (micro-VCs, angel groups)
    'site:crunchbase.com/organization "angel" "fund"',
    'site:crunchbase.com/organization "micro vc"',
    'site:crunchbase.com/organization "pre-seed fund"',
    'site:crunchbase.com/organization "angel group"',
    'site:crunchbase.com/organization "angel network"',
    'site:crunchbase.com/organization "solo gp"',
    'site:crunchbase.com/organization "emerging manager"',
]


class CrunchbaseAngelDiscovery(BaseEnricher):
    """Discover new angel investors from Crunchbase profiles."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        discovered_urls: set[str] = set()
        created_slugs: set[str] = set()

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # Phase 1: Crawl Crunchbase hub pages
            for url in HUB_PAGES:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                try:
                    profile_urls = await self._crawl_hub_page(client, url)
                    new = [u for u in profile_urls if u not in discovered_urls]
                    discovered_urls.update(new)
                    logger.info(f"[{SOURCE_KEY}] Hub '{url.split('/')[-1]}' → {len(new)} new URLs")
                    await asyncio.sleep(REQUEST_DELAY)
                except _RateLimitedError:
                    logger.warning(f"[{SOURCE_KEY}] Rate limited on hub pages, moving to search")
                    break
                except Exception as e:
                    logger.debug(f"[{SOURCE_KEY}] Hub error {url}: {e}")

            # Phase 2: DuckDuckGo search discovery
            for query in DISCOVERY_QUERIES:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                try:
                    urls = await self._search_ddg(client, query)
                    new_urls = [u for u in urls if u not in discovered_urls]
                    discovered_urls.update(new_urls)
                    if new_urls:
                        logger.info(f"[{SOURCE_KEY}] Query '{query[:50]}...' → {len(new_urls)} new URLs")
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

    async def _crawl_hub_page(
        self, client: httpx.AsyncClient, url: str
    ) -> list[str]:
        """Crawl a Crunchbase hub page for person/org profile URLs."""
        resp = await client.get(url)
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        profile_urls = []

        for link in soup.find_all("a", href=True):
            href = link["href"]
            # Normalize relative URLs
            if href.startswith("/"):
                href = f"https://www.crunchbase.com{href}"

            if "crunchbase.com/person/" in href or "crunchbase.com/organization/" in href:
                clean = href.split("?")[0].split("#")[0]
                # Skip hub/list pages themselves
                path_parts = clean.rstrip("/").split("/")
                if len(path_parts) >= 5:  # e.g. crunchbase.com/person/john-doe
                    profile_urls.append(clean)

        # Try to find pagination links
        next_pages = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "page=" in href or "/hub/" in href:
                if href.startswith("/"):
                    href = f"https://www.crunchbase.com{href}"
                if href != url and href not in next_pages:
                    next_pages.append(href)

        # Crawl up to 3 additional pages from this hub
        for page_url in next_pages[:3]:
            try:
                await asyncio.sleep(REQUEST_DELAY)
                resp = await client.get(page_url)
                if resp.status_code in (403, 429):
                    break
                if resp.status_code == 200:
                    page_soup = BeautifulSoup(resp.text, "html.parser")
                    for link in page_soup.find_all("a", href=True):
                        href = link["href"]
                        if href.startswith("/"):
                            href = f"https://www.crunchbase.com{href}"
                        if "crunchbase.com/person/" in href or \
                           "crunchbase.com/organization/" in href:
                            clean = href.split("?")[0].split("#")[0]
                            if clean not in profile_urls:
                                profile_urls.append(clean)
            except Exception:
                break

        return profile_urls

    async def _search_ddg(
        self, client: httpx.AsyncClient, query: str
    ) -> list[str]:
        """Search DuckDuckGo for Crunchbase profile URLs."""
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
            url_match = re.search(r"uddg=([^&]+)", href)
            if url_match:
                actual_url = urllib.parse.unquote(url_match.group(1))
            else:
                actual_url = href

            if "crunchbase.com/person/" in actual_url or \
               "crunchbase.com/organization/" in actual_url:
                clean = actual_url.split("?")[0]
                urls.append(clean)

        return urls

    async def _scrape_and_create(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        url: str,
        created_slugs: set[str],
    ) -> bool:
        """Scrape a Crunchbase profile and create an Investor if new."""
        resp = await client.get(url)
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return False

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        is_person = "/person/" in url
        is_org = "/organization/" in url

        name = self._extract_name(soup, html)
        if not name or len(name) < 2 or len(name) > 300:
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

        # For person profiles, verify they're an investor
        if is_person and not self._is_investor(html):
            return False

        # Extract data
        description = self._extract_description(soup, html)
        location = self._extract_location(soup, html)
        twitter = self._extract_twitter(soup, html)
        website = self._extract_website(soup, html)
        inv_type = self._detect_type(html, is_person, is_org)
        category = self._detect_category(html, is_person)

        investor = Investor(
            name=name,
            slug=slug,
            type=inv_type,
            description=description[:2000] if description else None,
            hq_location=location[:200] if location else None,
            twitter=twitter[:200] if twitter else None,
            website=website,
            investor_category=category,
            source_freshness={
                SOURCE_KEY: datetime.now(timezone.utc).isoformat(),
                "crunchbase_url": url,
            },
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        created_slugs.add(slug)
        return True

    def _extract_name(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract name from Crunchbase profile."""
        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("name"):
                    return data["name"].strip()
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("name"):
                            return item["name"].strip()
            except (json.JSONDecodeError, TypeError):
                continue

        # og:title
        meta = soup.find("meta", attrs={"property": "og:title"})
        if meta and meta.get("content"):
            title = meta["content"].strip()
            # Remove " - Crunchbase Person/Organization Profile"
            name = re.split(r"\s*[-|–—]\s*Crunchbase", title, flags=re.IGNORECASE)[0].strip()
            if name and len(name) > 1:
                return name

        # <title>
        title_tag = soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True)
            name = re.split(r"\s*[-|–—]\s*Crunchbase", title, flags=re.IGNORECASE)[0].strip()
            if name and len(name) > 1:
                return name

        # h1
        h1 = soup.find("h1")
        if h1:
            name = h1.get_text(strip=True)
            if name and 1 < len(name) < 200:
                return name

        return None

    def _is_investor(self, html: str) -> bool:
        """Check if this person is actually an investor."""
        lower = html.lower()
        investor_signals = [
            "angel investor", "angel investing", "seed investor",
            "pre-seed", "venture partner", "syndicate",
            "portfolio companies", "investments include",
            "invested in", "startup investor", "angel fund",
            "number of investments", "investment highlights",
            "limited partner", "scout", "check size",
            "angel group", "angel network",
            "investment activity", "lead investor",
        ]
        # Require at least one investor signal
        return any(signal in lower for signal in investor_signals)

    def _extract_description(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract description."""
        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and data.get("description"):
                    desc = data["description"].strip()
                    if len(desc) > 20:
                        return desc
            except (json.JSONDecodeError, TypeError):
                continue

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

        return None

    def _extract_location(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract location."""
        # JSON-LD address
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    address = data.get("address")
                    if isinstance(address, dict):
                        parts = []
                        for field in ("addressLocality", "addressRegion", "addressCountry"):
                            val = address.get(field)
                            if val:
                                parts.append(val.strip())
                        if parts:
                            return ", ".join(parts)
            except (json.JSONDecodeError, TypeError):
                continue

        match = re.search(r'"addressLocality":\s*"([^"]+)"', html)
        if match:
            return match.group(1).strip()

        for selector in [
            "[data-test='location']", "[class*='location']",
            ".field-type-location_identifiers",
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
                if handle.lower() not in ("share", "intent", "home", "search", "explore", "i", "hashtag"):
                    return f"@{handle}"

        match = re.search(r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})', html)
        if match:
            handle = match.group(1)
            if handle.lower() not in ("share", "intent", "home", "search", "explore", "i", "hashtag"):
                return f"@{handle}"

        return None

    def _extract_website(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract website URL."""
        # JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict):
                    url = data.get("url") or data.get("sameAs")
                    if isinstance(url, str) and "crunchbase.com" not in url and url.startswith("http"):
                        return url
            except (json.JSONDecodeError, TypeError):
                continue

        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True).lower()
            if any(d in href for d in [
                "crunchbase.com", "twitter.com", "x.com", "linkedin.com",
                "facebook.com", "github.com", "youtube.com",
            ]):
                continue
            if text in ("website", "site", "homepage", "visit website") or "website" in text:
                if href.startswith("http"):
                    return href

        return None

    def _detect_type(self, html: str, is_person: bool, is_org: bool) -> str:
        """Detect investor type."""
        lower = html.lower()

        if is_person:
            return "angel"

        # For organizations, try to classify
        type_keywords = {
            "vc": ["venture capital", "venture fund", "vc firm"],
            "angel": ["angel group", "angel network", "angel fund"],
            "accelerator": ["accelerator", "incubator"],
            "corporate": ["corporate venture", "cvc"],
            "family_office": ["family office"],
        }
        for inv_type, keywords in type_keywords.items():
            if any(kw in lower for kw in keywords):
                return inv_type

        return "vc"  # Default for orgs

    def _detect_category(self, html: str, is_person: bool) -> str:
        """Detect investor category."""
        lower = html.lower()

        if is_person:
            return "angel_investor"

        if any(kw in lower for kw in ["micro vc", "micro-vc", "micro venture"]):
            return "micro_vc"
        if any(kw in lower for kw in ["pre-seed fund", "pre seed fund"]):
            return "pre_seed_fund"
        if any(kw in lower for kw in ["angel group", "angel network"]):
            return "angel_group"
        if any(kw in lower for kw in ["solo gp", "emerging manager"]):
            return "emerging_manager"

        return "pre_seed_fund"


class _RateLimitedError(Exception):
    """Raised on 403/429."""
