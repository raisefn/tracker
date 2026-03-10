"""Tests for Phase 4: coverage expansion — news parser, new collectors, scheduler wiring."""

from datetime import date

import pytest

from src.collectors.news_parser import (
    RAISES_PATTERN,
    VALUATION_PATTERN,
    clean_company_name,
    extract_investors,
    extract_round_type,
    extract_valuation,
    parse_amount,
    parse_rss_date,
)


# --- News parser: amount parsing ---


def test_parse_amount_usd_millions():
    assert parse_amount("25", "million") == 25_000_000
    assert parse_amount("1.5", "m") == 1_500_000
    assert parse_amount("100", "mn") == 100_000_000


def test_parse_amount_usd_billions():
    assert parse_amount("2", "billion") == 2_000_000_000
    assert parse_amount("1.2", "bn") == 1_200_000_000
    assert parse_amount("3", "b") == 3_000_000_000


def test_parse_amount_eur():
    result = parse_amount("10", "million", currency="€")
    assert result == int(10_000_000 * 1.08)


def test_parse_amount_gbp():
    result = parse_amount("5", "million", currency="£")
    assert result == int(5_000_000 * 1.27)


def test_parse_amount_no_unit_assumes_millions():
    assert parse_amount("50", None) == 50_000_000


def test_parse_amount_invalid():
    assert parse_amount("abc", "million") is None


# --- News parser: round type extraction ---


def test_extract_round_type_standard():
    assert extract_round_type("Series A funding round") == "series_a"
    assert extract_round_type("raised a seed round") == "seed"
    assert extract_round_type("pre-seed investment") == "pre_seed"


def test_extract_round_type_late_stages():
    assert extract_round_type("Series F round") == "series_f"
    assert extract_round_type("Series G funding") == "series_g"
    assert extract_round_type("Series H mega-round") == "series_h"


def test_extract_round_type_new_types():
    assert extract_round_type("bridge round to extend runway") == "bridge"
    assert extract_round_type("growth equity investment") == "growth"
    assert extract_round_type("venture debt financing") == "debt"
    assert extract_round_type("Series A extension") == "series_a_ext"
    assert extract_round_type("Series B extension round") == "series_b_ext"


def test_extract_round_type_none():
    assert extract_round_type("company acquires rival") is None


# --- News parser: RAISES_PATTERN verbs ---


def test_raises_pattern_standard_verbs():
    m = RAISES_PATTERN.match("Acme Corp raises $50M in Series B")
    assert m is not None
    assert m.group(1).strip() == "Acme Corp"
    assert m.group(2) == "50"
    assert m.group(3).upper() == "M"


def test_raises_pattern_announces():
    m = RAISES_PATTERN.match("Stripe announces $100M funding round")
    assert m is not None
    assert m.group(1).strip() == "Stripe"


def test_raises_pattern_completes():
    m = RAISES_PATTERN.match("DataBricks completes $500M Series B")
    assert m is not None
    assert m.group(1).strip() == "DataBricks"


def test_raises_pattern_unveils():
    m = RAISES_PATTERN.match("NewCo unveils $20M seed round")
    assert m is not None
    assert m.group(1).strip() == "NewCo"


# --- News parser: company name cleaning ---


def test_clean_company_name_strips_prefixes():
    assert clean_company_name("Exclusive: Acme Corp") == "Acme Corp"
    assert clean_company_name("Breaking: FooBar") == "FooBar"
    assert clean_company_name("AI startup DeepTech") == "DeepTech"
    assert clean_company_name("Crypto startup ChainCo") == "ChainCo"
    assert clean_company_name("Startup Genome") == "Genome"


def test_clean_company_name_strips_punctuation():
    assert clean_company_name("'Acme Corp'") == "Acme Corp"
    assert clean_company_name('"FooCo"') == "FooCo"


# --- News parser: investor extraction ---


def test_extract_investors_led_by():
    leads, others = extract_investors("Acme raises $10M led by Sequoia Capital")
    assert "Sequoia Capital" in leads


