"""Tests for GitHub enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.github_enricher import GitHubEnricher, _parse_github_org
from src.models import Project


def test_parse_github_org_standard():
    assert _parse_github_org("https://github.com/uniswap") == "uniswap"
    assert _parse_github_org("https://github.com/Uniswap/v3-core") == "Uniswap"


def test_parse_github_org_bare_name():
    assert _parse_github_org("Uniswap") == "Uniswap"
    assert _parse_github_org("NexusMutual") == "NexusMutual"


def test_parse_github_org_none():
    assert _parse_github_org("") is None
    assert _parse_github_org("https://example.com") is None
    assert _parse_github_org(None) is None


@pytest.mark.asyncio
async def test_enriches_project_with_github_data(db_session):
    proj = Project(
        name="TestProj", slug="testproj", github="https://github.com/testorg"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_repos = [
        {"full_name": "testorg/repo1", "stargazers_count": 500},
        {"full_name": "testorg/repo2", "stargazers_count": 200},
    ]

    # Mock responses
    repos_resp = MagicMock()
    repos_resp.status_code = 200
    repos_resp.json.return_value = mock_repos

    contributors_resp = MagicMock()
    contributors_resp.status_code = 200
    contributors_resp.json.return_value = [{"id": 1}, {"id": 2}]
    contributors_resp.headers = {}

    commits_resp = MagicMock()
    commits_resp.status_code = 200
    commits_resp.json.return_value = [
        {"total": 10}, {"total": 15}, {"total": 20}, {"total": 25},
    ]

    async def mock_get(url, **kwargs):
        if "/orgs/" in url and "/repos" in url:
            return repos_resp
        if "/contributors" in url:
            return contributors_resp
        if "/stats/commit_activity" in url:
            return commits_resp
        not_found = MagicMock()
        not_found.status_code = 404
        return not_found

    with patch("src.collectors.github_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = GitHubEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.github_org == "testorg"
    assert proj.github_stars == 700  # 500 + 200
    assert proj.github_contributors == 4  # 2 per repo
    assert proj.github_commits_30d == 140  # (10+15+20+25) * 2 repos


@pytest.mark.asyncio
async def test_skips_projects_without_github(db_session):
    proj = Project(name="NoGH", slug="no-gh")
    db_session.add(proj)
    await db_session.flush()

    enricher = GitHubEnricher()
    result = await enricher.enrich(db_session)

    assert result.records_updated == 0
    assert proj.github_stars is None
