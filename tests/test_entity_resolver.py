"""Tests for investor entity resolution."""

from src.pipeline.entity_resolver import resolve_investor_name


def test_known_alias_a16z():
    assert resolve_investor_name("a16z") == "Andreessen Horowitz"
    assert resolve_investor_name("a16z crypto") == "Andreessen Horowitz"
    assert resolve_investor_name("a16z Crypto") == "Andreessen Horowitz"


def test_known_alias_paradigm():
    assert resolve_investor_name("Paradigm Fund") == "Paradigm"
    assert resolve_investor_name("Paradigm Operations") == "Paradigm"


def test_known_alias_sequoia():
    assert resolve_investor_name("Sequoia Capital") == "Sequoia"
    assert resolve_investor_name("Sequoia China") == "Sequoia"


def test_unknown_passthrough():
    assert resolve_investor_name("Random VC Fund") == "Random VC Fund"


def test_whitespace_stripping():
    assert resolve_investor_name("  Paradigm Fund  ") == "Paradigm"


def test_case_insensitive_match():
    # "a16Z" (capital Z) should still resolve via case-insensitive matching
    assert resolve_investor_name("a16Z") == "Andreessen Horowitz"
