"""Tests for DefiLlama protocol enricher."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.defillama_enricher import DefiLlamaProtocolEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_matching_project(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap", sector="defi"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_protocols = [
        {
            "name": "Uniswap",
            "tvl": 5_000_000_000,
            "change_7d": 2.5,
            "gecko_id": "uniswap",
            "symbol": "UNI",
            "url": "https://uniswap.org",
            "twitter": "Uniswap",
            "description": "DEX protocol",
            "chains": ["Ethereum", "Polygon"],
        }
    ]

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = mock_protocols

    with patch("src.collectors.defillama_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = DefiLlamaProtocolEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.tvl == 5_000_000_000
    assert proj.tvl_change_7d == 2.5
    assert proj.coingecko_id == "uniswap"
    assert proj.token_symbol == "UNI"
    assert proj.description == "DEX protocol"
    assert proj.last_enriched_at is not None


@pytest.mark.asyncio
async def test_skips_unmatched_project(db_session):
    proj = Project(
        name=f"Unknown-{uuid.uuid4().hex[:8]}", slug=f"unknown-{uuid.uuid4().hex[:8]}"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [{"name": "SomethingElse", "tvl": 100}]

    with patch("src.collectors.defillama_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = DefiLlamaProtocolEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_skipped >= 1
    assert proj.tvl is None


@pytest.mark.asyncio
async def test_does_not_overwrite_existing_description(db_session):
    proj = Project(
        name="Aave", slug="aave", description="Existing description"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = [
        {"name": "Aave", "tvl": 1000, "description": "New description"}
    ]

    with patch("src.collectors.defillama_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        enricher = DefiLlamaProtocolEnricher()
        await enricher.enrich(db_session)

    assert proj.description == "Existing description"
