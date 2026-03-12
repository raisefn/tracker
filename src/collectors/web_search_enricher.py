"""Web search enricher for investor profiles.

Uses DuckDuckGo HTML search (no API key) to find investor websites,
social links, descriptions, and locations. Prioritizes investors with
the most round participations (highest value to enrich first).
"""

import asyncio
import logging
import re
from urllib.parse import unquote, urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.models.round_investor import RoundInvestor

logger = logging.getLogger(__name__)

SOURCE_KEY = "web_search"
BATCH_SIZE = 50
REQUEST_DELAY = 2.0  # seconds between requests to DuckDuckGo

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Names that are not meaningfully searchable
SKIP_NAMES = {"unknown", "undisclosed", "anonymous", "n/a", "na", "none", "tbd", "various"}

# Social media domains to exclude when looking for a company/fund website
SOCIAL_DOMAINS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "reddit.com", "medium.com", "github.com",
    "crunchbase.com", "pitchbook.com", "angel.co", "wellfound.com",
    "google.com", "wikipedia.org", "duckduckgo.com", "bing.com",
    "bloomberg.com", "forbes.com", "techcrunch.com", "sec.gov",
}


def _is_searchable(name: str) -> bool:
    """Return False for names that are too short or generic to search."""
    cleaned = name.strip()
    if len(cleaned) < 3:
        return False
    if cleaned.lower() in SKIP_NAMES:
        return False
    # Single generic word
    if len(cleaned.split()) == 1 and cleaned.lower() in {
        "investor", "fund", "capital", "ventures", "partners", "group",
        "holdings", "management", "advisors", "associates",
    }:
        return False
    return True


def _extract_urls_from_ddg_html(html: str) -> list[tuple[str, str]]:
    """Extract (url, snippet) pairs from DuckDuckGo HTML search results.

    DuckDuckGo HTML results have links in <a class="result__a"> and
    snippets in <a class="result__snippet">.
    """
    results: list[tuple[str, str]] = []

    # DuckDuckGo wraps result URLs in redirect links — extract the actual URL
    # Pattern: <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=ENCODED_URL&...">
    # Or direct links: href="https://..."
    link_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (raw_url, _title) in enumerate(links):
        # Decode DDG redirect URL
        url = _decode_ddg_url(raw_url)
        if not url:
            continue

        snippet = ""
        if i < len(snippets):
            # Strip HTML tags from snippet
            snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

        results.append((url, snippet))

    return results


def _decode_ddg_url(raw_url: str) -> str | None:
    """Decode a DuckDuckGo redirect URL to the actual target URL."""
    if "uddg=" in raw_url:
        match = re.search(r"uddg=([^&]+)", raw_url)
        if match:
            return unquote(match.group(1))
    # Direct URL
    if raw_url.startswith("http"):
        return raw_url
    if raw_url.startswith("//"):
        return "https:" + raw_url
    return None


def _extract_domain(url: str) -> str:
    """Extract the root domain from a URL."""
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        # Remove www. prefix
        if host.startswith("www."):
            host = host[4:]
        return host.lower()
    except Exception:
        return ""


def _find_website(results: list[tuple[str, str]]) -> str | None:
    """Find the first result URL that looks like a company/fund website."""
    for url, _snippet in results:
        domain = _extract_domain(url)
        if not domain:
            continue
        # Skip known social/news/aggregator domains
        if any(domain == sd or domain.endswith("." + sd) for sd in SOCIAL_DOMAINS):
            continue
        # Skip results that are clearly not a fund/company homepage
        if any(skip in domain for skip in ["stackexchange", "quora", "yelp"]):
            continue
        return url
    return None


def _find_linkedin(results: list[tuple[str, str]]) -> str | None:
    """Find a LinkedIn profile or company URL from results."""
    for url, _snippet in results:
        domain = _extract_domain(url)
        if domain == "linkedin.com" or domain.endswith(".linkedin.com"):
            if "/in/" in url or "/company/" in url:
                # Clean tracking params
                clean = url.split("?")[0]
                return clean
    return None


def _find_twitter(results: list[tuple[str, str]]) -> str | None:
    """Find a Twitter/X handle from results."""
    for url, _snippet in results:
        domain = _extract_domain(url)
        if domain in ("twitter.com", "x.com"):
            # Extract handle from URL like twitter.com/handle
            match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", url)
            if match:
                handle = match.group(1).lower()
                # Skip common non-profile pages
                if handle in {"search", "explore", "home", "i", "intent", "share", "hashtag"}:
                    continue
                return f"@{handle}"
    return None


def _find_description(results: list[tuple[str, str]], investor_name: str) -> str | None:
    """Extract the best snippet that describes the investor."""
    name_lower = investor_name.lower()
    # Prefer snippets that mention the investor name
    for _url, snippet in results:
        if not snippet or len(snippet) < 30:
            continue
        if name_lower in snippet.lower() or any(
            word in snippet.lower() for word in name_lower.split()[:2]
        ):
            # Truncate overly long snippets
            if len(snippet) > 500:
                snippet = snippet[:497] + "..."
            return snippet
    # Fallback: first decent snippet
    for _url, snippet in results:
        if snippet and len(snippet) >= 30:
            if len(snippet) > 500:
                snippet = snippet[:497] + "..."
            return snippet
    return None


