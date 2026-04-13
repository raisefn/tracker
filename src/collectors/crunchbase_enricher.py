"""Crunchbase public profile enricher for investors.

Scrapes public Crunchbase organization and person pages to extract
investment history, description, location, website, and social links.

Strategy:
1. For each investor not yet enriched by this source, try to find their
   Crunchbase profile via direct slug lookup or DuckDuckGo search.
2. Extract structured data from JSON-LD script tags and HTML meta tags.
3. Only update NULL/empty fields on the Investor model.

Rate limit: 3 seconds between requests, max 40 investors per run.
Stops gracefully on 403/429 responses.
"""

import asyncio
import json
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor, RoundInvestor

logger = logging.getLogger(__name__)

SOURCE_KEY = "crunchbase"
BATCH_SIZE = 40
REQUEST_DELAY = 3  # seconds between requests

CRUNCHBASE_BASE = "https://www.crunchbase.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;"
    "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

# Crunchbase type keywords for investor classification
TYPE_KEYWORDS = {
    "vc": [
        "venture capital",
        "venture fund",
        "vc firm",
        "venture firm",
        "venture investing",
        "early-stage venture",
    ],
    "angel": [
        "angel investor",
        "angel investing",
        "individual investor",
    ],
    "accelerator": [
        "accelerator",
        "incubator",
        "startup accelerator",
        "startup program",
    ],
    "corporate": [
        "corporate venture",
        "cvc",
        "strategic investor",
        "corporate investment",
    ],
    "fund_of_funds": [
        "fund of funds",
        "fund-of-funds",
    ],
    "family_office": [
        "family office",
        "family investment",
    ],
}


