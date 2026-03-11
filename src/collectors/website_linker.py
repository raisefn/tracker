"""Scrape project websites to discover GitHub and Twitter links.

Fetches homepage HTML for projects that have a website but are missing
GitHub/Twitter handles. Extracts links via regex — no heavy parsing needed.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.models import Project

logger = logging.getLogger(__name__)

# Patterns to extract from HTML
GITHUB_RE = re.compile(
    r'href=["\']https?://github\.com/([a-zA-Z0-9_-]+)(?:/([a-zA-Z0-9_.-]+))?["\']',
    re.IGNORECASE,
)
TWITTER_RE = re.compile(
    r'href=["\']https?://(?:twitter\.com|x\.com)/([a-zA-Z0-9_]+)["\']',
    re.IGNORECASE,
)

# Twitter handles to ignore (common non-project accounts)
TWITTER_BLOCKLIST = frozenset({
    "intent", "share", "home", "search", "explore", "settings",
    "i", "hashtag", "login", "signup",
})

# GitHub orgs to ignore (common non-project orgs)
GITHUB_BLOCKLIST = frozenset({
    "github", "microsoft", "google", "facebook", "meta", "apple",
    "twitter", "vercel", "netlify", "heroku", "aws", "azure",
    "topics", "features", "pricing", "about", "sponsors", "orgs",
})

# Max projects per run to avoid extremely long runs
BATCH_SIZE = 1000


class WebsiteLinker(BaseEnricher):
    def source_name(self) -> str:
        return "website_linker"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Find projects with website but missing github OR twitter
        projects = (
            await session.execute(
                select(Project)
                .where(
                    Project.website.isnot(None),
                    or_(
                        Project.github.is_(None),
                        Project.twitter.is_(None),
                    ),
                )
                .limit(BATCH_SIZE)
            )
        ).scalars().all()

        if not projects:
            logger.info("No projects need website link discovery")
            return result

        logger.info(f"Website linker: scanning {len(projects)} project websites")

        async with httpx.AsyncClient(
            timeout=10.0,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=5),
            headers={"User-Agent": "raisefn-tracker/1.0 (startup research bot)"},
        ) as client:
            for project in projects:
                try:
                    url = project.website.strip()
                    if not url.startswith("http"):
                        url = f"https://{url}"

                    resp = await client.get(url)
                    if resp.status_code != 200:
                        result.records_skipped += 1
                        continue

                    html = resp.text[:100_000]  # Cap at 100KB
                    updated = False

                    # Extract GitHub
                    if not project.github:
                        github_url = self._extract_github(html, project.name)
                        if github_url:
                            project.github = github_url
                            updated = True

                    # Extract Twitter
                    if not project.twitter:
                        twitter_handle = self._extract_twitter(html)
                        if twitter_handle:
                            project.twitter = twitter_handle
                            updated = True

                    if updated:
                        stamp_freshness(project, self.source_name())
                        result.records_updated += 1
                    else:
                        result.records_skipped += 1

                    # Be respectful — 0.5s between requests
                    await asyncio.sleep(0.5)

                except Exception as e:
                    result.records_skipped += 1
                    # Don't log every failure — many sites will be down/slow
                    if "timeout" not in str(e).lower():
                        result.errors.append(f"{project.slug}: {e}")

        await session.flush()
        logger.info(
            f"Website linker: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    def _extract_github(self, html: str, project_name: str) -> str | None:
        """Extract the most likely GitHub org URL from HTML."""
        matches = GITHUB_RE.findall(html)
        if not matches:
            return None

        # Count occurrences of each org
        org_counts: dict[str, int] = {}
        for org, repo in matches:
            org_lower = org.lower()
            if org_lower in GITHUB_BLOCKLIST:
                continue
            if len(org) < 2:
                continue
            org_counts[org] = org_counts.get(org, 0) + 1

        if not org_counts:
            return None

        # Pick the most frequently linked org
        best_org = max(org_counts, key=org_counts.get)
        return f"https://github.com/{best_org}"

    def _extract_twitter(self, html: str) -> str | None:
        """Extract the most likely Twitter handle from HTML."""
        matches = TWITTER_RE.findall(html)
        if not matches:
            return None

        # Count occurrences of each handle
        handle_counts: dict[str, int] = {}
        for handle in matches:
            handle_lower = handle.lower()
            if handle_lower in TWITTER_BLOCKLIST:
                continue
            if len(handle) < 2:
                continue
            handle_counts[handle] = handle_counts.get(handle, 0) + 1

        if not handle_counts:
            return None

        # Pick the most frequently linked handle
        return max(handle_counts, key=handle_counts.get)
