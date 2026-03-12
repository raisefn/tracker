"""Founder enricher — populate LinkedIn, bio, and previous companies.

21K+ founders in the DB from SEC EDGAR with only names and roles.
This enricher uses DuckDuckGo to find LinkedIn profiles and extract
career history, giving the Brain product founder context for intelligence.

Strategy per founder:
1. Search DDG for "{name} {company} LinkedIn"
2. Extract LinkedIn URL from results
3. Search DDG for "{name} {company} founder" for bio/background
4. Parse snippets for previous company mentions and bio text

Rate limit: 2s between DDG requests, max 30 founders per run.
"""

import asyncio
import logging
import re
from urllib.parse import unquote

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project
from src.models.founder import Founder

logger = logging.getLogger(__name__)

SOURCE_KEY = "founder_search"
BATCH_SIZE = 20
REQUEST_DELAY = 4.0

DDG_URL = "https://html.duckduckgo.com/html/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}

# Names too generic to search
SKIP_NAMES = {
    "unknown", "undisclosed", "anonymous", "n/a", "na", "none", "tbd",
    "various", "director", "officer", "member", "manager", "agent",
}

# Previous company signal words in bios/snippets
PREV_COMPANY_PATTERNS = [
    re.compile(r"(?:formerly|previously|ex-|former)\s+(?:at\s+)?([A-Z][A-Za-z0-9 &.\-']+)", re.IGNORECASE),
    re.compile(r"(?:worked at|came from|left|departed|co-founded|founded)\s+([A-Z][A-Za-z0-9 &.\-']+)", re.IGNORECASE),
    re.compile(r"(?:alum(?:nus|na)?|graduate)\s+(?:of\s+)?([A-Z][A-Za-z0-9 &.\-']+)", re.IGNORECASE),
]

# Role patterns
ROLE_PATTERNS = re.compile(
    r"\b(CEO|CTO|COO|CFO|CPO|CRO|CMO|VP|SVP|EVP|"
    r"Chief\s+\w+\s+Officer|"
    r"Co-?[Ff]ounder|Founder|"
    r"Managing\s+(?:Director|Partner)|"
    r"General\s+Partner|Partner|"
    r"Head\s+of\s+\w+|"
    r"Director\s+of\s+\w+|"
    r"President)\b",
    re.IGNORECASE,
)


def _is_searchable(name: str) -> bool:
    """Return False for names that are too short or generic."""
    cleaned = name.strip()
    if len(cleaned) < 4:
        return False
    if cleaned.lower() in SKIP_NAMES:
        return False
    # Must have at least first + last name
    if len(cleaned.split()) < 2:
        return False
    return True