class CrunchbaseEnricher(BaseEnricher):
    """Enrich investor records with profile data from Crunchbase public pages."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        investors = await self._get_candidates(session)
        if not investors:
            logger.info(f"[{SOURCE_KEY}] No candidate investors to enrich")
            return result

        logger.info(f"[{SOURCE_KEY}] Processing {len(investors)} investor candidates")

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for investor in investors:
                try:
                    updated = await self._enrich_investor(client, investor)
                    if updated:
                        stamp_freshness(investor, self.source_name())
                        investor.last_enriched_at = datetime.now(timezone.utc)
                        result.records_updated += 1
                    else:
                        # Still stamp so we don't retry every run
                        stamp_freshness(investor, self.source_name())
                        result.records_skipped += 1
                except _RateLimitedError:
                    logger.warning(f"[{SOURCE_KEY}] Rate limited (429/403), stopping run")
                    result.errors.append("Rate limited, stopping early")
                    break
                except Exception as e:
                    error_msg = f"{investor.slug}: {e}"
                    logger.warning(f"[{SOURCE_KEY}] Error: {error_msg}")
                    result.errors.append(error_msg)
                    result.records_skipped += 1

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()
        logger.info(
            f"[{SOURCE_KEY}] Done: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _get_candidates(self, session: AsyncSession) -> list[Investor]:
        """Get investors not yet enriched by crunchbase, ordered by round participation count."""
        participation_count = (
            select(
                RoundInvestor.investor_id,
                func.count().label("deal_count"),
            )
            .group_by(RoundInvestor.investor_id)
            .subquery()
        )

        query = (
            select(Investor)
            .outerjoin(participation_count, Investor.id == participation_count.c.investor_id)
            .where(
                or_(
                    Investor.source_freshness.is_(None),
                    ~cast(Investor.source_freshness, String).contains(SOURCE_KEY),
                )
            )
            .order_by(func.coalesce(participation_count.c.deal_count, 0).desc())
            .limit(BATCH_SIZE)
        )

        rows = await session.execute(query)
        return list(rows.scalars().all())

    async def _enrich_investor(self, client: httpx.AsyncClient, investor: Investor) -> bool:
        """Try to find and scrape an investor's Crunchbase profile."""
        slug = investor.slug.replace("_", "-")
        profile_url = None
        html = None

        # Strategy 1: Try /organization/{slug} (for firms)
        url = f"{CRUNCHBASE_BASE}/organization/{slug}"
        html = await self._try_url(client, url)
        if html:
            profile_url = url

        # Strategy 2: Try /person/{slug} (for individuals)
        if html is None:
            await asyncio.sleep(REQUEST_DELAY)
            url = f"{CRUNCHBASE_BASE}/person/{slug}"
            html = await self._try_url(client, url)
            if html:
                profile_url = url

        # Strategy 3: DuckDuckGo search fallback
        if html is None:
            await asyncio.sleep(REQUEST_DELAY)
            found_url = await self._search_crunchbase(client, investor.name)
            if found_url:
                await asyncio.sleep(REQUEST_DELAY)
                html = await self._try_url(client, found_url)
                if html:
                    profile_url = found_url

        if html is None:
            return False

        return self._extract_profile_data(investor, html, profile_url)

    async def _try_url(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a URL, returning HTML on success or None on 404.

        Raises _RateLimitedError on 403/429.
        """
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            logger.debug(f"[{SOURCE_KEY}] HTTP error fetching {url}: {e}")
            return None

        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code == 404 or resp.status_code >= 400:
            return None

        return resp.text

    async def _search_crunchbase(self, client: httpx.AsyncClient, investor_name: str) -> str | None:
        """Search DuckDuckGo for the investor's Crunchbase profile URL."""
        query = f'site:crunchbase.com "{investor_name}"'
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            if resp.status_code in (403, 429):
                # DDG rate limit — don't escalate, just skip search
                logger.debug(f"[{SOURCE_KEY}] DDG rate limited for '{investor_name}'")
                return None
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.select("a.result__a"):
                href = link.get("href", "")
                # DuckDuckGo wraps URLs; extract the actual URL
                url_match = re.search(r"uddg=([^&]+)", href)
                if url_match:
                    actual_url = urllib.parse.unquote(url_match.group(1))
                else:
                    actual_url = href

                if (
                    "crunchbase.com/organization/" in actual_url
                    or "crunchbase.com/person/" in actual_url
                ):
                    # Clean trailing query params
                    return actual_url.split("?")[0]

        except _RateLimitedError:
            raise
        except Exception as e:
            logger.debug(f"[{SOURCE_KEY}] Search error for '{investor_name}': {e}")

        return None

    def _extract_profile_data(self, investor: Investor, html: str, profile_url: str | None) -> bool:
        """Extract profile data from a Crunchbase HTML page.

        Only updates fields that are currently NULL/empty on the investor.
        Returns True if any field was updated.
        """
        soup = BeautifulSoup(html, "html.parser")
        updated = False

        # Try to parse JSON-LD structured data first
        jsonld = self._extract_jsonld(soup)

        # --- Store Crunchbase URL in source_freshness ---
        if profile_url:
            freshness = investor.source_freshness or {}
            if "crunchbase_url" not in freshness:
                freshness["crunchbase_url"] = profile_url
                investor.source_freshness = freshness
                flag_modified(investor, "source_freshness")
                updated = True

        # --- Description ---
        if not investor.description:
            desc = self._extract_description(soup, jsonld)
            if desc:
                investor.description = desc[:2000]
                updated = True

        # --- Location ---
        if not investor.hq_location:
            location = self._extract_location(soup, jsonld, html)
            if location:
                investor.hq_location = location[:200]
                updated = True

        # --- Website ---
        if not investor.website:
            website = self._extract_website(soup, jsonld)
            if website:
                investor.website = website
                updated = True

        # --- Twitter ---
        if not investor.twitter:
            twitter = self._extract_twitter(soup, html)
            if twitter:
                investor.twitter = twitter[:200]
                updated = True

        # --- LinkedIn URL (store in source_freshness) ---
        linkedin = self._extract_linkedin(soup, html)
        if linkedin:
            freshness = investor.source_freshness or {}
            if "linkedin_url" not in freshness:
                freshness["linkedin_url"] = linkedin
                investor.source_freshness = freshness
                flag_modified(investor, "source_freshness")
                updated = True

        # --- Investment count and notable investments (store in source_freshness) ---
        inv_count, notable = self._extract_investments(soup, html)
        if inv_count is not None or notable:
            freshness = investor.source_freshness or {}
            if inv_count is not None and "crunchbase_investment_count" not in freshness:
                freshness["crunchbase_investment_count"] = inv_count
                investor.source_freshness = freshness
                flag_modified(investor, "source_freshness")
                updated = True
            if notable and "crunchbase_notable_investments" not in freshness:
                freshness["crunchbase_notable_investments"] = notable[:20]  # cap list size
                investor.source_freshness = freshness
                flag_modified(investor, "source_freshness")
                updated = True

        # --- Investor type ---
        if not investor.type:
            inv_type = self._detect_investor_type(soup, html, jsonld)
            if inv_type:
                investor.type = inv_type
                updated = True

        # --- Founded date (store in source_freshness) ---
        founded = self._extract_founded(soup, jsonld, html)
        if founded:
            freshness = investor.source_freshness or {}
            if "crunchbase_founded" not in freshness:
                freshness["crunchbase_founded"] = founded
                investor.source_freshness = freshness
                flag_modified(investor, "source_freshness")
                updated = True

        return updated

    def _extract_jsonld(self, soup: BeautifulSoup) -> dict | None:
        """Extract JSON-LD structured data from script tags."""
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                # Could be a single object or a list
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") in (
                            "Organization",
                            "Person",
                            "Corporation",
                            "FundingAgency",
                            "FinancialService",
                        ):
                            return item
                elif isinstance(data, dict):
                    if data.get("@type") in (
                        "Organization",
                        "Person",
                        "Corporation",
                        "FundingAgency",
                        "FinancialService",
                    ):
                        return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _extract_description(self, soup: BeautifulSoup, jsonld: dict | None) -> str | None:
        """Extract description from JSON-LD, meta tags, or page content."""
        # JSON-LD description
        if jsonld and jsonld.get("description"):
            desc = jsonld["description"].strip()
            if len(desc) > 20:
                return desc

        # og:description
        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        # name="description"
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        # Crunchbase-specific: look for description section
        for selector in [
            "[class*='description']",
            "[data-test='description']",
            ".profile-description",
            ".overview-description",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if len(text) > 20:
                    return text

        return None

    def _extract_location(self, soup: BeautifulSoup, jsonld: dict | None, html: str) -> str | None:
        """Extract location from JSON-LD, structured data, or page content."""
        # JSON-LD address
        if jsonld:
            address = jsonld.get("address")
            if isinstance(address, dict):
                parts = []
                for field in ("addressLocality", "addressRegion", "addressCountry"):
                    val = address.get(field)
                    if val:
                        parts.append(val.strip())
                if parts:
                    return ", ".join(parts)

            # Some pages have location as a simple string
            location = jsonld.get("location")
            if isinstance(location, str) and location.strip():
                return location.strip()
            if isinstance(location, dict):
                addr = location.get("address", {})
                if isinstance(addr, dict):
                    parts = []
                    for field in ("addressLocality", "addressRegion", "addressCountry"):
                        val = addr.get(field)
                        if val:
                            parts.append(val.strip())
                    if parts:
                        return ", ".join(parts)

        # Regex fallback: look for structured address data in HTML
        location_match = re.search(r'"addressLocality":\s*"([^"]+)"', html)
        if location_match:
            return location_match.group(1).strip()

        # Crunchbase-specific selectors
        for selector in [
            "[data-test='location']",
            "[class*='location']",
            ".field-type-location_identifiers",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) < 100:
                    return text

        return None

    def _extract_website(self, soup: BeautifulSoup, jsonld: dict | None) -> str | None:
        """Extract website URL from JSON-LD or page links."""
        # JSON-LD
        if jsonld:
            url = jsonld.get("url") or jsonld.get("sameAs")
            if isinstance(url, str) and "crunchbase.com" not in url:
                if url.startswith("http"):
                    return url
            if isinstance(url, list):
                for u in url:
                    if isinstance(u, str) and "crunchbase.com" not in u and u.startswith("http"):
                        return u

        # Look for explicit website links on the page
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True).lower()

            # Skip internal crunchbase links and social media
            if "crunchbase.com" in href:
                continue
            if any(
                domain in href
                for domain in [
                    "twitter.com",
                    "x.com",
                    "linkedin.com",
                    "facebook.com",
                    "github.com",
                    "youtube.com",
                    "instagram.com",
                ]
            ):
                continue

            # Look for explicit website labels
            if text in ("website", "site", "homepage", "web", "visit website") or "website" in text:
                if href.startswith("http"):
                    return href

        return None

    def _extract_twitter(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract Twitter/X handle from page links or content."""
        for link in soup.find_all("a", href=True):
            href = link["href"]
            twitter_match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/?$", href)
            if twitter_match:
                handle = twitter_match.group(1)
                if handle.lower() not in (
                    "share",
                    "intent",
                    "home",
                    "search",
                    "explore",
                    "i",
                    "hashtag",
                ):
                    return f"@{handle}"

        # Regex fallback in page content
        match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})", html)
        if match:
            handle = match.group(1)
            if handle.lower() not in (
                "share",
                "intent",
                "home",
                "search",
                "explore",
                "i",
                "hashtag",
            ):
                return f"@{handle}"

        return None

    def _extract_linkedin(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract LinkedIn URL from page links."""
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if "linkedin.com" in href and ("/in/" in href or "/company/" in href):
                return href.split("?")[0]

        # Regex fallback
        match = re.search(r"https?://(?:www\.)?linkedin\.com/(?:in|company)/[A-Za-z0-9_-]+", html)
        if match:
            return match.group(0)

        return None

    def _extract_investments(
        self, soup: BeautifulSoup, html: str
    ) -> tuple[int | None, list[str] | None]:
        """Extract investment count and notable portfolio company names."""
        inv_count = None
        notable = None

        # Look for investment count in page
        # Crunchbase often has "N Investments" or "Number of Investments: N"
        count_match = re.search(
            r"(?:Number of )?Investments?\s*(?::</?\s*)?(\d+)", html, re.IGNORECASE
        )
        if count_match:
            try:
                inv_count = int(count_match.group(1))
            except ValueError:
                pass

        # Look for portfolio company links in investments section
        companies = []
        inv_section = soup.find(
            lambda tag: (
                tag.name in ("h2", "h3", "h4")
                and tag.get_text(strip=True).lower().startswith("investment")
            )
        )
        if inv_section:
            # Walk siblings until next section header
            sibling = inv_section.find_next_sibling()
            while sibling and sibling.name not in ("h2", "h3", "h4"):
                for link in sibling.find_all("a", href=True):
                    href = link["href"]
                    if "/organization/" in href:
                        name = link.get_text(strip=True)
                        if name and name not in companies:
                            companies.append(name)
                sibling = sibling.find_next_sibling()

        # Also try to extract from any investment-related list
        if not companies:
            for link in soup.find_all("a", href=True):
                href = link["href"]
                # Portfolio company links within the page
                if "/organization/" in href and "crunchbase.com" in href:
                    name = link.get_text(strip=True)
                    if name and len(name) > 1 and name not in companies:
                        companies.append(name)
                        if len(companies) >= 20:
                            break

        if companies:
            notable = companies

        return inv_count, notable

    def _detect_investor_type(
        self, soup: BeautifulSoup, html: str, jsonld: dict | None
    ) -> str | None:
        """Detect investor type from page content."""
        text_lower = html.lower()

        # Check JSON-LD @type or additionalType
        if jsonld:
            jtype = jsonld.get("additionalType", "")
            if isinstance(jtype, str):
                jtype_lower = jtype.lower()
                for inv_type, keywords in TYPE_KEYWORDS.items():
                    if any(kw in jtype_lower for kw in keywords):
                        return inv_type

        # Check page content for type keywords
        for inv_type, keywords in TYPE_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return inv_type

        # Fallback: /person/ pages are likely angels
        if "/person/" in html:
            return "angel"

        return None

    def _extract_founded(self, soup: BeautifulSoup, jsonld: dict | None, html: str) -> str | None:
        """Extract founded date from JSON-LD or page content."""
        if jsonld:
            founded = jsonld.get("foundingDate")
            if founded:
                return str(founded)

        # Regex fallback
        match = re.search(r"Founded\s*(?:Date|:)?\s*(\d{4})", html, re.IGNORECASE)
        if match:
            return match.group(1)

        return None


class _RateLimitedError(Exception):
    """Raised when Crunchbase returns 403 or 429."""
