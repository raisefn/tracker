"""Tests for Hacker News enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.hackernews_enricher import HackerNewsEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_project_with_hn_mentions(db_session):
    proj = Project(name="Celestia", slug="celestia")
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "hits": [
            {"title": "Celestia launches modular blockchain", "points": 150},
            {"title": "Why Celestia matters for rollups", "points": 85},
            {"title": "Unrelated article", "points": 50},  # won't match
        ],
        "nbHits": 3,
    }

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.hackernews_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = HackerNewsEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.hn_mentions_90d == 2  # only "Celestia" in title
    assert proj.hn_total_points == 235  # 150 + 85


@pytest.mark.asyncio
async def test_skips_project_with_no_mentions(db_session):
    proj = Project(name="ObscureProtocol", slug="obscure-protocol")
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"hits": [], "nbHits": 0}

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.hackernews_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = HackerNewsEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_skipped == 1
    assert proj.hn_mentions_90d is None