class FounderEnricher(BaseEnricher):
    """Enrich founder records with LinkedIn, bio, and career history."""

    def source_name(self) -> str:
        return SOURCE_KEY

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        founders = await self._get_candidates(session)
        if not founders:
            logger.info(f"[{SOURCE_KEY}] No candidate founders to enrich")
            return result

        logger.info(f"[{SOURCE_KEY}] Processing {len(founders)} founder candidates")

        # Pre-load project names for search context
        project_ids = {f.project_id for f in founders}
        projects = (
            await session.execute(
                select(Project).where(Project.id.in_(project_ids))
            )
        ).scalars().all()
        project_map = {p.id: p.name for p in projects}

        async with httpx.AsyncClient(
            timeout=15,
            headers=HEADERS,
            follow_redirects=True,
        ) as client:
            for founder in founders:
                company = project_map.get(founder.project_id, "")
                try:
                    updated = await self._enrich_founder(client, founder, company)
                    if updated:
                        stamp_freshness(founder, self.source_name())
                        result.records_updated += 1
                    else:
                        stamp_freshness(founder, self.source_name())
                        result.records_skipped += 1
                except _RateLimited:
                    logger.warning(f"[{SOURCE_KEY}] Rate limited, stopping run")
                    result.errors.append("DDG rate limited, stopping early")
                    break
                except Exception as e:
                    error_msg = f"{founder.name}: {e}"
                    logger.warning(f"[{SOURCE_KEY}] Error: {error_msg}")
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"[{SOURCE_KEY}] Done: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _get_candidates(self, session: AsyncSession) -> list[Founder]:
        """Get founders not yet enriched, prioritizing those at companies with most rounds."""
        from src.models import Round

        round_count = (
            select(
                Round.project_id,
                func.count().label("round_count"),
            )
            .group_by(Round.project_id)
            .subquery()
        )

        query = (
            select(Founder)
            .outerjoin(round_count, Founder.project_id == round_count.c.project_id)
            .where(
                Founder.source_freshness.is_(None)
                | ~Founder.source_freshness.has_key(SOURCE_KEY)  # noqa: W601
            )
            .order_by(func.coalesce(round_count.c.round_count, 0).desc())
            .limit(BATCH_SIZE)
        )

        rows = await session.execute(query)
        return list(rows.scalars().all())

    async def _enrich_founder(
        self, client: httpx.AsyncClient, founder: Founder, company: str
    ) -> bool:
        """Search for founder info and update record."""
        if not _is_searchable(founder.name):
            return False

        updated = False

        # Search 1: Find LinkedIn profile
        if not founder.linkedin:
            await asyncio.sleep(REQUEST_DELAY)
            linkedin = await self._search_linkedin(client, founder.name, company)
            if linkedin:
                founder.linkedin = linkedin
                updated = True

        # Search 2: Find bio, previous companies, Twitter
        await asyncio.sleep(REQUEST_DELAY)
        bio, prev_companies, twitter = await self._search_background(
            client, founder.name, company
        )

        if bio and not founder.bio:
            founder.bio = bio[:2000]
            updated = True

        if prev_companies and not founder.previous_companies:
            founder.previous_companies = prev_companies
            flag_modified(founder, "previous_companies")
            updated = True

        if twitter and not founder.twitter:
            founder.twitter = twitter
            updated = True

        return updated

    async def _search_linkedin(
        self, client: httpx.AsyncClient, name: str, company: str
    ) -> str | None:
        """Search DDG for the founder's LinkedIn profile."""
        query = f'"{name}" {company} linkedin profile'
        html = await self._ddg_search(client, query)
        if not html:
            return None

        # Extract LinkedIn URLs from results
        for url, _snippet in _extract_ddg_results(html):
            if "linkedin.com/in/" in url:
                # Clean and return
                clean = url.split("?")[0]
                if clean.endswith("/"):
                    clean = clean[:-1]
                return clean

        return None

    async def _search_background(
        self, client: httpx.AsyncClient, name: str, company: str
    ) -> tuple[str | None, list[dict] | None, str | None]:
        """Search DDG for founder background info.

        Returns (bio, previous_companies, twitter).
        """
        query = f'"{name}" {company} founder CEO'
        html = await self._ddg_search(client, query)
        if not html:
            return None, None, None

        results = _extract_ddg_results(html)
        if not results:
            return None, None, None

        bio = None
        prev_companies: list[dict] = []
        twitter = None

        for url, snippet in results[:5]:
            clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()

            # Extract bio from first substantial snippet mentioning the person
            if not bio and name.split()[0].lower() in clean_snippet.lower() and len(clean_snippet) > 50:
                bio = clean_snippet[:2000]

            # Extract Twitter handle
            if not twitter:
                tw_match = re.search(
                    r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})", url
                )
                if tw_match:
                    handle = tw_match.group(1).lower()
                    if handle not in ("share", "intent", "home", "search", "explore", "i"):
                        twitter = f"@{tw_match.group(1)}"

            # Extract previous companies from snippets
            for pattern in PREV_COMPANY_PATTERNS:
                for match in pattern.finditer(clean_snippet):
                    company_name = match.group(1).strip().rstrip(".,;")
                    if company_name and len(company_name) > 2 and company_name.lower() != company.lower():
                        # Avoid duplicates
                        if not any(p.get("name", "").lower() == company_name.lower() for p in prev_companies):
                            prev_companies.append({"name": company_name})

            # Extract roles mentioned with previous companies
            for match in ROLE_PATTERNS.finditer(clean_snippet):
                role = match.group(1)
                if prev_companies and not prev_companies[-1].get("role"):
                    prev_companies[-1]["role"] = role

        return (
            bio,
            prev_companies[:10] if prev_companies else None,
            twitter,
        )

    async def _ddg_search(self, client: httpx.AsyncClient, query: str) -> str | None:
        """Execute a DuckDuckGo HTML search and return raw HTML.

        On 403/429, backs off 30s and retries once before raising _RateLimited.
        """
        for attempt in range(2):
            try:
                resp = await client.post(DDG_URL, data={"q": query, "b": ""})
                if resp.status_code in (403, 429):
                    if attempt == 0:
                        logger.debug(f"[{SOURCE_KEY}] DDG {resp.status_code}, backing off 30s")
                        await asyncio.sleep(30)
                        continue
                    raise _RateLimited()
                if resp.status_code != 200:
                    return None
                return resp.text
            except _RateLimited:
                raise
            except Exception as e:
                logger.debug(f"[{SOURCE_KEY}] DDG search error: {e}")
                return None
        return None


def _extract_ddg_results(html: str) -> list[tuple[str, str]]:
    """Extract (url, snippet) pairs from DuckDuckGo HTML results."""
    results: list[tuple[str, str]] = []

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
        # Decode DDG redirect URLs
        uddg_match = re.search(r"uddg=([^&]+)", raw_url)
        if uddg_match:
            url = unquote(uddg_match.group(1))
        elif raw_url.startswith("http"):
            url = raw_url
        else:
            continue

        snippet = snippets[i] if i < len(snippets) else ""
        results.append((url, snippet))

    return results


class _RateLimited(Exception):
    """Raised when DDG returns 403/429."""
