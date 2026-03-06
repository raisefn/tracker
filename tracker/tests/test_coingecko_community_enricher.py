"""Tests for CoinGecko community enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.coingecko_community_enricher import CoinGeckoCommunityEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_with_community_data(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap", coingecko_id="uniswap"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "community_data": {
            "twitter_followers": 1200000,
            "telegram_channel_user_count": 50000,
        },
        "platforms": {
            "ethereum": "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984",
        },
    }

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.coingecko_community_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = CoinGeckoCommunityEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.twitter_followers == 1200000
    assert proj.telegram_members == 50000
    assert proj.token_contract == "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"


@pytest.mark.asyncio
async def test_skips_without_coingecko_id(db_session):
    proj = Project(name="NoCG", slug="no-cg")
    db_session.add(proj)
    await db_session.flush()

    enricher = CoinGeckoCommunityEnricher()
    result = await enricher.enrich(db_session)

    assert result.records_updated == 0
    assert proj.twitter_followers is None