def _find_location(results: list[tuple[str, str]]) -> str | None:
    """Try to extract a location from snippets (e.g. 'based in San Francisco')."""
    location_patterns = [
        re.compile(r"(?:based|headquartered|located)\s+in\s+([A-Z][A-Za-z\s,]+?)(?:\.|,\s*(?:is|was|and|with|focused))", re.IGNORECASE),
        re.compile(r"(?:based|headquartered|located)\s+in\s+([A-Z][A-Za-z\s,]+?)$", re.IGNORECASE),
    ]
    for _url, snippet in results:
        if not snippet:
            continue
        for pattern in location_patterns:
            match = pattern.search(snippet)
            if match:
                location = match.group(1).strip().rstrip(",.")
                # Sanity check: should be short and look like a place name
                if 3 <= len(location) <= 80:
                    return location
    return None


class WebSearchEnricher(BaseEnricher):
    """Enrich investor profiles by searching the web via DuckDuckGo."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Find investors not yet web-searched, ordered by round participation count
        # (most valuable investors first)
        participation_count = (
            select(
                RoundInvestor.investor_id,
                func.count().label("deal_count"),
            )
            .group_by(RoundInvestor.investor_id)
            .subquery()
        )

        stmt = (
            select(Investor)
            .outerjoin(participation_count, Investor.id == participation_count.c.investor_id)
            .where(
                Investor.source_freshness.is_(None)
                | ~Investor.source_freshness.has_key(SOURCE_KEY)
            )
            .order_by(func.coalesce(participation_count.c.deal_count, 0).desc())
            .limit(BATCH_SIZE)
        )

        batch_result = await session.execute(stmt)
        investors = batch_result.scalars().all()

        if not investors:
            logger.info(f"[{SOURCE_KEY}] No investors to enrich")
            return result

        logger.info(f"[{SOURCE_KEY}] Processing batch of {len(investors)} investors")

        async with httpx.AsyncClient(
            timeout=15,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            for investor in investors:
                if not _is_searchable(investor.name):
                    # Stamp freshness so we don't re-process unsearchable names
                    stamp_freshness(investor, SOURCE_KEY)
                    result.records_skipped += 1
                    continue

                try:
                    updated = await self._search_investor(client, investor)
                    stamp_freshness(investor, SOURCE_KEY)
                    if updated:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (429, 403):
                        logger.warning(
                            f"[{SOURCE_KEY}] Rate limited ({e.response.status_code}), "
                            f"stopping run after {result.records_updated} updates"
                        )
                        result.errors.append(f"Rate limited: {e.response.status_code}")
                        break
                    result.errors.append(f"{investor.name}: HTTP {e.response.status_code}")
                except Exception as e:
                    result.errors.append(f"{investor.name}: {e}")

                # Rate limit: 1 request per 2 seconds
                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()

        logger.info(
            f"[{SOURCE_KEY}] Complete: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _search_investor(
        self, client: httpx.AsyncClient, investor: Investor
    ) -> bool:
        """Search DuckDuckGo for an investor and extract profile data."""
        query = f'"{investor.name}" investor'
        resp = await client.get(DDG_URL, params={"q": query})

        # Raise on 429/403 so we can catch and stop the run
        if resp.status_code in (429, 403):
            resp.raise_for_status()

        if resp.status_code != 200:
            logger.debug(f"[{SOURCE_KEY}] {investor.name}: HTTP {resp.status_code}")
            return False

        search_results = _extract_urls_from_ddg_html(resp.text)
        if not search_results:
            return False

        updated = False

        # Website — only set if currently empty
        if not investor.website:
            website = _find_website(search_results)
            if website:
                investor.website = website
                updated = True

        # Twitter — only set if currently empty
        if not investor.twitter:
            twitter = _find_twitter(search_results)
            if twitter:
                investor.twitter = twitter
                updated = True

        # Description — only set if currently empty
        if not investor.description:
            description = _find_description(search_results, investor.name)
            if description:
                investor.description = description
                updated = True

        # HQ location — only set if currently empty
        if not investor.hq_location:
            location = _find_location(search_results)
            if location:
                investor.hq_location = location
                updated = True

        # LinkedIn — store in source_freshness since there's no dedicated column
        linkedin = _find_linkedin(search_results)
        if linkedin:
            freshness = investor.source_freshness or {}
            if "linkedin_url" not in freshness:
                freshness["linkedin_url"] = linkedin
                investor.source_freshness = freshness
                updated = True

        if updated:
            from datetime import datetime, timezone
            investor.last_enriched_at = datetime.now(timezone.utc)

        return updated
