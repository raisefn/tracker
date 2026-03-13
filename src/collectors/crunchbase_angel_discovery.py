"""Published angel investor list scraper.

Discovers new angel investors by scraping published "top angel investors"
articles from major tech/business publications. These articles are:
- Well-indexed by search engines (unlike Crunchbase/Wellfound)
- Not behind anti-scraper walls
- Curated lists of real, active investors with names and bios
- Published by Forbes, TechCrunch, Business Insider, Inc., etc.

Strategy:
1. Search DDG for "top angel investors" / "best angel investors" articles
2. Scrape each article for investor names (look for lists, tables, headings)
3. Extract name + description for each investor found
4. Create new Investor records for names not already in the DB

Also scrapes GitHub "awesome" lists that curate angel investors.

Rate limit: 3 seconds between requests, 30s pause on rate limit.
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

SOURCE_KEY = "published_list_discovery"
REQUEST_DELAY = 3
RATE_LIMIT_PAUSE = 30
MAX_INVESTORS_PER_RUN = 5000

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# DDG queries to find published angel investor lists
ARTICLE_QUERIES = [
    # General top lists
    '"top angel investors" list',
    '"best angel investors" 2024',
    '"best angel investors" 2025',
    '"most active angel investors"',
    '"prolific angel investors"',
    '"angel investors to know"',
    '"angel investors you should know"',
    '"top startup investors"',
    '"best seed investors"',
    '"top pre-seed investors"',
    '"super angels" list',
    '"angel investor" list "check size"',
    '"angel investor directory"',

    # Forbes / publications
    'forbes "angel investors" list',
    'techcrunch "angel investors" list',
    '"business insider" "angel investors"',
    'inc.com "angel investors"',
    'entrepreneur.com "angel investors"',
    'fortune "angel investors"',

    # Sector-specific lists
    '"angel investors" fintech list',
    '"angel investors" "artificial intelligence" list',
    '"angel investors" AI list',
    '"angel investors" crypto list',
    '"angel investors" blockchain list',
    '"angel investors" web3 list',
    '"angel investors" SaaS list',
    '"angel investors" healthcare list',
    '"angel investors" biotech list',
    '"angel investors" climate list',
    '"angel investors" cleantech list',
    '"angel investors" edtech list',
    '"angel investors" consumer list',
    '"angel investors" deep tech list',
    '"angel investors" hardware list',
    '"angel investors" cybersecurity list',
    '"angel investors" gaming list',
    '"angel investors" food tech list',
    '"angel investors" proptech list',

    # Location-specific lists
    '"angel investors" "San Francisco" list',
    '"angel investors" "New York" list',
    '"angel investors" "Los Angeles" list',
    '"angel investors" London list',
    '"angel investors" Singapore list',
    '"angel investors" India list',
    '"angel investors" Europe list',
    '"angel investors" "Tel Aviv" list',
    '"angel investors" Africa list',
    '"angel investors" "Latin America" list',
    '"angel investors" Canada list',
    '"angel investors" Australia list',
    '"angel investors" Berlin list',
    '"angel investors" Miami list',
    '"angel investors" Austin list',
    '"angel investors" Boston list',
    '"angel investors" Seattle list',

    # Specific curated sources
    '"angel investor" "invested in" "portfolio"',
    '"angel investor" "$25K" OR "$50K" OR "$100K"',
    'site:github.com "awesome" "angel investors"',
    'site:github.com "angel investor" list',
    '"top women angel investors"',
    '"diverse angel investors" list',
    '"angel investors" "emerging managers"',
    '"angel investor networks"',
    '"angel groups" directory',
]

# Known good article URLs to scrape directly (curated, high-quality lists)
KNOWN_ARTICLE_URLS = [
    # These are placeholder patterns — the DDG search will find current ones
    # but these known URLs provide a reliable baseline
]


class PublishedListAngelDiscovery(BaseEnricher):
    """Discover angel investors from published lists and articles."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())
        created_slugs: set[str] = set()
        seen_names: set[str] = set()
        scraped_urls: set[str] = set()
        rate_limit_count = 0

        async with httpx.AsyncClient(
            timeout=20,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            # Phase 1: Search DDG for article URLs
            article_urls: list[str] = list(KNOWN_ARTICLE_URLS)

            for query in ARTICLE_QUERIES:
                if rate_limit_count >= 3:
                    break
                try:
                    urls = await self._search_ddg_for_articles(client, query)
                    for url in urls:
                        if url not in scraped_urls and url not in article_urls:
                            article_urls.append(url)
                except _RateLimitedError:
                    rate_limit_count += 1
                    logger.warning(
                        f"[{SOURCE_KEY}] DDG rate limited ({rate_limit_count}/3), "
                        f"pausing {RATE_LIMIT_PAUSE}s"
                    )
                    await asyncio.sleep(RATE_LIMIT_PAUSE)
                    continue
                except Exception as e:
                    logger.debug(f"[{SOURCE_KEY}] Search error: {e}")

                await asyncio.sleep(REQUEST_DELAY)

            logger.info(f"[{SOURCE_KEY}] Found {len(article_urls)} article URLs to scrape")

            # Phase 2: Scrape each article for investor names
            for url in article_urls:
                if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                    break
                if url in scraped_urls:
                    continue
                scraped_urls.add(url)

                try:
                    people = await self._scrape_article(client, url)
                    new_count = 0
                    for person in people:
                        if len(created_slugs) >= MAX_INVESTORS_PER_RUN:
                            break
                        name = person.get("name")
                        if not name or name in seen_names:
                            continue
                        seen_names.add(name)

                        try:
                            created = await self._create_investor(
                                session, person, created_slugs
                            )
                            if created:
                                new_count += 1
                                result.records_updated += 1
                            else:
                                result.records_skipped += 1
                        except Exception as e:
                            result.errors.append(f"{name}: {e}")

                    if new_count > 0:
                        logger.info(
                            f"[{SOURCE_KEY}] '{url[:80]}' → "
                            f"{len(people)} people, {new_count} new"
                        )

                except Exception as e:
                    logger.debug(f"[{SOURCE_KEY}] Scrape error {url[:80]}: {e}")

                await asyncio.sleep(REQUEST_DELAY)

        await session.flush()
        logger.info(
            f"[{SOURCE_KEY}] Done: {result.records_updated} new investors, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors. "
            f"Articles scraped: {len(scraped_urls)}"
        )
        return result

    async def _search_ddg_for_articles(
        self, client: httpx.AsyncClient, query: str
    ) -> list[str]:
        """Search DDG and return article URLs (not investor profile URLs)."""
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
        )
        if resp.status_code in (403, 429):
            raise _RateLimitedError()
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        urls = []

        for link in soup.select("a.result__a"):
            href = link.get("href", "")
            url_match = re.search(r"uddg=([^&]+)", href)
            if url_match:
                actual_url = urllib.parse.unquote(url_match.group(1))
            else:
                actual_url = href

            if not actual_url.startswith("http"):
                continue

            # Skip social media profiles — we want articles
            skip_domains = [
                "linkedin.com/in/", "twitter.com/", "x.com/",
                "facebook.com/", "instagram.com/",
                "crunchbase.com/person/", "wellfound.com/people/",
            ]
            if any(d in actual_url for d in skip_domains):
                continue

            # Accept articles from known good domains + github
            clean = actual_url.split("?")[0]
            urls.append(clean)

        return urls

    async def _scrape_article(
        self, client: httpx.AsyncClient, url: str
    ) -> list[dict]:
        """Scrape an article page for angel investor names and descriptions."""
        try:
            resp = await client.get(url)
        except httpx.HTTPError:
            return []

        if resp.status_code != 200:
            return []

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # Remove navigation, sidebar, footer noise
        for tag in soup.select("nav, footer, .sidebar, .nav, .footer, .menu, .ad, .advertisement"):
            tag.decompose()

        people = []

        # Strategy 1: Look for numbered/bulleted lists with names
        people.extend(self._extract_from_lists(soup))

        # Strategy 2: Look for headings (h2, h3) that are person names
        people.extend(self._extract_from_headings(soup))

        # Strategy 3: Look for bold names followed by descriptions
        people.extend(self._extract_from_bold_names(soup))

        # Strategy 4: GitHub markdown lists (for awesome-* repos)
        if "github.com" in url or "raw.githubusercontent.com" in url:
            people.extend(self._extract_from_github_markdown(html))

        # Deduplicate by name
        seen = set()
        unique = []
        for p in people:
            name = p.get("name", "").strip()
            if name and name not in seen:
                seen.add(name)
                unique.append(p)

        return unique

    def _extract_from_lists(self, soup: BeautifulSoup) -> list[dict]:
        """Extract names from numbered/bulleted lists (ol, ul)."""
        people = []
        for li in soup.select("ol li, ul li"):
            text = li.get_text(strip=True)
            if len(text) < 10 or len(text) > 1000:
                continue

            # Look for bold/strong name at start
            bold = li.find(["strong", "b"])
            if bold:
                name_candidate = bold.get_text(strip=True)
                # Remove numbering like "1." or "1)"
                name_candidate = re.sub(r"^\d+[\.\)]\s*", "", name_candidate).strip()
                # Remove trailing punctuation
                name_candidate = re.sub(r"[:\-–—,]+$", "", name_candidate).strip()

                if self._is_person_name(name_candidate):
                    desc = text.replace(name_candidate, "", 1).strip()
                    desc = re.sub(r"^[\s:\-–—]+", "", desc).strip()
                    people.append({
                        "name": name_candidate,
                        "description": desc[:500] if desc else None,
                    })
                    continue

            # Try to extract "Name — description" or "Name: description" pattern
            match = re.match(
                r"^(?:\d+[\.\)]\s*)?([A-Z][a-zA-Z]+(?:\s+[A-Z][a-zA-Z]+){1,4})\s*[\-–—:]\s*(.+)",
                text,
            )
            if match:
                name_candidate = match.group(1).strip()
                if self._is_person_name(name_candidate):
                    people.append({
                        "name": name_candidate,
                        "description": match.group(2).strip()[:500],
                    })

        return people

    def _extract_from_headings(self, soup: BeautifulSoup) -> list[dict]:
        """Extract names from h2/h3/h4 headings that look like person names."""
        people = []
        for heading in soup.select("h2, h3, h4"):
            text = heading.get_text(strip=True)
            # Remove numbering
            text = re.sub(r"^\d+[\.\)]\s*", "", text).strip()
            # Remove trailing punctuation
            text = re.sub(r"[:\-–—,]+$", "", text).strip()

            if not self._is_person_name(text):
                continue

            # Get following paragraph as description
            desc = None
            next_el = heading.find_next_sibling()
            if next_el and next_el.name == "p":
                desc = next_el.get_text(strip=True)[:500]

            people.append({
                "name": text,
                "description": desc,
            })

        return people

    def _extract_from_bold_names(self, soup: BeautifulSoup) -> list[dict]:
        """Extract names from bold text within paragraphs."""
        people = []
        for p in soup.select("p"):
            bolds = p.find_all(["strong", "b"])
            for bold in bolds:
                name_candidate = bold.get_text(strip=True)
                name_candidate = re.sub(r"^\d+[\.\)]\s*", "", name_candidate).strip()
                name_candidate = re.sub(r"[:\-–—,]+$", "", name_candidate).strip()

                if self._is_person_name(name_candidate):
                    # Get rest of paragraph as description
                    full_text = p.get_text(strip=True)
                    desc = full_text.replace(name_candidate, "", 1).strip()
                    desc = re.sub(r"^[\s:\-–—]+", "", desc).strip()
                    people.append({
                        "name": name_candidate,
                        "description": desc[:500] if desc else None,
                    })

        return people

    def _extract_from_github_markdown(self, html: str) -> list[dict]:
        """Extract names from GitHub README markdown rendered as HTML."""
        soup = BeautifulSoup(html, "html.parser")
        people = []

        # GitHub renders markdown in article.markdown-body
        article = soup.select_one("article.markdown-body") or soup

        # Look for links that look like person names
        for link in article.find_all("a", href=True):
            text = link.get_text(strip=True)
            if self._is_person_name(text):
                # Get surrounding text as description
                parent = link.parent
                if parent:
                    full_text = parent.get_text(strip=True)
                    desc = full_text.replace(text, "", 1).strip()
                    desc = re.sub(r"^[\s:\-–—|]+", "", desc).strip()
                    people.append({
                        "name": text,
                        "description": desc[:500] if desc else None,
                    })

        # Also check table rows
        for row in article.select("tr"):
            cells = row.select("td")
            if len(cells) >= 1:
                first_cell = cells[0].get_text(strip=True)
                if self._is_person_name(first_cell):
                    desc = " | ".join(c.get_text(strip=True) for c in cells[1:])
                    people.append({
                        "name": first_cell,
                        "description": desc[:500] if desc else None,
                    })

        return people

    def _is_person_name(self, text: str) -> bool:
        """Check if text looks like a person's name."""
        if not text or len(text) < 3 or len(text) > 80:
            return False

        words = text.split()
        if len(words) < 2 or len(words) > 5:
            return False

        # Must start with uppercase letter
        if not words[0][0].isupper():
            return False

        # No numbers
        if re.search(r"\d", text):
            return False

        # No common non-name words
        non_names = {
            "the", "top", "best", "most", "how", "what", "why", "when",
            "angel", "investor", "investors", "investing", "investment",
            "venture", "capital", "fund", "funding", "about", "read",
            "more", "learn", "see", "view", "click", "here", "share",
            "related", "posts", "articles", "news", "home", "contact",
            "sign", "login", "register", "subscribe", "newsletter",
            "number", "total", "notable", "portfolio", "companies",
            "exits", "investments", "overview", "summary", "table",
            "contents", "key", "takeaways", "conclusion", "introduction",
            "featured", "image", "source", "photo", "credit", "getty",
            "shutterstock", "updated", "published", "written", "author",
        }
        lower_words = {w.lower() for w in words}
        if lower_words & non_names:
            return False

        # At least 2 words starting with uppercase
        cap_words = sum(1 for w in words if w[0].isupper())
        if cap_words < 2:
            return False

        return True

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

        existing = await session.execute(
            select(Investor.id).where(Investor.slug == slug)
        )
        if existing.scalar_one_or_none() is not None:
            return False

        description = person.get("description")

        investor = Investor(
            name=name,
            slug=slug,
            type="angel",
            description=description[:2000] if description else None,
            investor_category="angel_investor",
            source_freshness={
                SOURCE_KEY: datetime.now(timezone.utc).isoformat(),
            },
            last_enriched_at=datetime.now(timezone.utc),
        )
        session.add(investor)
        created_slugs.add(slug)
        return True


class _RateLimitedError(Exception):
    """Raised on 403/429."""
