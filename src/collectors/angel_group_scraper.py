"""Angel group directory scraper.

Discovers and enriches early-stage angel investor groups from:
1. Angel Capital Association (ACA) member directory
2. Gust angel group directory
3. Individual angel group websites (top ~30 most active groups)

This is both a COLLECTOR (discovers new angel groups as Investor records)
and an ENRICHER (updates existing ones with fresh data).

Each angel group becomes an Investor record with type="angel" and
investor_category="angel_group". Extra metadata (member count, portfolio
companies, investment focus) is stored in source_freshness JSON.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, Tag
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import (
    BaseEnricher,
    EnrichmentResult,
    find_investor_match,
    stamp_freshness,
)
from src.models import Investor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "angel_groups"
REQUEST_DELAY = 2  # seconds between requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Top ~30 most active US angel groups with known public-facing sites
TOP_ANGEL_GROUPS: list[dict] = [
    {
        "name": "Tech Coast Angels",
        "website": "https://www.techcoastangels.com",
        "location": "Los Angeles, CA",
    },
    {
        "name": "Golden Seeds",
        "website": "https://www.goldenseeds.com",
        "location": "New York, NY",
    },
    {
        "name": "New York Angels",
        "website": "https://www.newyorkangels.com",
        "location": "New York, NY",
    },
    {
        "name": "Keiretsu Forum",
        "website": "https://www.keiretsuforum.com",
        "location": "San Francisco, CA",
    },
    {
        "name": "Band of Angels",
        "website": "https://www.bandofangels.com",
        "location": "Menlo Park, CA",
    },
    {
        "name": "Launchpad Venture Group",
        "website": "https://www.launchpadventuregroup.com",
        "location": "Boston, MA",
    },
    {
        "name": "Hyde Park Angels",
        "website": "https://www.hydeparkangels.com",
        "location": "Chicago, IL",
    },
    {
        "name": "Sand Hill Angels",
        "website": "https://www.sandhillangels.com",
        "location": "Menlo Park, CA",
    },
    {
        "name": "Houston Angel Network",
        "website": "https://www.houstonangelnetwork.com",
        "location": "Houston, TX",
    },
    {
        "name": "Central Texas Angel Network",
        "website": "https://www.centraltexasangelnetwork.com",
        "location": "Austin, TX",
    },
    {
        "name": "Seattle Angel Group",
        "website": "https://www.seattleangelgroup.com",
        "location": "Seattle, WA",
    },
    {
        "name": "Arizona Technology Investors",
        "website": "https://www.aztechinvestors.com",
        "location": "Scottsdale, AZ",
    },
    {
        "name": "Angel Resource Institute",
        "website": "https://www.angelresourceinstitute.org",
        "location": "Overland Park, KS",
    },
    {
        "name": "Robin Hood Ventures",
        "website": "https://www.robinhoodventures.com",
        "location": "Philadelphia, PA",
    },
    {
        "name": "Desert Angels",
        "website": "https://www.desertangels.com",
        "location": "Tucson, AZ",
    },
    {
        "name": "Investors Circle",
        "website": "https://www.investorscircle.net",
        "location": "San Francisco, CA",
    },
    {
        "name": "Pasadena Angels",
        "website": "https://www.pasadenaangels.com",
        "location": "Pasadena, CA",
    },
    {
        "name": "North Coast Angel Fund",
        "website": "https://www.northcoastangelfund.com",
        "location": "Cleveland, OH",
    },
    {
        "name": "BlueTree Allied Angels",
        "website": "https://www.bluetreealliedangels.com",
        "location": "Pittsburgh, PA",
    },
    {
        "name": "Sacramento Angels",
        "website": "https://www.sacangels.com",
        "location": "Sacramento, CA",
    },
    {
        "name": "Atlanta Technology Angels",
        "website": "https://www.angelatlanta.com",
        "location": "Atlanta, GA",
    },
    {
        "name": "Maine Angels",
        "website": "https://www.maineangels.org",
        "location": "Portland, ME",
    },
    {
        "name": "Rockies Venture Club",
        "website": "https://www.rockiesventureclub.org",
        "location": "Denver, CO",
    },
    {
        "name": "Triangle Angel Partners",
        "website": "https://www.triangleangelpartners.com",
        "location": "Durham, NC",
    },
    {
        "name": "Mid-Atlantic Angel Group",
        "website": "https://www.midatlanticangelgroup.com",
        "location": "Washington, DC",
    },
    {
        "name": "New World Angels",
        "website": "https://www.newworldangels.com",
        "location": "Boca Raton, FL",
    },
    {
        "name": "Oregon Angel Fund",
        "website": "https://www.oregonangelfund.com",
        "location": "Portland, OR",
    },
    {
        "name": "Harvard Business School Angels of New York",
        "website": "https://www.hbsangelsny.com",
        "location": "New York, NY",
    },
    {
        "name": "Astia Angels",
        "website": "https://www.astia.org",
        "location": "San Francisco, CA",
    },
    {
        "name": "Pipeline Angels",
        "website": "https://www.pipelineangels.com",
        "location": "New York, NY",
    },
]


class _RateLimitedError(Exception):
    """Raised on 403/429 to stop the current source gracefully."""


class AngelGroupScraper(BaseEnricher):
    """Discover and enrich angel investor groups from public directories."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # Phase 1: ACA directory (canonical list)
            await self._scrape_aca_directory(client, session, result)

            # Phase 2: Gust directory
            await self._scrape_gust_directory(client, session, result)

            # Phase 3: Hardcoded top angel groups (ensure they exist + enrich)
            await self._process_top_groups(client, session, result)

        await session.flush()
        logger.info(
            f"[angel_groups] Done: {result.records_updated} updated/created, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    # ------------------------------------------------------------------
    # ACA directory
    # ------------------------------------------------------------------

    async def _scrape_aca_directory(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        result: EnrichmentResult,
    ) -> None:
        """Scrape the Angel Capital Association member directory."""
        url = "https://www.angelcapitalassociation.org/directory/"
        logger.info(f"[angel_groups] Scraping ACA directory: {url}")

        html = await self._fetch(client, url)
        if html is None:
            result.errors.append("ACA directory: failed to fetch")
            return

        soup = BeautifulSoup(html, "html.parser")
        groups = self._parse_aca_entries(soup)
        logger.info(f"[angel_groups] ACA: found {len(groups)} entries")

        for group_data in groups:
            try:
                await self._upsert_angel_group(session, group_data, "aca_directory", result)
            except Exception as e:
                result.errors.append(f"ACA/{group_data.get('name', '?')}: {e}")

        await asyncio.sleep(REQUEST_DELAY)

    def _parse_aca_entries(self, soup: BeautifulSoup) -> list[dict]:
        """Parse angel group entries from ACA directory HTML.

        The ACA directory has gone through several redesigns, so we try
        multiple selector strategies to maximize extraction.
        """
        groups: list[dict] = []

        # Strategy 1: Look for directory listing items (common patterns)
        for selector in [
            "div.directory-listing",
            "div.member-listing",
            "div.directory-item",
            "article.member",
            "li.directory-entry",
            "div.entry-content",
            "table.directory tbody tr",
        ]:
            entries = soup.select(selector)
            if entries:
                for entry in entries:
                    group = self._extract_aca_entry(entry)
                    if group:
                        groups.append(group)
                if groups:
                    return groups

        # Strategy 2: Look for headings with links (common directory pattern)
        for heading_tag in ["h3", "h4", "h2", "strong"]:
            headings = soup.find_all(heading_tag)
            for heading in headings:
                link = heading.find("a", href=True) if isinstance(heading, Tag) else None
                name = heading.get_text(strip=True) if isinstance(heading, Tag) else None
                if name and len(name) > 3 and len(name) < 200:
                    group: dict = {"name": name}
                    if link:
                        href = link.get("href", "")
                        if isinstance(href, str) and href.startswith("http"):
                            group["website"] = href
                    # Try to find location in sibling or parent text
                    parent = heading.parent if isinstance(heading, Tag) else None
                    if isinstance(parent, Tag):
                        parent_text = parent.get_text(strip=True)
                        loc = self._extract_location_from_text(parent_text)
                        if loc:
                            group["location"] = loc
                    groups.append(group)

        # Strategy 3: Generic link harvesting from the page body
        if not groups:
            for link in soup.find_all("a", href=True):
                href = link.get("href", "")
                text = link.get_text(strip=True)
                if (
                    isinstance(href, str)
                    and href.startswith("http")
                    and "angelcapitalassociation" not in href
                    and text
                    and len(text) > 4
                    and len(text) < 200
                    and any(
                        kw in text.lower()
                        for kw in [
                            "angel",
                            "investor",
                            "venture",
                            "fund",
                            "network",
                            "capital",
                        ]
                    )
                ):
                    groups.append({"name": text, "website": href})

        return groups

    def _extract_aca_entry(self, entry: Tag) -> dict | None:
        """Extract name, website, location, and description from a single ACA entry."""
        # Name: first heading or strong
        name_el = entry.find(["h2", "h3", "h4", "strong", "a"])
        if not name_el:
            return None
        name = name_el.get_text(strip=True)
        if not name or len(name) < 3:
            return None

        group: dict = {"name": name}

        # Website: first external link
        link = entry.find("a", href=True)
        if link:
            href = link.get("href", "")
            if isinstance(href, str) and href.startswith("http"):
                group["website"] = href

        # Location
        text = entry.get_text(" ", strip=True)
        loc = self._extract_location_from_text(text)
        if loc:
            group["location"] = loc

        # Description: any paragraph text
        para = entry.find("p")
        if isinstance(para, Tag):
            desc = para.get_text(strip=True)
            if desc and len(desc) > 20:
                group["description"] = desc[:2000]

        return group

    # ------------------------------------------------------------------
    # Gust directory
    # ------------------------------------------------------------------

    async def _scrape_gust_directory(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        result: EnrichmentResult,
    ) -> None:
        """Scrape the Gust angel groups directory."""
        url = "https://gust.com/angel-groups"
        logger.info(f"[angel_groups] Scraping Gust directory: {url}")

        html = await self._fetch(client, url)
        if html is None:
            result.errors.append("Gust directory: failed to fetch")
            return

        soup = BeautifulSoup(html, "html.parser")
        groups = self._parse_gust_entries(soup)
        logger.info(f"[angel_groups] Gust: found {len(groups)} entries")

        for group_data in groups:
            try:
                await self._upsert_angel_group(session, group_data, "gust_directory", result)
            except Exception as e:
                result.errors.append(f"Gust/{group_data.get('name', '?')}: {e}")

        await asyncio.sleep(REQUEST_DELAY)

    def _parse_gust_entries(self, soup: BeautifulSoup) -> list[dict]:
        """Parse angel group entries from Gust directory HTML."""
        groups: list[dict] = []

        # Gust typically uses card-based layouts
        for selector in [
            "div.angel-group",
            "div.group-card",
            "div.organization-card",
            "a.group-listing",
            "li.group-item",
            "div.card",
        ]:
            entries = soup.select(selector)
            if entries:
                for entry in entries:
                    group = self._extract_gust_entry(entry)
                    if group:
                        groups.append(group)
                if groups:
                    return groups

        # Fallback: look for structured listings with links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if (
                isinstance(href, str)
                and "/angel-groups/" in href
                and text
                and len(text) > 3
                and len(text) < 200
            ):
                group: dict = {"name": text}
                # Build full URL if relative
                if href.startswith("/"):
                    group["gust_url"] = f"https://gust.com{href}"
                elif href.startswith("http"):
                    group["gust_url"] = href
                groups.append(group)

        return groups

    def _extract_gust_entry(self, entry: Tag) -> dict | None:
        """Extract group data from a Gust card element."""
        name_el = entry.find(["h2", "h3", "h4", "strong", "a"])
        if not name_el:
            return None
        name = name_el.get_text(strip=True)
        if not name or len(name) < 3:
            return None

        group: dict = {"name": name}

        # Website link
        link = entry.find("a", href=True)
        if link:
            href = link.get("href", "")
            if isinstance(href, str) and href.startswith("http") and "gust.com" not in href:
                group["website"] = href

        # Location
        text = entry.get_text(" ", strip=True)
        loc = self._extract_location_from_text(text)
        if loc:
            group["location"] = loc

        # Description
        para = entry.find("p")
        if isinstance(para, Tag):
            desc = para.get_text(strip=True)
            if desc and len(desc) > 20:
                group["description"] = desc[:2000]

        return group

    # ------------------------------------------------------------------
    # Top angel groups (hardcoded list + website scraping)
    # ------------------------------------------------------------------

    async def _process_top_groups(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        result: EnrichmentResult,
    ) -> None:
        """Ensure top angel groups exist and enrich from their websites."""
        logger.info(f"[angel_groups] Processing {len(TOP_ANGEL_GROUPS)} top angel groups")

        for group_info in TOP_ANGEL_GROUPS:
            try:
                # First ensure the group exists as an Investor record
                await self._upsert_angel_group(session, group_info, "top_groups", result)

                # Then try to scrape their website for extra data
                website = group_info.get("website")
                if website:
                    await asyncio.sleep(REQUEST_DELAY)
                    await self._enrich_from_website(client, session, group_info, result)

            except _RateLimitedError:
                logger.warning("[angel_groups] Rate limited on top group sites, stopping")
                result.errors.append("Rate limited on angel group websites")
                break
            except Exception as e:
                result.errors.append(f"TopGroup/{group_info['name']}: {e}")

    async def _enrich_from_website(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        group_info: dict,
        result: EnrichmentResult,
    ) -> None:
        """Scrape an angel group's website for additional metadata."""
        website = group_info.get("website", "")
        if not website:
            return

        html = await self._fetch(client, website)
        if html is None:
            return

        soup = BeautifulSoup(html, "html.parser")
        investor = await find_investor_match(session, group_info["name"])
        if not investor:
            return

        updated = False
        extra_meta = investor.source_freshness or {}
        angel_meta = extra_meta.get("angel_group_meta", {})

        # Extract description from meta tags if not already set
        if not investor.description:
            desc = self._extract_meta_description(soup)
            if desc:
                investor.description = desc[:2000]
                updated = True

        # Extract member count from page text
        member_count = self._extract_member_count(soup)
        if member_count and angel_meta.get("member_count") != member_count:
            angel_meta["member_count"] = member_count
            updated = True

        # Extract investment focus / sectors
        focus = self._extract_investment_focus(soup)
        if focus and angel_meta.get("investment_focus") != focus:
            angel_meta["investment_focus"] = focus
            updated = True

        # Extract portfolio companies
        portfolio = self._extract_portfolio_companies(soup)
        if portfolio and angel_meta.get("portfolio_companies") != portfolio:
            angel_meta["portfolio_companies"] = portfolio
            updated = True

        # Extract Twitter handle
        if not investor.twitter:
            twitter = self._extract_twitter(soup, html)
            if twitter:
                investor.twitter = twitter[:200]
                updated = True

        if updated:
            extra_meta["angel_group_meta"] = angel_meta
            investor.source_freshness = extra_meta
            stamp_freshness(investor, self.source_name())
            investor.last_enriched_at = datetime.now(timezone.utc)

    # ------------------------------------------------------------------
    # Upsert logic
    # ------------------------------------------------------------------

    async def _upsert_angel_group(
        self,
        session: AsyncSession,
        group_data: dict,
        sub_source: str,
        result: EnrichmentResult,
    ) -> Investor | None:
        """Create or update an Investor record for an angel group.

        Returns the investor if created/updated, None if skipped.
        """
        name = group_data.get("name", "").strip()
        if not name or len(name) < 3:
            result.records_skipped += 1
            return None

        # Check for existing match
        investor = await find_investor_match(session, name)

        if investor:
            # Update fields that are currently empty
            updated = False

            if not investor.website and group_data.get("website"):
                investor.website = group_data["website"]
                updated = True

            if not investor.hq_location and group_data.get("location"):
                investor.hq_location = group_data["location"][:200]
                updated = True

            if not investor.description and group_data.get("description"):
                investor.description = group_data["description"][:2000]
                updated = True

            # Always ensure type and category are correct
            if not investor.type:
                investor.type = "angel"
                updated = True
            if not investor.investor_category:
                investor.investor_category = "angel_group"
                updated = True

            if updated:
                stamp_freshness(investor, self.source_name())
                investor.last_enriched_at = datetime.now(timezone.utc)
                result.records_updated += 1
            else:
                result.records_skipped += 1

            return investor

        # No match found — create new investor record (genuine discovery)
        slug = make_slug(name)
        investor = Investor(
            name=name,
            slug=slug,
            type="angel",
            investor_category="angel_group",
            website=group_data.get("website"),
            hq_location=(group_data.get("location", "") or "")[:200] or None,
            description=(group_data.get("description", "") or "")[:2000] or None,
        )
        stamp_freshness(investor, self.source_name())
        investor.last_enriched_at = datetime.now(timezone.utc)

        # Store sub-source info in freshness metadata
        freshness = investor.source_freshness or {}
        freshness["discovered_via"] = sub_source
        investor.source_freshness = freshness

        session.add(investor)
        result.records_updated += 1
        logger.debug(f"[angel_groups] Discovered new angel group: {name}")
        return investor

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    async def _fetch(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Fetch a URL, returning HTML on success or None on failure.

        Raises _RateLimitedError on 403/429.
        """
        try:
            resp = await client.get(url)
        except httpx.HTTPError as e:
            logger.debug(f"[angel_groups] HTTP error fetching {url}: {e}")
            return None

        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code >= 400:
            logger.debug(f"[angel_groups] {resp.status_code} for {url}")
            return None

        return resp.text

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_location_from_text(self, text: str) -> str | None:
        """Try to extract a US city/state location from free text."""
        # Match patterns like "City, ST" or "City, State"
        us_states = (
            "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
            "MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
        )
        match = re.search(
            rf"([A-Z][a-zA-Z\s.-]+),\s*({us_states})\b",
            text,
        )
        if match:
            city = match.group(1).strip()
            state = match.group(2).strip()
            if len(city) < 50:
                return f"{city}, {state}"
        return None

    def _extract_meta_description(self, soup: BeautifulSoup) -> str | None:
        """Extract description from HTML meta tags."""
        for attr in [
            {"property": "og:description"},
            {"name": "description"},
        ]:
            meta = soup.find("meta", attrs=attr)
            if meta and meta.get("content"):
                content = meta["content"].strip()
                if len(content) > 20:
                    return content
        return None

    def _extract_member_count(self, soup: BeautifulSoup) -> int | None:
        """Try to find member count from page text."""
        text = soup.get_text(" ", strip=True)
        # Match patterns like "150 members", "150+ members", "over 300 members"
        patterns = [
            r"(\d{1,4})\+?\s*(?:active\s+)?members",
            r"over\s+(\d{1,4})\s+(?:active\s+)?members",
            r"(\d{1,4})\s+(?:active\s+)?angel\s+investors",
            r"(\d{1,4})\+?\s+investors",
            r"membership\s+of\s+(\d{1,4})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    count = int(match.group(1))
                    if 5 <= count <= 5000:  # sanity bounds
                        return count
                except ValueError:
                    continue
        return None

    def _extract_investment_focus(self, soup: BeautifulSoup) -> list[str] | None:
        """Extract investment focus areas from page text."""
        text = soup.get_text(" ", strip=True).lower()
        focus_keywords = [
            "technology",
            "software",
            "saas",
            "healthcare",
            "healthtech",
            "biotech",
            "fintech",
            "cleantech",
            "climate",
            "ai",
            "artificial intelligence",
            "machine learning",
            "consumer",
            "enterprise",
            "hardware",
            "iot",
            "medtech",
            "edtech",
            "cybersecurity",
            "deep tech",
            "life sciences",
            "digital health",
            "agtech",
            "foodtech",
            "proptech",
            "robotics",
            "blockchain",
        ]
        found = [kw for kw in focus_keywords if kw in text]
        return found[:10] if found else None

    def _extract_portfolio_companies(self, soup: BeautifulSoup) -> list[str] | None:
        """Try to extract portfolio company names from a portfolio section."""
        portfolio: list[str] = []

        # Look for portfolio sections
        for selector in [
            "section.portfolio",
            "div.portfolio",
            "[class*='portfolio']",
            "#portfolio",
        ]:
            section = soup.select_one(selector)
            if section:
                # Extract company names from links or headings within
                for el in section.find_all(["a", "h3", "h4", "strong"]):
                    name = el.get_text(strip=True)
                    if name and 2 < len(name) < 100:
                        portfolio.append(name)

        # Fallback: look for text near "portfolio" heading
        if not portfolio:
            for heading in soup.find_all(["h2", "h3", "h4"]):
                if isinstance(heading, Tag) and "portfolio" in heading.get_text(strip=True).lower():
                    # Get siblings after the heading
                    sibling = heading.find_next_sibling()
                    if isinstance(sibling, Tag):
                        for el in sibling.find_all(["a", "li", "strong"]):
                            name = el.get_text(strip=True)
                            if name and 2 < len(name) < 100:
                                portfolio.append(name)
                    break

        # Dedupe and limit
        seen: set[str] = set()
        unique: list[str] = []
        for name in portfolio:
            if name.lower() not in seen:
                seen.add(name.lower())
                unique.append(name)

        return unique[:50] if unique else None

    def _extract_twitter(self, soup: BeautifulSoup, html: str) -> str | None:
        """Extract Twitter/X handle from page."""
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            if isinstance(href, str):
                twitter_match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)/?$", href)
                if twitter_match:
                    handle = twitter_match.group(1)
                    if handle.lower() not in (
                        "share",
                        "intent",
                        "home",
                        "search",
                        "login",
                        "signup",
                    ):
                        return f"@{handle}"

        match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})", html)
        if match:
            handle = match.group(1)
            if handle.lower() not in ("share", "intent", "home", "search", "login", "signup"):
                return f"@{handle}"

        return None