def test_extract_investors_with_participation():
    leads, others = extract_investors(
        "Acme raises $10M led by a16z with participation from Tiger Global, SoftBank"
    )
    assert "a16z" in leads
    assert "Tiger Global" in others or "SoftBank" in others


# --- News parser: valuation extraction ---


def test_extract_valuation_billion():
    v = extract_valuation("Stripe raises $600M at a $50B valuation")
    assert v == 50_000_000_000


def test_extract_valuation_million():
    v = extract_valuation("FooCo valued at $500M")
    assert v == 500_000_000


def test_extract_valuation_none():
    v = extract_valuation("Acme raises $10M seed round")
    assert v is None


# --- News parser: date parsing ---


def test_parse_rss_date_rfc2822():
    d = parse_rss_date("Mon, 15 Jan 2024 12:00:00 GMT")
    assert d == date(2024, 1, 15)


def test_parse_rss_date_iso():
    d = parse_rss_date("2024-06-15T10:00:00Z")
    assert d == date(2024, 6, 15)


def test_parse_rss_date_invalid():
    assert parse_rss_date("not a date") is None


# --- RSS feeds count ---


def test_rss_feeds_expanded():
    from src.collectors.rss_funding import RSS_FEEDS

    assert len(RSS_FEEDS) >= 10


# --- Google News collector ---


def test_google_news_parse_article():
    from src.collectors.google_news import GoogleNewsFundingCollector

    collector = GoogleNewsFundingCollector()
    result = collector._parse_article(
        title="Acme Corp raises $30M Series A led by Sequoia - TechCrunch",
        description="Acme Corp announced a $30M Series A round.",
        link="https://example.com/article",
        pub_date="Mon, 10 Mar 2025 08:00:00 GMT",
    )
    assert result is not None
    assert result.project_name == "Acme Corp"
    assert result.amount_usd == 30_000_000
    assert result.round_type == "series_a"


def test_google_news_strips_source_suffix():
    from src.collectors.google_news import GoogleNewsFundingCollector

    collector = GoogleNewsFundingCollector()
    result = collector._parse_article(
        title="FooCo raises $5M seed round - Bloomberg",
        description="",
        link="https://example.com/foo",
        pub_date="",
    )
    assert result is not None
    assert result.project_name == "FooCo"


def test_google_news_no_match():
    from src.collectors.google_news import GoogleNewsFundingCollector

    collector = GoogleNewsFundingCollector()
    result = collector._parse_article(
        title="Apple releases new iPhone",
        description="No funding here.",
        link="https://example.com/apple",
        pub_date="",
    )
    assert result is None


def test_google_news_search_queries():
    from src.collectors.google_news import SEARCH_QUERIES

    assert len(SEARCH_QUERIES) >= 3
    assert any("raises" in q for q in SEARCH_QUERIES)


# --- PitchBook News collector ---


def test_pitchbook_parse_article():
    from src.collectors.pitchbook_news import PitchBookNewsCollector

    collector = PitchBookNewsCollector()
    result = collector._parse_article(
        title="HealthCo secures $75M Series C",
        description="HealthCo announced a $75M Series C round led by Flagship.",
        link="https://pitchbook.com/news/healthco",
        pub_date="Wed, 05 Feb 2025 10:00:00 GMT",
    )
    assert result is not None
    assert result.project_name == "HealthCo"
    assert result.amount_usd == 75_000_000
    assert result.round_type == "series_c"


# --- OpenVC collector ---


def test_openvc_parse_round():
    from src.collectors.openvc import OpenVCCollector

    collector = OpenVCCollector()
    result = collector._parse_round({
        "company_name": "TestCo",
        "amount": 5000000,
        "round_type": "Series A",
        "date": "2024-06-15",
        "investors": [
            {"name": "VC Fund", "is_lead": True},
            {"name": "Angel Group", "is_lead": False},
        ],
        "website": "https://testco.com",
        "id": "abc123",
    })
    assert result is not None
    assert result.project_name == "TestCo"
    assert result.amount_usd == 5_000_000
    assert result.round_type == "series_a"
    assert "VC Fund" in result.lead_investors
    assert "Angel Group" in result.other_investors


