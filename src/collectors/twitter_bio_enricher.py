"""Twitter/X bio enricher for investor profiles.

Scrapes public Twitter profiles via Nitter instances (open-source Twitter frontend)
to extract bios, follower counts, locations, and investor signals. Also discovers
Twitter handles for investors who don't have one via DuckDuckGo search.

No API key required — Nitter serves public profile data without auth.
"""

import asyncio
import logging
import re
from urllib.parse import unquote

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor
from src.models.round_investor import RoundInvestor

logger = logging.getLogger(__name__)

SOURCE_KEY = "twitter_bio"
ENRICH_BATCH_SIZE = 50
DISCOVERY_BATCH_SIZE = 20
REQUEST_DELAY = 2.0  # seconds between requests

# Nitter instances to try in order (fall back if one is down)
NITTER_INSTANCES = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.1d4.us",
]

DDG_URL = "https://html.duckduckgo.com/html/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Names that are not meaningfully searchable for twitter discovery
SKIP_NAMES = {
    "unknown", "undisclosed", "anonymous", "n/a", "na", "none", "tbd", "various",
}

# Keywords in Twitter bios that signal an investor
INVESTOR_KEYWORDS = [
    "investor", "angel", " vc ", "venture capital", "venture partner",
    "partner at", "general partner", "managing partner", "founding partner",
    "investing in", "backed", "seed", "pre-seed", "preseed",
    "fund manager", "fund of funds", "family office",
    "limited partner", "lp at", "gp at", "check writer",
    "angel investor", "angel syndicate", "syndicator",
]

# Keywords that suggest VC affiliation (vs individual angel)
VC_KEYWORDS = [
    "partner at", "general partner", "managing partner",
    "founding partner", "venture partner", "principal at",
    "venture capital", " vc ", "gp at",
]

# Keywords that suggest individual angel
ANGEL_KEYWORDS = [
    "angel investor", "angel", "check writer",
    "investing in", "backed companies", "personal investments",
]


def _normalize_handle(raw: str) -> str | None:
    """Normalize a Twitter handle: strip @, URL prefixes, whitespace."""
    if not raw:
        return None
    raw = raw.strip()
    # Handle full URLs
    match = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", raw)
    if match:
        return match.group(1).lower()
    # Handle @username or plain username
    raw = raw.lstrip("@").strip().split("/")[0].split("?")[0]
    if raw and re.match(r"^[A-Za-z0-9_]+$", raw):
        return raw.lower()
    return None


def _has_investor_signals(bio: str) -> bool:
    """Check if a bio text contains investor-related keywords."""
    bio_lower = bio.lower()
    return any(kw in bio_lower for kw in INVESTOR_KEYWORDS)


def _infer_investor_type(bio: str) -> str | None:
    """Infer investor type from bio keywords. Returns 'vc', 'angel', or None."""
    bio_lower = bio.lower()
    if any(kw in bio_lower for kw in VC_KEYWORDS):
        return "vc"
    if any(kw in bio_lower for kw in ANGEL_KEYWORDS):
        return "angel"
    return None


async def _fetch_nitter_profile(
    client: httpx.AsyncClient, handle: str
) -> dict | None:
    """Try to fetch a Twitter profile from Nitter instances.

    Returns a dict with keys: bio, location, followers, display_name
    or None if all instances fail.
    """
    for instance in NITTER_INSTANCES:
        url = f"https://{instance}/{handle}"
        try:
            resp = await client.get(url)
            if resp.status_code == 404:
                # Profile doesn't exist — no point trying other instances
                logger.debug(f"[{SOURCE_KEY}] @{handle} not found on {instance}")
                return None
            if resp.status_code != 200:
                logger.debug(
                    f"[{SOURCE_KEY}] {instance} returned {resp.status_code} for @{handle}"
                )
                continue

            return _parse_nitter_html(resp.text)
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
            logger.debug(f"[{SOURCE_KEY}] {instance} unreachable: {e}")
            continue
        except Exception as e:
            logger.debug(f"[{SOURCE_KEY}] {instance} error for @{handle}: {e}")
            continue

    return None


