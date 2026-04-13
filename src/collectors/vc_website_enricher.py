"""VC website enricher — scrapes firm websites for team, portfolio, and thesis data.

Fetches VC firm homepages, discovers subpages (team, portfolio, about), and
extracts structured data: partner profiles, portfolio companies, investment
thesis, check sizes, and location. Only updates NULL/empty fields.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Investor, Project
from src.models.round_investor import RoundInvestor
from src.pipeline.normalizer import make_slug

logger = logging.getLogger(__name__)

SOURCE_KEY = "vc_website"
BATCH_SIZE = 30
REQUEST_DELAY = 2.0  # seconds between requests

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Patterns for discovering subpages from nav links
TEAM_PATTERNS = re.compile(
    r"(?:^|/)(?:team|people|about(?:-us)?|who-we-are|our-team|partners|leadership)/?$",
    re.IGNORECASE,
)
PORTFOLIO_PATTERNS = re.compile(
    r"(?:^|/)(?:portfolio|companies|investments|our-portfolio|our-companies)/?$",
    re.IGNORECASE,
)
ABOUT_PATTERNS = re.compile(
    r"(?:^|/)(?:about(?:-us)?|who-we-are|our-story|thesis|philosophy|approach|focus|strategy)/?$",
    re.IGNORECASE,
)

# Regex for check size extraction
CHECK_SIZE_RE = re.compile(
    r"\$[\d,.]+[KkMm]?\s*[-–—to]+\s*\$[\d,.]+[KkMm]?",
    re.IGNORECASE,
)

# Stage keywords
STAGE_KEYWORDS = [
    "pre-seed", "preseed", "seed", "series a", "series b", "series c",
    "early stage", "early-stage", "growth stage", "growth-stage",
    "late stage", "late-stage", "venture", "angel",
]

# Sector keywords
SECTOR_KEYWORDS = [
    "fintech", "healthtech", "health tech", "biotech", "edtech",
    "climate", "cleantech", "clean tech", "saas", "enterprise",
    "consumer", "marketplace", "crypto", "web3", "blockchain",
    "defi", "ai", "artificial intelligence", "machine learning",
    "robotics", "cybersecurity", "security", "infrastructure",
    "developer tools", "dev tools", "deep tech", "hardware",
    "gaming", "media", "commerce", "e-commerce",
]

# Location patterns
LOCATION_RE = re.compile(
    r"(?:based\s+in|headquartered\s+in|located\s+in|offices?\s+in)\s+"
    r"([A-Z][A-Za-z\s,]+?)(?:\.|,\s*(?:is|was|and|with|focused|we)|$)",
    re.IGNORECASE,
)


def _same_domain(base_url: str, candidate_url: str) -> bool:
    """Check if candidate URL is on the same domain as the base."""
    try:
        base_host = urlparse(base_url).hostname or ""
        cand_host = urlparse(candidate_url).hostname or ""
        # Strip www. for comparison
        base_host = base_host.removeprefix("www.")
        cand_host = cand_host.removeprefix("www.")
        return base_host == cand_host
    except Exception:
        return False


def _discover_subpages(
    base_url: str, soup: BeautifulSoup
) -> dict[str, str | None]:
    """Scan nav links for team, portfolio, and about pages."""
    found: dict[str, str | None] = {"team": None, "portfolio": None, "about": None}

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        full_url = urljoin(base_url, href)

        if not _same_domain(base_url, full_url):
            continue

        path = urlparse(full_url).path.rstrip("/") + "/"
        link_text = a_tag.get_text(strip=True).lower()

        # Match by URL path
        if not found["team"] and TEAM_PATTERNS.search(path):
            found["team"] = full_url
        elif not found["portfolio"] and PORTFOLIO_PATTERNS.search(path):
            found["portfolio"] = full_url
        elif not found["about"] and ABOUT_PATTERNS.search(path):
            found["about"] = full_url

        # Match by link text as fallback
        if not found["team"] and link_text in (
            "team", "people", "our team", "who we are", "partners", "leadership",
        ):
            found["team"] = full_url
        elif not found["portfolio"] and link_text in (
            "portfolio", "companies", "investments", "our portfolio",
        ):
            found["portfolio"] = full_url
        elif not found["about"] and link_text in (
            "about", "about us", "our story", "thesis", "approach", "focus",
        ):
            found["about"] = full_url

    return found


def _extract_team_members(soup: BeautifulSoup) -> list[dict[str, str]]:
    """Extract team member profiles from a team/people page.

    Looks for common patterns: cards with headings + subtitles, or
    structured lists of people with name/title/bio.
    """
    members: list[dict[str, str]] = []
    seen_names: set[str] = set()

    # Strategy 1: Look for common team card patterns
    # Many VC sites use div/article elements with h2/h3 for name, p/span for title
    for container_tag in ("article", "div", "li", "section"):
        cards = soup.find_all(container_tag, class_=re.compile(
            r"(?:team|member|person|partner|staff|people|bio)", re.IGNORECASE
        ))
        for card in cards:
            member = _parse_member_card(card)
            if member and member["name"] not in seen_names:
                seen_names.add(member["name"])
                members.append(member)

    # Strategy 2: If no class-based cards found, try heading + sibling patterns
    if not members:
        for heading_tag in ("h2", "h3", "h4"):
            headings = soup.find_all(heading_tag)
            for h in headings:
                name = h.get_text(strip=True)
                if not name or len(name) < 3 or len(name) > 80:
                    continue
                # Skip navigation headings
                if name.lower() in (
                    "team", "our team", "people", "partners", "leadership",
                    "portfolio", "about", "contact", "news", "blog",
                ):
                    continue
                # Looks like a person name: 2-4 words, capitalized
                words = name.split()
                if len(words) < 2 or len(words) > 5:
                    continue
                if not all(
                    w[0].isupper()
                    or w.lower() in ("de", "van", "von", "la", "el", "al")
                    for w in words
                ):
                    continue

                title = ""
                bio = ""
                # Check next sibling for title
                next_el = h.find_next_sibling()
                if next_el:
                    text = next_el.get_text(strip=True)
                    if text and len(text) < 150:
                        title = text
                    elif text and len(text) >= 150:
                        bio = text[:500]

                if name not in seen_names:
                    seen_names.add(name)
                    members.append({
                        "name": name,
                        "title": title,
                        "bio": bio,
                    })

    # Cap at 50 members to avoid bloating JSON
    return members[:50]


def _parse_member_card(card) -> dict[str, str] | None:
    """Parse a single team member card element."""
    # Find the name: usually the first heading
    name_el = card.find(re.compile(r"^h[2-5]$"))
    if not name_el:
        # Try strong or b tags
        name_el = card.find(("strong", "b"))
    if not name_el:
        return None

    name = name_el.get_text(strip=True)
    if not name or len(name) < 3 or len(name) > 80:
        return None

    # Find title: often in a p, span, or div with class containing "title" or "role"
    title = ""
    title_el = card.find(class_=re.compile(r"(?:title|role|position|subtitle)", re.IGNORECASE))
    if title_el:
        title = title_el.get_text(strip=True)
    elif name_el.find_next_sibling():
        next_text = name_el.find_next_sibling().get_text(strip=True)
        if next_text and len(next_text) < 150:
            title = next_text

    # Find bio: longer text block
    bio = ""
    bio_el = card.find(class_=re.compile(r"(?:bio|description|summary|about)", re.IGNORECASE))
    if bio_el:
        bio = bio_el.get_text(strip=True)[:500]
    else:
        # Look for a paragraph with substantial text
        for p in card.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 80:
                bio = text[:500]
                break

    return {"name": name, "title": title, "bio": bio}


def _extract_portfolio_companies(soup: BeautifulSoup) -> list[str]:
    """Extract company names from a portfolio page."""
    companies: list[str] = []
    seen: set[str] = set()

    # Strategy 1: Look for portfolio card containers
    for container_tag in ("article", "div", "li", "a"):
        cards = soup.find_all(container_tag, class_=re.compile(
            r"(?:portfolio|company|investment|startup)", re.IGNORECASE
        ))
        for card in cards:
            name = _extract_company_name(card)
            if name and name.lower() not in seen:
                seen.add(name.lower())
                companies.append(name)

    # Strategy 2: Grid/list of links with company names
    if not companies:
        # Look for sections containing "portfolio" in heading
        for section in soup.find_all(["section", "div"]):
            heading = section.find(re.compile(r"^h[1-3]$"))
            if heading and "portfolio" in heading.get_text(strip=True).lower():
                for a_tag in section.find_all("a"):
                    name = a_tag.get_text(strip=True)
                    if name and 2 <= len(name) <= 80 and name.lower() not in seen:
                        # Skip navigation-style links
                        if name.lower() in (
                            "learn more", "read more", "view all", "see all",
                            "back", "next", "previous", "home",
                        ):
                            continue
                        seen.add(name.lower())
                        companies.append(name)

    # Strategy 3: Headings that look like company names under portfolio sections
    if not companies:
        for h in soup.find_all(re.compile(r"^h[2-4]$")):
            name = h.get_text(strip=True)
            if name and 2 <= len(name) <= 60 and name.lower() not in seen:
                # Heuristic: company names are short, capitalized
                if name[0].isupper() and len(name.split()) <= 5:
                    seen.add(name.lower())
                    companies.append(name)

    return companies[:200]  # Cap at 200


def _extract_company_name(card) -> str | None:
    """Extract a company name from a portfolio card element."""
    # Try heading first
    name_el = card.find(re.compile(r"^h[2-5]$"))
    if name_el:
        name = name_el.get_text(strip=True)
        if name and 2 <= len(name) <= 80:
            return name

    # Try img alt text (many portfolio pages show logos)
    img = card.find("img", alt=True)
    if img and img["alt"]:
        alt = img["alt"].strip()
        if 2 <= len(alt) <= 80 and alt.lower() not in ("logo", "image", "photo"):
            return alt

    # Try the card's own text if short enough
    text = card.get_text(strip=True)
    if text and 2 <= len(text) <= 60:
        return text

    return None


def _extract_thesis_info(text: str) -> dict[str, str | list[str] | None]:
    """Extract investment thesis details from page text."""
    info: dict[str, str | list[str] | None] = {
        "check_size": None,
        "stage_focus": [],
        "sector_focus": [],
    }

    text_lower = text.lower()

    # Check size
    check_matches = CHECK_SIZE_RE.findall(text)
    if check_matches:
        info["check_size"] = check_matches[0]

    # Stage focus
    stages = []
    for kw in STAGE_KEYWORDS:
        if kw in text_lower:
            stages.append(kw)
    info["stage_focus"] = stages or None

    # Sector focus
    sectors = []
    for kw in SECTOR_KEYWORDS:
        if kw in text_lower:
            sectors.append(kw)
    info["sector_focus"] = sectors or None

    return info


def _extract_meta_description(soup: BeautifulSoup) -> str | None:
    """Extract meta description or og:description from page."""
    for attr_set in (
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attr_set)
        if tag and tag.get("content"):
            desc = tag["content"].strip()
            if len(desc) >= 20:
                return desc[:500]
    return None


def _extract_location_from_page(soup: BeautifulSoup) -> str | None:
    """Try to extract a location from footer, contact section, or body text."""
    # Check footer first
    footer = soup.find("footer")
    if footer:
        text = footer.get_text(" ", strip=True)
        match = LOCATION_RE.search(text)
        if match:
            loc = match.group(1).strip().rstrip(",.")
            if 3 <= len(loc) <= 80:
                return loc

    # Check contact sections
    for section in soup.find_all(["section", "div"], class_=re.compile(
        r"(?:contact|address|location|footer)", re.IGNORECASE
    )):
        text = section.get_text(" ", strip=True)
        match = LOCATION_RE.search(text)
        if match:
            loc = match.group(1).strip().rstrip(",.")
            if 3 <= len(loc) <= 80:
                return loc

    # Check full body text
    body_text = soup.get_text(" ", strip=True)
    match = LOCATION_RE.search(body_text)
    if match:
        loc = match.group(1).strip().rstrip(",.")
        if 3 <= len(loc) <= 80:
            return loc

    return None


async def _fetch_page(
    client: httpx.AsyncClient, url: str
) -> BeautifulSoup | None:
    """Fetch a page and return parsed BeautifulSoup, or None on failure."""
    try:
        resp = await client.get(url)
        if resp.status_code in (429, 403):
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except httpx.HTTPStatusError:
        raise  # Re-raise rate limits so caller can handle
    except (httpx.TimeoutException, httpx.ConnectError) as e:
        logger.debug(f"[{SOURCE_KEY}] Failed to fetch {url}: {e}")
        return None


class VCWebsiteEnricher(BaseEnricher):
    """Enrich VC investor profiles by scraping their websites."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Build subquery for round participation count (prioritize most active)
        participation_count = (
            select(
                RoundInvestor.investor_id,
                func.count().label("deal_count"),
            )
            .group_by(RoundInvestor.investor_id)
            .subquery()
        )

        # Find VC investors with websites that haven't been scraped yet
        stmt = (
            select(Investor)
            .outerjoin(participation_count, Investor.id == participation_count.c.investor_id)
            .where(
                # Must have a website
                Investor.website.isnot(None),
                Investor.website != "",
                # Not yet enriched by this source
                or_(
                    Investor.source_freshness.is_(None),
                    ~cast(Investor.source_freshness, String).contains(SOURCE_KEY),
                ),
                # VC-type investors only
                (
                    Investor.type.in_(("vc", None))
                    | Investor.investor_category.ilike("%vc%")
                ),
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

        # Load project slugs for portfolio cross-referencing
        project_slugs = await self._load_project_slugs(session)

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
                try:
                    updated = await self._scrape_investor(
                        client, investor, project_slugs
                    )
                    stamp_freshness(investor, SOURCE_KEY)
                    if updated:
                        investor.last_enriched_at = datetime.now(timezone.utc)
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
                    logger.debug(f"[{SOURCE_KEY}] Error for {investor.name}: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()

        logger.info(
            f"[{SOURCE_KEY}] Complete: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _load_project_slugs(self, session: AsyncSession) -> set[str]:
        """Load all project slugs for portfolio cross-referencing."""
        stmt = select(Project.slug).where(Project.slug.isnot(None))
        rows = await session.execute(stmt)
        return {row[0] for row in rows.all()}

    async def _scrape_investor(
        self,
        client: httpx.AsyncClient,
        investor: Investor,
        project_slugs: set[str],
    ) -> bool:
        """Scrape an investor's website and update their profile."""
        website = investor.website.rstrip("/")
        if not website.startswith("http"):
            website = "https://" + website

        logger.debug(f"[{SOURCE_KEY}] Scraping {investor.name}: {website}")

        # Step 1: Fetch homepage
        homepage_soup = await _fetch_page(client, website)
        if not homepage_soup:
            return False

        # Step 2: Discover subpages from nav links
        subpages = _discover_subpages(website, homepage_soup)

        updated = False

        # Step 3: Scrape team page
        team_data = await self._scrape_team(client, subpages.get("team"), homepage_soup)
        if team_data:
            freshness = investor.source_freshness or {}
            freshness["team"] = team_data
            investor.source_freshness = freshness
            flag_modified(investor, "source_freshness")
            updated = True

        await asyncio.sleep(REQUEST_DELAY)

        # Step 4: Scrape portfolio page
        portfolio_data = await self._scrape_portfolio(
            client, subpages.get("portfolio"), homepage_soup, project_slugs
        )
        if portfolio_data:
            freshness = investor.source_freshness or {}
            freshness["portfolio_count"] = portfolio_data["count"]
            freshness["portfolio_sample"] = portfolio_data["sample"]
            if portfolio_data.get("matched_projects"):
                freshness["portfolio_matched"] = portfolio_data["matched_projects"]
            investor.source_freshness = freshness
            flag_modified(investor, "source_freshness")
            updated = True

        await asyncio.sleep(REQUEST_DELAY)

        # Step 5: Scrape about/thesis page
        thesis_data = await self._scrape_thesis(
            client, subpages.get("about"), homepage_soup
        )
        if thesis_data:
            freshness = investor.source_freshness or {}
            if thesis_data.get("check_size"):
                freshness["check_size"] = thesis_data["check_size"]
            if thesis_data.get("stage_focus"):
                freshness["stage_focus"] = thesis_data["stage_focus"]
            if thesis_data.get("sector_focus"):
                freshness["sector_focus"] = thesis_data["sector_focus"]
            investor.source_freshness = freshness
            flag_modified(investor, "source_freshness")
            updated = True

        # Step 6: Fill empty fields from homepage
        if not investor.description:
            desc = _extract_meta_description(homepage_soup)
            # Also try about page if we fetched it
            if not desc and subpages.get("about"):
                about_soup = await _fetch_page(client, subpages["about"])
                if about_soup:
                    desc = _extract_meta_description(about_soup)
                    await asyncio.sleep(REQUEST_DELAY)
            if desc:
                investor.description = desc
                updated = True

        if not investor.hq_location:
            location = _extract_location_from_page(homepage_soup)
            if location:
                investor.hq_location = location
                updated = True

        return updated

    async def _scrape_team(
        self,
        client: httpx.AsyncClient,
        team_url: str | None,
        homepage_soup: BeautifulSoup,
    ) -> list[dict[str, str]] | None:
        """Scrape team members from team page or homepage."""
        if team_url:
            soup = await _fetch_page(client, team_url)
            if soup:
                members = _extract_team_members(soup)
                if members:
                    return members

        # Fallback: try extracting from homepage
        members = _extract_team_members(homepage_soup)
        return members or None

    async def _scrape_portfolio(
        self,
        client: httpx.AsyncClient,
        portfolio_url: str | None,
        homepage_soup: BeautifulSoup,
        project_slugs: set[str],
    ) -> dict | None:
        """Scrape portfolio companies from portfolio page or homepage."""
        companies: list[str] = []

        if portfolio_url:
            soup = await _fetch_page(client, portfolio_url)
            if soup:
                companies = _extract_portfolio_companies(soup)

        # Fallback: try homepage
        if not companies:
            companies = _extract_portfolio_companies(homepage_soup)

        if not companies:
            return None

        # Cross-reference with our projects table
        matched = []
        for company_name in companies:
            slug = make_slug(company_name)
            if slug in project_slugs:
                matched.append(company_name)

        return {
            "count": len(companies),
            "sample": companies[:20],  # Store first 20 names
            "matched_projects": matched[:20] if matched else None,
        }

    async def _scrape_thesis(
        self,
        client: httpx.AsyncClient,
        about_url: str | None,
        homepage_soup: BeautifulSoup,
    ) -> dict | None:
        """Extract investment thesis info from about page or homepage."""
        # Try about page first
        if about_url:
            soup = await _fetch_page(client, about_url)
            if soup:
                text = soup.get_text(" ", strip=True)
                info = _extract_thesis_info(text)
                if any(v for v in info.values()):
                    return info

        # Fallback: homepage text
        text = homepage_soup.get_text(" ", strip=True)
        info = _extract_thesis_info(text)
        if any(v for v in info.values()):
            return info

        return None