def test_openvc_parse_round_no_name():
    from src.collectors.openvc import OpenVCCollector

    collector = OpenVCCollector()
    result = collector._parse_round({"amount": 1000000})
    assert result is None


def test_openvc_parse_round_string_investors():
    from src.collectors.openvc import OpenVCCollector

    collector = OpenVCCollector()
    result = collector._parse_round({
        "company_name": "Foo",
        "investors": ["Investor A", "Investor B"],
    })
    assert result is not None
    assert "Investor A" in result.other_investors


# --- Wellfound enricher ---


def test_wellfound_extract_data():
    from unittest.mock import MagicMock

    from src.collectors.wellfound import WellfoundEnricher

    enricher = WellfoundEnricher()
    project = MagicMock()
    project.description = None
    project.team_size = None
    project.location = None

    html = '''
    <meta name="description" content="Acme builds cool stuff">
    <script type="application/ld+json">
    {"numberOfEmployees": "42", "address": {"addressLocality": "San Francisco"}}
    </script>
    '''

    updated = enricher._extract_data(project, html)
    assert updated is True
    assert project.description == "Acme builds cool stuff"
    assert project.team_size == 42
    assert project.location == "San Francisco"


def test_wellfound_extract_data_no_update():
    from unittest.mock import MagicMock

    from src.collectors.wellfound import WellfoundEnricher

    enricher = WellfoundEnricher()
    project = MagicMock()
    project.description = "Already set"
    project.team_size = 10
    project.location = "NYC"

    html = '<meta name="description" content="Something">'
    updated = enricher._extract_data(project, html)
    assert updated is False


# --- Backfill EDGAR script ---


def test_backfill_get_quarters():
    from scripts.backfill_edgar import get_quarters

    quarters = get_quarters(2024, 2024)
    assert len(quarters) >= 1
    assert all(y == 2024 for y, q in quarters)
    assert all(1 <= q <= 4 for y, q in quarters)


def test_backfill_get_quarters_range():
    from scripts.backfill_edgar import get_quarters

    quarters = get_quarters(2023, 2024)
    assert len(quarters) >= 5  # At least 4 from 2023 + 1 from 2024
    assert quarters[0] == (2023, 1)


def test_backfill_get_quarters_skips_future():
    from scripts.backfill_edgar import get_quarters

    quarters = get_quarters(2030, 2030)
    assert len(quarters) == 0


# --- Scheduler wiring ---


def test_scheduler_imports_all_sources():
    """Verify scheduler module imports without errors and references all sources."""
    import src.scheduler as sched

    # Check key functions exist
    assert callable(sched.realtime_tick)
    assert callable(sched.hourly_tick)
    assert callable(sched.daily_tick)
    assert callable(sched.weekly_tick)

    # Check new Phase 4 sources are wired in
    source = __import__("inspect").getsource(sched)
    assert "GoogleNewsFundingCollector" in source
    assert "OpenVCCollector" in source
    assert "PitchBookNewsCollector" in source
    assert "WellfoundEnricher" in source
    assert "CoinGeckoEnricher" in source
    assert "CoinGeckoCommunityEnricher" in source
    assert "EtherscanEnricher" in source
    assert "SnapshotEnricher" in source
    assert "DefiLlamaProtocolEnricher" in source
    assert "SECEdgarBulkCollector" in source


def test_run_collectors_includes_new():
    """Verify run_collectors.py has new Phase 4 collectors."""
    from scripts.run_collectors import COLLECTORS

    assert "google_news" in COLLECTORS
    assert "openvc" in COLLECTORS
    assert "pitchbook_news" in COLLECTORS


def test_run_enrichers_includes_wellfound():
    """Verify run_enrichers.py has wellfound."""
    from scripts.run_enrichers import ENRICHERS

    assert "wellfound" in ENRICHERS
