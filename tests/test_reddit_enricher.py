"""Tests for Reddit enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.reddit_enricher import RedditEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_project_with_reddit_data(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap", reddit_subreddit="uniswap"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "subscribers": 150000,
            "accounts_active": 500,
        }
    }

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.reddit_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = RedditEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.reddit_subscribers == 150000
    assert proj.reddit_active_users == 500


@pytest.mark.asyncio
async def test_skips_project_without_subreddit(db_session):
    proj = Project(name="NoReddit", slug="no-reddit")
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.reddit_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = RedditEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_skipped >= 1
    assert proj.reddit_subscribers is None
