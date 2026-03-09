"""Tests for the normalization pipeline."""

from datetime import date

from src.collectors.base import RawRound
from src.pipeline.normalizer import make_slug, normalize_chains, normalize_round, normalize_sector


def test_make_slug_basic():
    assert make_slug("My Cool Project") == "my-cool-project"


def test_make_slug_special_chars():
    assert make_slug("Uniswap V3!") == "uniswap-v3"


def test_make_slug_max_length():
    long_name = "A" * 300
    slug = make_slug(long_name)
    assert len(slug) <= 200


def test_normalize_sector_known():
    assert normalize_sector("DeFi") == "defi"
    assert normalize_sector("Infra") == "infrastructure"
    assert normalize_sector("CEX") == "fintech"
    assert normalize_sector("DEX") == "defi"


def test_normalize_sector_defi_cefi():
    assert normalize_sector("DeFi & CeFi") == "defi"


def test_normalize_sector_unknown_passthrough():
    assert normalize_sector("metaverse") == "metaverse"


def test_normalize_sector_none():
    assert normalize_sector(None) is None


def test_normalize_sector_empty():
    assert normalize_sector("") is None


def test_normalize_chains_aliases():
    result = normalize_chains(["ETH", "BSC"])
    assert result == ["ethereum", "bnb"]


def test_normalize_chains_dedup():
    result = normalize_chains(["eth", "Ethereum"])
    assert result == ["ethereum"]


def test_normalize_chains_unknown_passthrough():
    result = normalize_chains(["fantom"])
    assert result == ["fantom"]


def test_normalize_chains_empty():
    result = normalize_chains([])
    assert result == []


def test_normalize_round_integration():
    raw = RawRound(
        project_name="Test",
        date=date(2024, 1, 1),
        sector="Infra",
        chains=["ETH", "SOL"],
        lead_investors=["a16z"],
    )
    result = normalize_round(raw)
    assert result.sector == "infrastructure"
    assert result.chains == ["ethereum", "solana"]
