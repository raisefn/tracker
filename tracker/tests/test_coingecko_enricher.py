"""Tests for CoinGecko enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.coingecko_enricher import CoinGeckoEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_project_with_market_data(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap", coingecko_id="uniswap"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_coin_data = {
        "symbol": "uni",
        "market_data": {
            "market_cap": {"usd": 5_000_000_000},
            "current_price": {"usd": 8.50},
        },
        "description": {"en": "Uniswap is a DEX protocol."},
        "links": {
            "twitter_screen_name": "Uniswap",
            "homepage": ["https://uniswap.org"],
        },
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = mock_coin_data

    with patch("src.collectors.coingecko_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = CoinGeckoEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.market_cap == 5_000_000_000
    assert proj.token_price_usd == 8.50
    assert proj.token_symbol == "UNI"


@pytest.mark.asyncio
async def test_skips_projects_without_coingecko_id(db_session):
    proj = Project(name="NoGecko", slug="no-gecko")
    db_session.add(proj)
    await db_session.flush()

    enricher = CoinGeckoEnricher()
    result = await enricher.enrich(db_session)

    assert result.records_updated == 0
    assert proj.market_cap is None


@pytest.mark.asyncio
async def test_handles_404_gracefully(db_session):
    proj = Project(name="Gone", slug="gone", coingecko_id="nonexistent-coin")
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("src.collectors.coingecko_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = CoinGeckoEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_skipped == 1
    assert result.records_updated == 0