def _parse_nitter_html(html: str) -> dict | None:
    """Parse a Nitter profile page and extract bio, location, followers."""
    soup = BeautifulSoup(html, "html.parser")

    data: dict = {}

    # Bio text — Nitter uses .profile-bio p
    bio_el = soup.select_one(".profile-bio p")
    if bio_el:
        data["bio"] = bio_el.get_text(separator=" ").strip()

    # Display name
    name_el = soup.select_one(".profile-card-fullname")
    if name_el:
        data["display_name"] = name_el.get_text().strip()

    # Location — .profile-location
    loc_el = soup.select_one(".profile-location")
    if loc_el:
        data["location"] = loc_el.get_text().strip()

    # Follower count — look for stat items
    stat_items = soup.select(".profile-stat-num")
    stat_labels = soup.select(".profile-stat-header")
    if stat_items and stat_labels:
        for num_el, label_el in zip(stat_items, stat_labels):
            label = label_el.get_text().strip().lower()
            if "follower" in label:
                raw_count = num_el.get_text().strip().replace(",", "")
                try:
                    data["followers"] = int(raw_count)
                except ValueError:
                    # Handle abbreviated counts like "12.5K"
                    data["followers"] = _parse_abbreviated_count(raw_count)

    if not data:
        return None

    return data


def _parse_abbreviated_count(text: str) -> int | None:
    """Parse counts like '12.5K', '1.2M' into integers."""
    text = text.strip().upper()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if text.endswith(suffix):
            try:
                return int(float(text[:-1]) * mult)
            except ValueError:
                return None
    try:
        return int(text)
    except ValueError:
        return None


def _extract_twitter_handle_from_ddg(html: str) -> str | None:
    """Extract a Twitter handle from DuckDuckGo search results."""
    # Look for twitter.com or x.com links in results
    link_pattern = re.compile(
        r'href="([^"]*(?:twitter\.com|x\.com)/[A-Za-z0-9_]+[^"]*)"',
        re.IGNORECASE,
    )
    for match in link_pattern.finditer(html):
        raw_url = match.group(1)
        # Decode DDG redirect if needed
        if "uddg=" in raw_url:
            uddg_match = re.search(r"uddg=([^&]+)", raw_url)
            if uddg_match:
                raw_url = unquote(uddg_match.group(1))

        handle_match = re.search(
            r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]+)", raw_url
        )
        if handle_match:
            handle = handle_match.group(1).lower()
            # Skip common non-profile pages
            if handle in {
                "search", "explore", "home", "i", "intent",
                "share", "hashtag", "settings", "login", "signup",
            }:
                continue
            return handle

    return None


