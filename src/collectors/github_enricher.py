"""Enrich projects with GitHub activity data."""

import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collectors.enrichment_base import BaseEnricher, EnrichmentResult, stamp_freshness
from src.config import settings
from src.models import Project

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


def _parse_github_org(url: str) -> str | None:
    """Extract org/user from a GitHub URL or bare org name."""
    if not url:
        return None
    url = url.strip()
    # Handle full URLs
    if url.startswith("http"):
        parsed = urlparse(url)
        if "github.com" not in (parsed.hostname or ""):
            return None
        parts = parsed.path.strip("/").split("/")
        if parts and parts[0]:
            return parts[0]
        return None
    # Handle bare org/user names (from DefiLlama)
    if "/" not in url and " " not in url:
        return url
    return None


class GitHubEnricher(BaseEnricher):
    def source_name(self) -> str:
        return "github"

    async def enrich(self, session: AsyncSession) -> EnrichmentResult:
        result = EnrichmentResult(source=self.source_name())

        # Only enrich projects that have a GitHub URL
        projects = (
            await session.execute(
                select(Project).where(Project.github.isnot(None))
            )
        ).scalars().all()

        if not projects:
            logger.info("No projects with GitHub URLs found.")
            return result

        headers = {"Accept": "application/vnd.github+json"}
        if settings.github_token:
            headers["Authorization"] = f"Bearer {settings.github_token}"

        # Rate limits: 60/hr unauthenticated, 5000/hr with token
        delay = 1.0 if settings.github_token else 3.0

        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            seen_orgs: set[str] = set()

            for project in projects:
                org = _parse_github_org(project.github)
                if not org or org in seen_orgs:
                    result.records_skipped += 1
                    continue
                seen_orgs.add(org)

                try:
                    # Fetch repos for org (try org first, fall back to user)
                    repos = await self._fetch_repos(client, org)
                    if repos is None:
                        result.records_skipped += 1
                        continue

                    # Aggregate stats
                    total_stars = 0
                    total_contributors = 0
                    total_commits_30d = 0

                    for repo in repos[:20]:  # Cap at 20 most active repos
                        total_stars += repo.get("stargazers_count", 0)

                        # Fetch contributor count (cheap: just check header)
                        contributors = await self._fetch_contributor_count(
                            client, repo["full_name"]
                        )
                        total_contributors += contributors

                        # Fetch recent commit activity
                        commits = await self._fetch_commits_30d(
                            client, repo["full_name"]
                        )
                        total_commits_30d += commits

                        await asyncio.sleep(delay)

                    project.github_org = org
                    project.github_stars = total_stars
                    project.github_contributors = total_contributors
                    project.github_commits_30d = total_commits_30d
                    project.last_enriched_at = datetime.now(timezone.utc)
                    stamp_freshness(project, self.source_name())
                    result.records_updated += 1

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 403:
                        logger.warning("GitHub rate limited, stopping enrichment")
                        result.errors.append("Rate limited by GitHub")
                        break
                    error_msg = f"GitHub error for {org}: {e.response.status_code}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1
                except Exception as e:
                    error_msg = f"Error enriching GitHub {org}: {e}"
                    logger.warning(error_msg)
                    result.errors.append(error_msg)
                    result.records_skipped += 1

        await session.flush()
        logger.info(
            f"GitHub enrichment: {result.records_updated} updated, "
            f"{result.records_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    async def _fetch_repos(self, client: httpx.AsyncClient, org: str) -> list[dict] | None:
        """Fetch repos for an org, falling back to user endpoint."""
        # Try org first
        resp = await client.get(
            f"{GITHUB_API_BASE}/orgs/{org}/repos",
            params={"sort": "pushed", "per_page": 20},
        )
        if resp.status_code == 200:
            return resp.json()

        # Fall back to user
        resp = await client.get(
            f"{GITHUB_API_BASE}/users/{org}/repos",
            params={"sort": "pushed", "per_page": 20},
        )
        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return None

    async def _fetch_contributor_count(self, client: httpx.AsyncClient, repo_full_name: str) -> int:
        """Get approximate contributor count for a repo."""
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{repo_full_name}/contributors",
            params={"per_page": 1, "anon": "false"},
        )
        if resp.status_code != 200:
            return 0
        # Parse total from Link header if present
        link = resp.headers.get("Link", "")
        match = re.search(r'page=(\d+)>; rel="last"', link)
        if match:
            return int(match.group(1))
        return len(resp.json())

    async def _fetch_commits_30d(self, client: httpx.AsyncClient, repo_full_name: str) -> int:
        """Get commit activity for last 30 days."""
        resp = await client.get(
            f"{GITHUB_API_BASE}/repos/{repo_full_name}/stats/commit_activity"
        )
        if resp.status_code != 200:
            return 0
        weeks = resp.json()
        if not isinstance(weeks, list):
            return 0
        # Last 4 weeks = ~30 days
        recent = weeks[-4:] if len(weeks) >= 4 else weeks
        return sum(w.get("total", 0) for w in recent)
