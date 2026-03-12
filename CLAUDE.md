# Tracker — Claude Context

## What This Is

raisefn/tracker is the open-source data collection and enrichment engine for raisefn. It collects crypto/startup fundraising data (rounds, investors, projects) and enriches records with profile data from 15+ sources.

- **Repo**: github.com/raisefn/tracker (public, MIT)
- **Stack**: Python 3.12, FastAPI, SQLAlchemy async + asyncpg, Alembic, BeautifulSoup
- **DB**: Postgres on Railway (internal URL: postgres.railway.internal)
- **Deploy**: Railway, auto-deploys on push to main
- **Local DB access**: `railway connect postgres` (requires `libpq` — installed at `/opt/homebrew/opt/libpq/bin/psql`)

## Database Schema (key tables)

- **projects** (~20K): Startups/protocols. Fields: name, slug, website, github_org, description, sector, chain, source_freshness (JSONB)
- **rounds** (~24K): Funding rounds. Fields: project_id, round_type, amount_usd, valuation_usd, date, source_type
- **investors** (~9K): VCs, angels, funds. Fields: name, slug, type, investor_category, description, hq_location, website, twitter, aum, sec_crd, sec_cik, ein, formd_appearances, formd_roles (JSONB), source_freshness (JSONB), last_enriched_at
- **round_investors** (~31K): Junction table. Fields: round_id, investor_id, is_lead
- **api_keys**: For brain API auth. Has `email` field (added 2026-03-12, migration 016)

Round types: pre_seed, seed, angel, series_a, series_b, series_c, series_d, series_e, grant, ido, ieo, ico, private_sale, strategic, undisclosed, other

~9,500 early-stage rounds (pre_seed + seed + series_a + angel).

## Enrichment Pipeline

### Architecture

All enrichers extend `BaseEnricher` in `src/collectors/enrichment_base.py`:
- `enrich(session) -> EnrichmentResult` — main method
- `source_name() -> str` — unique key for `source_freshness` tracking
- `stamp_freshness(record, source)` — marks a record as processed by a source (uses `flag_modified` for JSONB mutation detection)
- `find_investor_match(session, name, **identifiers)` — 4-step matching cascade: identifier match (CRD/CIK/EIN) → exact slug → normalized slug (strips 22 firm suffixes) → prefix match with length guard

### Investor Enrichers (11 total, added 2026-03-12)

**From existing data (no API calls):**
1. `investor_profile_aggregator` — Builds descriptions, categories, types from round participation data. Batches of 100. Daily.

**SEC/government sources:**
2. `formd_promoters` — Form D related persons frequency index. Creates NEW investor records for active angels (3+ appearances). Daily.
3. `sec_form_adv` — SEC Form ADV data (AUM, employee count, CRD). Enrichment-only (no new records). Weekly.
4. `sec_13f` — SEC 13F holdings data (AUM from institutional holdings). Enrichment-only. Weekly.
5. `propublica_990` — IRS 990 data for foundations/endowments (assets, grants). Enrichment-only. Weekly.

**Web scraping:**
6. `web_search_enricher` — DuckDuckGo HTML search for investor profiles. Extracts website, LinkedIn, Twitter, description, location. 50/run, 2s delay. Daily.
7. `angellist_enricher` — Wellfound profile scraping. 3-strategy discovery: /people/{slug}, /company/{slug}, DDG fallback. 50/run, 3s delay. Weekly.
8. `crunchbase_enricher` — Crunchbase public profile scraping. JSON-LD parsing. 40/run, 3s delay. Weekly.
9. `angel_group_scraper` — ACA directory + Gust directory + hardcoded top angel groups. Creates new Investor records. Weekly.
10. `vc_website_enricher` — Scrapes VC firm websites for team/portfolio/thesis. 30/run, 2s delay. Weekly.
11. `twitter_bio_enricher` — Phase 1: enrich existing handles via Nitter. Phase 2: discover new handles via DDG. 20 discoveries/run. Daily.

### Scheduler Tiers (`src/scheduler.py`)

- **Realtime (15min)**: RSS feeds, Google News
- **Hourly**: SEC EDGAR recent, HackerNews, Reddit
- **Daily**: Linkers (website, coingecko, snapshot) → Collectors (defillama, YC) → Enrichers (github, npm, pypi, producthunt, formd, defillama protocols, coingecko chain, etherscan, snapshot, twitter_bio, web_search, investor_profile_aggregator)
- **Weekly**: 500 Global, SEC (form_adv, 13f, 990, edgar_bulk), wellfound, angellist, crunchbase, angel_groups, vc_website
- **Startup**: Runs daily + weekly ticks immediately on deploy (don't wait 24h)

### Key Patterns

- All web scrapers have rate limiting (2-3s between requests) and graceful 429/403 handling
- DuckDuckGo HTML search (`https://html.duckduckgo.com/html/`) is used as a free search API by multiple enrichers
- `source_freshness` JSONB column tracks which enrichers have processed each record — prevents re-processing
- Enrichers only update NULL/empty fields (never overwrite existing data)
- `JOB_TIMEOUT = 30 * 60` (30 min) per job prevents one slow job from blocking

## Project Collectors (15 total)

RSS feeds, Google News, SEC EDGAR (recent + bulk), HackerNews, DefiLlama, YC Directory, Product Hunt, 500 Global. Plus enrichers for GitHub, npm, PyPI, CoinGecko, Etherscan, Snapshot, Reddit.

## Related: Brain Repo (closed source)

The brain at `/Users/justinpetsche/brain` is the intelligence API that queries tracker data. It has 6 endpoints: qualify_raise, match_investors, read_signal, plan_outreach, analyze_terms, analyze_narrative. Plus a `/chat` endpoint with Claude tool_use.

### Raise Companion System (NOT YET DEPLOYED)

A plan exists at `/Users/justinpetsche/.claude/plans/melodic-foraging-trinket.md` for a raise companion system — the core moat. Key idea: using the brain product IS updating the data. No forms, no manual logging. State advances automatically based on tool usage.

**Status**: Plan is complete but NO code has been written yet. Next steps:
1. Create 5 new models in brain repo: RaiseCampaign, RaiseInvestor, RaiseEvent, RaiseMetricsSnapshot, RaiseNotification
2. Set up Alembic in brain repo, create + run migrations
3. Build capture layer (raise_capture.py, raise_context.py)
4. Modify chat.py for raise awareness + update_pipeline/close_raise tools
5. Build notification system (email check-ins via Resend)

## Running Locally

```bash
# Install deps
pip install -e ".[dev]"

# DB access (Railway)
export PATH="/opt/homebrew/opt/libpq/bin:$PATH"
railway connect postgres

# Run scheduler
railway run python -m src.scheduler

# Run API
railway run uvicorn src.api:app --reload
```

## Deployment

Push to main → Railway auto-deploys. The scheduler runs as a worker process alongside the API.

## What Needs Monitoring

After the enricher deploy (2026-03-12):
- Check investor enrichment rate: `SELECT COUNT(*) FROM investors WHERE last_enriched_at IS NOT NULL`
- Check source coverage: `SELECT source_freshness::text, COUNT(*) FROM investors WHERE source_freshness IS NOT NULL GROUP BY 1 LIMIT 20`
- Watch for rate limiting in Railway logs (angellist, crunchbase, twitter enrichers most likely to hit limits)
- The investor_profile_aggregator should populate descriptions for most of the ~9K investors on first run since it only needs round data