class TwitterBioEnricher(BaseEnricher):
    """Enrich investor profiles using Twitter/X bio data via Nitter."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        async with httpx.AsyncClient(
            timeout=15,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            # Phase 1: Enrich investors that already have a twitter handle
            await self._enrich_existing_handles(client, session, result)

            # Phase 2: Discover twitter handles for investors without one
            await self._discover_handles(client, session, result)

        await session.flush()

        logger.info(
            f"[{SOURCE_KEY}] Complete: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _enrich_existing_handles(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        result: EnrichmentResult,
    ) -> None:
        """Scrape Nitter profiles for investors with existing twitter handles."""
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
                Investor.twitter.isnot(None),
                Investor.twitter != "",
                Investor.source_freshness.is_(None)
                | ~Investor.source_freshness.has_key(SOURCE_KEY),
            )
            .order_by(func.coalesce(participation_count.c.deal_count, 0).desc())
            .limit(ENRICH_BATCH_SIZE)
        )

        batch_result = await session.execute(stmt)
        investors = batch_result.scalars().all()

        if not investors:
            logger.info(f"[{SOURCE_KEY}] No investors with twitter handles to enrich")
            return

        logger.info(
            f"[{SOURCE_KEY}] Enriching {len(investors)} investors with existing handles"
        )

        for investor in investors:
            handle = _normalize_handle(investor.twitter)
            if not handle:
                stamp_freshness(investor, SOURCE_KEY)
                result.records_skipped += 1
                continue

            try:
                updated = await self._scrape_profile(client, investor, handle)
                stamp_freshness(investor, SOURCE_KEY)
                if updated:
                    result.records_updated += 1
                else:
                    result.records_skipped += 1
            except Exception as e:
                result.errors.append(f"@{handle} ({investor.name}): {e}")

            await asyncio.sleep(REQUEST_DELAY)

    async def _discover_handles(
        self,
        client: httpx.AsyncClient,
        session: AsyncSession,
        result: EnrichmentResult,
    ) -> None:
        """Find Twitter handles for investors who don't have one via DuckDuckGo."""
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
                (Investor.twitter.is_(None) | (Investor.twitter == "")),
                Investor.source_freshness.is_(None)
                | ~Investor.source_freshness.has_key(f"{SOURCE_KEY}_discovery"),
            )
            .order_by(func.coalesce(participation_count.c.deal_count, 0).desc())
            .limit(DISCOVERY_BATCH_SIZE)
        )

        batch_result = await session.execute(stmt)
        investors = batch_result.scalars().all()

        if not investors:
            logger.info(f"[{SOURCE_KEY}] No investors for twitter discovery")
            return

        logger.info(
            f"[{SOURCE_KEY}] Discovering twitter handles for {len(investors)} investors"
        )

        for investor in investors:
            name = investor.name.strip()
            if len(name) < 3 or name.lower() in SKIP_NAMES:
                stamp_freshness(investor, f"{SOURCE_KEY}_discovery")
                result.records_skipped += 1
                continue

            try:
                handle = await self._search_for_handle(client, name)
                stamp_freshness(investor, f"{SOURCE_KEY}_discovery")

                if handle:
                    investor.twitter = f"@{handle}"
                    flag_modified(investor, "twitter")
                    logger.info(f"[{SOURCE_KEY}] Discovered @{handle} for {name}")

                    # Now scrape the discovered profile
                    updated = await self._scrape_profile(client, investor, handle)
                    stamp_freshness(investor, SOURCE_KEY)
                    if updated:
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1

                    await asyncio.sleep(REQUEST_DELAY)
                else:
                    result.records_skipped += 1

            except httpx.HTTPStatusError as e:
                if e.response.status_code in (429, 403):
                    logger.warning(
                        f"[{SOURCE_KEY}] Rate limited during discovery, stopping"
                    )
                    result.errors.append(f"Discovery rate limited: {e.response.status_code}")
                    break
                result.errors.append(f"Discovery {name}: HTTP {e.response.status_code}")
            except Exception as e:
                result.errors.append(f"Discovery {name}: {e}")

            await asyncio.sleep(REQUEST_DELAY)

    async def _search_for_handle(
        self, client: httpx.AsyncClient, investor_name: str
    ) -> str | None:
        """Search DuckDuckGo for an investor's Twitter handle."""
        query = f'site:twitter.com "{investor_name}" investor'
        resp = await client.get(DDG_URL, params={"q": query})

        if resp.status_code in (429, 403):
            resp.raise_for_status()

        if resp.status_code != 200:
            return None

        return _extract_twitter_handle_from_ddg(resp.text)

    async def _scrape_profile(
        self,
        client: httpx.AsyncClient,
        investor: Investor,
        handle: str,
    ) -> bool:
        """Scrape a Nitter profile and update investor fields."""
        profile = await _fetch_nitter_profile(client, handle)
        if not profile:
            return False

        updated = False
        bio = profile.get("bio", "")

        # Update description from bio if empty
        if not investor.description and bio:
            investor.description = bio
            updated = True

        # Update location if empty
        if not investor.hq_location and profile.get("location"):
            investor.hq_location = profile["location"]
            updated = True

        # Store follower count in source_freshness
        if profile.get("followers") is not None:
            freshness = investor.source_freshness or {}
            freshness["twitter_followers"] = profile["followers"]
            investor.source_freshness = freshness
            flag_modified(investor, "source_freshness")
            updated = True

        # Infer investor type from bio if not already set
        if not investor.type and bio and _has_investor_signals(bio):
            inferred = _infer_investor_type(bio)
            if inferred:
                investor.type = inferred
                logger.info(
                    f"[{SOURCE_KEY}] Inferred type={inferred} for {investor.name} "
                    f"from bio: {bio[:80]}"
                )
                updated = True

        if updated:
            from datetime import datetime, timezone
            investor.last_enriched_at = datetime.now(timezone.utc)

        return updated
