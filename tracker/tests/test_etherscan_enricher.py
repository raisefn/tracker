"""Tests for Etherscan token holder enricher."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.collectors.etherscan_enricher import EtherscanEnricher
from src.models import Project


@pytest.mark.asyncio
async def test_enriches_with_holder_count(db_session):
    proj = Project(
        name="Uniswap", slug="uniswap",
        token_contract="0x1f9840a85d5af5bf1d1762f925bdaddc4201f984"
    )
    db_session.add(proj)
    await db_session.flush()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "1",
        "result": [{"holdersCount": "385000"}],
    }

    async def mock_get(url, **kwargs):
        return mock_resp

    with patch("src.collectors.etherscan_enricher.httpx.AsyncClient") as mock_cls:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_cls.return_value = mock_client

        with patch("src.collectors.etherscan_enricher.settings") as mock_settings:
            mock_settings.etherscan_api_key = "test-key"

            enricher = EtherscanEnricher()
            result = await enricher.enrich(db_session)

    assert result.records_updated == 1
    assert proj.token_holder_count == 385000


@pytest.mark.asyncio
async def test_skips_without_api_key(db_session):
    proj = Project(
        name="Test", slug="test",
        token_contract="0xabc"
    )
    db_session.add(proj)
    await db_session.flush()

    with patch("src.collectors.etherscan_enricher.settings") as mock_settings:
        mock_settings.etherscan_api_key = ""

        enricher = EtherscanEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 0


@pytest.mark.asyncio
async def test_skips_without_token_contract(db_session):
    proj = Project(name="NoContract", slug="no-contract")
    db_session.add(proj)
    await db_session.flush()

    with patch("src.collectors.etherscan_enricher.settings") as mock_settings:
        mock_settings.etherscan_api_key = "test-key"

        enricher = EtherscanEnricher()
        result = await enricher.enrich(db_session)

    assert result.records_updated == 0
