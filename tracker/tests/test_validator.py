"""Tests for validation and confidence scoring."""

from datetime import date, timedelta

from src.collectors.base import RawRound
from src.pipeline.validator import compute_confidence, validate_round


def _make_round(**kwargs) -> RawRound:
    defaults = {
        "project_name": "TestProject",
        "date": date(2024, 6, 15),
        "amount_usd": 5_000_000,
        "lead_investors": ["Sequoia"],
        "other_investors": ["Paradigm"],
        "source_url": "https://example.com/news",
    }
    defaults.update(kwargs)
    return RawRound(**defaults)


def test_valid_round_no_failures():
    raw = _make_round()
    failures = validate_round(raw)
    assert failures == []


def test_amount_too_small():
    raw = _make_round(amount_usd=5_000)
    failures = validate_round(raw)
    assert any("too small" in f for f in failures)


def test_amount_too_large():
    raw = _make_round(amount_usd=11_000_000_000)
    failures = validate_round(raw)
    assert any("too large" in f for f in failures)


def test_amount_none_is_ok():
    raw = _make_round(amount_usd=None)
    failures = validate_round(raw)
    assert not any("Amount" in f for f in failures)


def test_future_date():
    raw = _make_round(date=date.today() + timedelta(days=7))
    failures = validate_round(raw)
    assert any("future" in f for f in failures)


def test_date_before_bitcoin():
    raw = _make_round(date=date(2008, 1, 1))
    failures = validate_round(raw)
    assert any("Bitcoin genesis" in f for f in failures)


def test_no_investors():
    raw = _make_round(lead_investors=[], other_investors=[])
    failures = validate_round(raw)
    assert any("No investors" in f for f in failures)


def test_missing_project_name():
    raw = _make_round(project_name="")
    failures = validate_round(raw)
    assert any("Missing project" in f for f in failures)


def test_confidence_defillama_base():
    raw = _make_round()
    score = compute_confidence(raw, "defillama", [])
    # base 0.5 + defillama 0.3 + lead 0.05 + amount 0.05 + source_url 0.05 - age penalty
    assert score > 0.8


def test_confidence_penalties_per_failure():
    raw = _make_round()
    score_0 = compute_confidence(raw, "defillama", [])
    score_2 = compute_confidence(raw, "defillama", ["fail1", "fail2"])
    assert score_0 - score_2 == pytest.approx(0.30, abs=0.01)


def test_confidence_clamped_to_range():
    raw = _make_round(lead_investors=[], other_investors=[], amount_usd=None, source_url=None)
    score = compute_confidence(raw, "community", ["a", "b", "c", "d", "e"])
    assert score >= 0.0
    assert score <= 1.0


import pytest
