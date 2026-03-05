"""Tests for the DefiLlama collector."""

import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.collectors.defillama import DefiLlamaCollector, _normalize_round_type, _parse_amount


def test_normalize_round_type_known():
    assert _normalize_round_type("Series A") == "series_a"
    assert _normalize_round_type("pre-seed") == "pre_seed"
    assert _normalize_round_type("Seed") == "seed"


def test_normalize_round_type_unknown():
    assert _normalize_round_type("convertible note") == "convertible note"


def test_normalize_round_type_none():
    assert _normalize_round_type(None) is None


def test_normalize_round_type_empty():
    assert _normalize_round_type("") is None


def test_parse_amount_millions():
    assert _parse_amount(5.5) == 5_500_000
    assert _parse_amount(100) == 100_000_000


def test_parse_amount_none():
    assert _parse_amount(None) is None


def test_parse_amount_zero():
    assert _parse_amount(0) is None


def test_parse_amount_negative():
    assert _parse_amount(-5) is None


@pytest.mark.asyncio
async def test_collect_parses_raises():
    mock_data = {
        "raises": [
            {
                "name": "TestProject",
                "date": int(time.time()) - 86400,
                "round": "Seed",
                "amount": 10,
                "valuation": None,
                "leadInvestors": ["Sequoia"],
                "otherInvestors": ["Paradigm", "a16z"],
                "category": "DeFi",
                "categoryGroup": "DeFi & CeFi",
                "chains": ["Ethereum"],
                "source": "https://example.com",
            }
        ]
    }

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = AsyncMock()
    mock_response.json.return_value = mock_data

    with patch("src.collectors.defillama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        collector = DefiLlamaCollector()
        rounds = await collector.collect()

    assert len(rounds) == 1
    r = rounds[0]
    assert r.project_name == "TestProject"
    assert r.round_type == "seed"
    assert r.amount_usd == 10_000_000
    assert r.lead_investors == ["Sequoia"]
    assert r.other_investors == ["Paradigm", "a16z"]
    assert r.sector == "DeFi"
    assert r.source_url == "https://example.com"


@pytest.mark.asyncio
async def test_collect_skips_malformed():
    mock_data = {
        "raises": [
            {"name": "NoDate"},  # missing date, should be skipped
            {
                "name": "GoodProject",
                "date": int(time.time()),
                "leadInvestors": [],
                "otherInvestors": [],
            },
        ]
    }

    mock_response = AsyncMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = AsyncMock()
    mock_response.json.return_value = mock_data

    with patch("src.collectors.defillama.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        collector = DefiLlamaCollector()
        rounds = await collector.collect()

    assert len(rounds) == 1
    assert rounds[0].project_name == "GoodProject"


def test_source_type():
    collector = DefiLlamaCollector()
    assert collector.source_type() == "defillama"
