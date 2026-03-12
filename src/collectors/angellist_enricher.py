"""AngelList/Wellfound investor profile enricher.

Scrapes public investor profiles from Wellfound (formerly AngelList)
to fill in missing profile data (description, location, website,
twitter) for early-stage investors that have zero profile data.

Strategy:
1. Try direct slug lookup at wellfound.com/people/{slug} and /company/{slug}
2. Fall back to DuckDuckGo search: site:wellfound.com "{investor_name}"
3. Parse the HTML profile page with BeautifulSoup
4. Only update NULL/empty fields on the Investor model

Rate limit: 1 request per 3 seconds, max 50 investors per run.
Stops gracefully on 403/429 responses.
"""

import asyncio
import logging
import re
import urllib.parse
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor, Round, RoundInvestor

logger = logging.getLogger(__name__)

SOURCE_KEY = "angellist"
BATCH_SIZE = 50
REQUEST_DELAY = 3  # seconds between requests

WELLFOUND_BASE = "https://wellfound.com"

EARLY_STAGE_TYPES = {"pre_seed", "seed", "angel", "series_a", "grant"}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


class AngelListInvestorEnricher(BaseEnricher):
    """Enrich investor records with profile data from Wellfound/AngelList."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        investors = await self._get_candidates(session)
        if not investors:
            logger.info("[angellist] No candidate investors to enrich")
            return result

        logger.info(f"[angellist] Processing {len(investors)} investor candidates")

        async with httpx.AsyncClient(
            timeout=15,
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
                        # Still stamp freshness so we don't retry every run
                        stamp_freshness(investor, self.source_name())
                        result.records_skipped += 1
                except _RateLimitedError:
                    logger.warning("[angellist] Rate limited (429/403), stopping run")
                    result.errors.append("Rate limited, stopping early")
                    break
                except Exception as e:
                    error_msg = f"{investor.slug}: {e}"
                    logger.warning(f"[angellist] Error: {error_msg}")
                    result.errors.append(error_msg)
                    result.records_skipped += 1

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()
        logger.info(
            f"[angellist] Done: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _get_candidates(self, session: AsyncSession) -> list[Investor]:
        """Get investors with most early-stage round participations and no angellist data.

        Prioritizes investors that participate in the most early-stage rounds,
        since AngelList/Wellfound data is most useful for these.
        """
        # Subquery: count early-stage participations per investor
        early_count = (
            select(
                RoundInvestor.investor_id,
                func.count().label("early_rounds"),
            )
            .join(Round, Round.id == RoundInvestor.round_id)
            .where(Round.round_type.in_(EARLY_STAGE_TYPES))
            .group_by(RoundInvestor.investor_id)
            .subquery()
        )

        # Main query: investors not yet enriched by this source
        query = (
            select(Investor)
            .outerjoin(early_count, Investor.id == early_count.c.investor_id)
            .where(
                Investor.source_freshness.is_(None)
                | ~Investor.source_freshness.has_key(SOURCE_KEY)  # noqa: W601
            )
            .order_by(early_count.c.early_rounds.desc().nullslast())
            .limit(BATCH_SIZE)
        )

        rows = await session.execute(query)
        return list(rows.scalars().all())

    async def _enrich_investor(self, client: httpx.AsyncClient, investor: Investor) -> bool:
        """Try to find and scrape an investor's Wellfound profile."""
        slug = investor.slug.replace("_", "-")

        # Strategy 1: Try /people/{slug} (individual investors / angels)
        html = await self._try_url(client, f"{WELLFOUND_BASE}/people/{slug}")

        # Strategy 2: Try /company/{slug} (VC firms, funds)
        if html is None:
            await asyncio.sleep(REQUEST_DELAY)
            html = await self._try_url(client, f"{WELLFOUND_BASE}/company/{slug}")

        # Strategy 3: DuckDuckGo search fallback
        if html is None:
            await asyncio.sleep(REQUEST_DELAY)
            profile_url = await self._search_wellfound(client, investor.name)
            if profile_url:
                await asyncio.sleep(REQUEST_DELAY)
                html = await self._try_url(client, profile_url)

        if html is None:
            return False

        return self._extract_investor_data(investor, html)

    async def _try_url(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a URL, returning HTML on success or None on 404.

        Raises _RateLimitedError on 403/429.
        """
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            logger.debug(f"[angellist] HTTP error fetching {url}: {e}")
            return None

        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code == 404 or resp.status_code >= 400:
            return None

        return resp.text

    async def _search_wellfound(
        self, client: httpx.AsyncClient, investor_name: str
    ) -> str | None:
        """Search DuckDuckGo for the investor's Wellfound profile URL."""
        query = f'site:wellfound.com "{investor_name}"'
        try:
            resp = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
            )
            if resp.status_code in (403, 429):
                raise _RateLimitedError()
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

                if "wellfound.com/people/" in actual_url or "wellfound.com/company/" in actual_url:
                    return actual_url

        except _RateLimitedError:
            raise
        except Exception as e:
            logger.debug(f"[angellist] Search error for '{investor_name}': {e}")

        return None

    def _extract_investor_data(self, investor: Investor, html: str) -> bool:
        """Extract profile data from a Wellfound HTML page.

        Only updates fields that are currently NULL/empty on the investor.
        Returns True if any field was updated.
        """
        soup = BeautifulSoup(html, "html.parser")
        updated = False

        # --- Description ---
        if not investor.description:
            desc = self._extract_description(soup, html)
            if desc:
                investor.description = desc[:2000]  # Truncate to reasonable length
                updated = True

        # --- Location ---
        if not investor.hq_location:
            location = self._extract_location(soup, html)
            if location:
                investor.hq_location = location[:200]
                updated = True

        # --- Website ---
        if not investor.website:
            website = self._extract_website(soup)
            if website:
                investor.website = website
                updated = True

        # --- Twitter ---
        if not investor.twitter:
            twitter = self._extract_twitter(soup, html)
            if twitter:
                investor.twitter = twitter[:200]
                updated = True

        # --- Investor type (only if not already set) ---
        if not investor.type:
            inv_type = self._detect_investor_type(soup, html)
            if inv_type:
                investor.type = inv_type

        return updated

    def _extract_description(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract bio/description from profile page."""
        # og:description meta tag
        meta = soup.find("meta", attrs={"property": "og:description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        # name="description" meta tag
        meta = soup.find("meta", attrs={"name": "description"})
        if meta and meta.get("content"):
            content = meta["content"].strip()
            if len(content) > 20:
                return content

        # Look for bio sections in the page
        for selector in [
            "[data-testid='bio']",
            ".bio",
            ".about-section",
            ".profile-bio",
            "[class*='biography']",
            "[class*='about']",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if len(text) > 20:
                    return text

        return None

    def _extract_location(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract location from profile page."""
        # JSON-LD structured data
        location_match = re.search(
            r'"address":\s*\{[^}]*"addressLocality":\s*"([^"]+)"', html
        )
        if location_match:
            return location_match.group(1).strip()

        # Look for location elements
        for selector in [
            "[data-testid='location']",
            ".location",
            "[class*='location']",
        ]:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) < 100:
                    return text

        return None

    def _extract_website(self, soup: BeautifulSoup) -> str | None:
        """Extract website URL from profile page."""
        # Look for external website links
        for link in soup.find_all("a", href=True):
            href = link["href"]
            text = link.get_text(strip=True).lower()

            # Skip internal wellfound links and social media
            if "wellfound.com" in href or "angellist.com" in href:
                continue
            if "twitter.com" in href or "x.com" in href or "linkedin.com" in href:
                continue
            if "facebook.com" in href or "github.com" in href:
                continue

            # Look for explicit website links
            if text in ("website", "site", "homepage", "web") or "website" in text:
                if href.startswith("http"):
                    return href

        return None

    def _extract_twitter(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract Twitter/X handle from profile page."""
        # Look for Twitter links
        for link in soup.find_all("a", href=True):
            href = link["href"]
            twitter_match = re.search(
                r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/?$", href
            )
            if twitter_match:
                handle = twitter_match.group(1)
                if handle.lower() not in ("share", "intent", "home", "search"):
                    return f"@{handle}"

        # Regex fallback in page content
        match = re.search(
            r'(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})', html
        )
        if match:
            handle = match.group(1)
            if handle.lower() not in ("share", "intent", "home", "search"):
                return f"@{handle}"

        return None

    def _detect_investor_type(self, soup: BeautifulSoup, html: str) -> str | None:
        """Detect whether this is an angel, VC, etc. from page content."""
        text_lower = html.lower()

        # Check for investor markers
        if "/people/" in html and any(
            kw in text_lower for kw in ["angel investor", "angel", "advisor", "adviser"]
        ):
            return "angel"
        if "/company/" in html and any(
            kw in text_lower
            for kw in ["venture capital", "venture fund", "investment firm", "vc firm"]
        ):
            return "vc"

        return None


class _RateLimitedError(Exception):
    """Raised when Wellfound returns 403 or 429."""
