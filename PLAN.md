# raisefn/tracker — Plan

## What is this?

An open source tool that collects, normalizes, and serves crypto fundraising data across the entire ecosystem. It answers: **who is funding what, how much, at what stage, on which chains, and who's leading?**

The dataset it produces is the data layer for raisefn — the fundraising intelligence platform for AI agents.

## Why build this?

1. No comprehensive, open, machine-readable crypto fundraising dataset exists
2. Building it teaches us the landscape — who the players are, what patterns emerge
3. Open source = credibility in the crypto community
4. The data becomes the foundation for the closed-source intelligence layer

## Where this fits in the raisefn platform

```
┌─────────────────────────────────────────────────────────────────┐
│                        raisefn platform                          │
│                                                                  │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │   tracker     │  │  intelligence    │  │    agent SDK     │  │
│  │  (open src)   │→ │  API (closed)    │← │   (open src)     │  │
│  │              │  │                  │  │                  │  │
│  │  raw data:   │  │  the brain:      │  │  plug & play:    │  │
│  │  rounds      │  │  /qualify        │  │  LangChain tool  │  │
│  │  investors   │  │  /match          │  │  CrewAI tool     │  │
│  │  projects    │  │  /signal         │  │  Claude tool     │  │
│  │  trends      │  │  /outreach       │  │  MCP server      │  │
│  │              │  │  /terms/analyze  │  │                  │  │
│  └──────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                  │
│  Future modules:                                                 │
│  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│  │  on-chain     │  │  news/social     │  │  benchmarks      │  │
│  │  analytics    │  │  intelligence    │  │  & comps engine   │  │
│  └──────────────┘  └──────────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Data Sources (ranked by priority)

### Primary — Free, structured, start here

| Source | What it gives us | Cost |
|---|---|---|
| **DefiLlama `/api/raises`** | Structured rounds: name, amount, stage, investors, category, date, chains. No API key needed. Covers entire crypto ecosystem. | Free |
| **SEC EDGAR Form D** | US-based crypto rounds with filing data. Public record. | Free |

### Secondary — News ingestion (requires parsing)

| Source | What it gives us | Cost |
|---|---|---|
| **CryptoPanic API** | Aggregated news feed, filterable. Detects new rounds from press. | Free tier (50-200 req/hr) |
| **CoinDesk / CoinTelegraph RSS** | Press releases announcing rounds. Need NLP to extract structured data. | Free |

### Future — Paid enrichment

| Source | What it gives us | Cost |
|---|---|---|
| **CryptoRank** | Richer round data, fund profiles | $228+/yr |
| **Messari** | Most comprehensive dataset (14K+ rounds) | $6K+/yr |
| **Crunchbase** | Cross-reference with traditional startup data | Paid |

### Community — Built over time

- GitHub PRs to add/correct round data (how DefiLlama itself works)
- Manual submissions via issues
- Bounties for data quality contributions

---

## Data Model

Designed for the tracker MVP but structured to support the intelligence layer and future modules without breaking changes.

### Core entities

```
Round {
  id                uuid (pk)
  project_id        uuid (fk → Project)
  round_type        enum (pre_seed | seed | series_a | series_b | series_c |
                          series_d | strategic | private | public | undisclosed)
  amount_usd        bigint?
  valuation_usd     bigint?
  date              date
  chains            text[]
  sector            text
  category          text?
  source_url        text
  source_type       enum (defillama | sec_edgar | news | community | manual)
  raw_data          jsonb          -- original payload from source, for reprocessing
  confidence        float (0-1)    -- data quality score
  created_at        timestamptz
  updated_at        timestamptz
}

Investor {
  id                uuid (pk)
  name              text (unique)
  slug              text (unique)   -- url-safe identifier
  type              enum (vc | angel | dao | corporate | fund_of_funds | other)
  website           text?
  twitter           text?
  description       text?
  hq_location       text?
  created_at        timestamptz
  updated_at        timestamptz

  -- computed (materialized or view):
  -- portfolio_count, total_deployed_usd, sectors[], chains[], avg_check_size
}

Project {
  id                uuid (pk)
  name              text
  slug              text (unique)
  website           text?
  twitter           text?
  github            text?
  description       text?
  sector            text
  chains            text[]
  status            enum (active | acquired | dead | unknown)
  founded_date      date?
  created_at        timestamptz
  updated_at        timestamptz

  -- computed:
  -- total_raised_usd, round_count, latest_round_date
}

RoundInvestor {
  round_id          uuid (fk → Round)
  investor_id       uuid (fk → Investor)
  is_lead           boolean default false
  created_at        timestamptz
}
```

### Why this structure scales

- **`raw_data` (jsonb)** — stores the original source payload so we can reprocess data as our normalization improves without re-fetching
- **`confidence` score** — lets the intelligence layer weight data quality when making recommendations
- **`RoundInvestor` join table** — properly models the many-to-many relationship, supports co-investment analysis and investor graph queries
- **`slug` fields** — stable URL-safe identifiers that won't break if names change
- **`enum` types** — consistent, queryable categories that the API layer can validate against
- **`status` on Project** — tracks outcomes, critical for the intelligence layer to learn what works

### Indexes (planned)

```sql
-- Primary query patterns
CREATE INDEX idx_rounds_date ON rounds (date DESC);
CREATE INDEX idx_rounds_sector ON rounds (sector);
CREATE INDEX idx_rounds_amount ON rounds (amount_usd) WHERE amount_usd IS NOT NULL;
CREATE INDEX idx_rounds_type ON rounds (round_type);
CREATE INDEX idx_rounds_chains ON rounds USING GIN (chains);
CREATE INDEX idx_rounds_project ON rounds (project_id);

-- Investor lookups
CREATE INDEX idx_investors_slug ON investors (slug);
CREATE INDEX idx_investors_type ON investors (type);

-- Project lookups
CREATE INDEX idx_projects_slug ON projects (slug);
CREATE INDEX idx_projects_sector ON projects (sector);
CREATE INDEX idx_projects_chains ON projects USING GIN (chains);

-- Join table for co-investment queries
CREATE INDEX idx_round_investors_investor ON round_investors (investor_id);
CREATE INDEX idx_round_investors_round ON round_investors (round_id);
CREATE INDEX idx_round_investors_lead ON round_investors (investor_id) WHERE is_lead = true;
```

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        Collector Layer                            │
│                                                                  │
│  Each collector is a standalone module with a common interface:  │
│  collect() → list[RawRound]                                     │
│                                                                  │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────┐ │
│  │ defillama  │ │   edgar    │ │    news    │ │  community   │ │
│  │ collector  │ │ collector  │ │ collector  │ │  collector   │ │
│  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └──────┬───────┘ │
└────────┼──────────────┼──────────────┼───────────────┼──────────┘
         │              │              │               │
         ▼              ▼              ▼               ▼
┌──────────────────────────────────────────────────────────────────┐
│                       Ingestion Pipeline                         │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │ validate │→ │normalize │→ │  dedup   │→ │ entity resolve │  │
│  │          │  │          │  │          │  │ (fuzzy match)  │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
│                                                                  │
│  Entity resolution: "a16z" = "Andreessen Horowitz" = "a16z      │
│  crypto" → single investor record. Critical for data quality.   │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                        Data Store                                │
│                                                                  │
│  PostgreSQL                                                      │
│  ├── rounds              (fundraising events)                    │
│  ├── investors           (VC firms, angels, DAOs)                │
│  ├── projects            (companies/protocols)                   │
│  ├── round_investors     (who participated in what)              │
│  └── collector_runs      (audit log: what ran, when, results)    │
│                                                                  │
│  Redis (future)                                                  │
│  ├── API response cache                                          │
│  └── rate limiting state                                         │
└──────────────────────┬───────────────────────────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────────────────────────┐
│                         API Layer                                │
│                                                                  │
│  FastAPI                                                         │
│  ├── /v1/rounds          (list, filter, paginate)               │
│  ├── /v1/investors       (list, filter, paginate)               │
│  ├── /v1/projects        (list, filter, paginate)               │
│  ├── /v1/stats           (aggregates, trends)                   │
│  ├── /v1/graph           (co-investment network) [future]       │
│  ├── /v1/export          (CSV/JSON bulk download) [future]      │
│  └── /health             (status, last collector run)            │
│                                                                  │
│  Middleware:                                                     │
│  ├── API key auth (optional for public, required for heavy use) │
│  ├── Rate limiting (token bucket per key)                        │
│  ├── Request logging (for usage analytics)                       │
│  └── CORS                                                        │
└──────────────────────────────────────────────────────────────────┘
```

### Collector interface (plugin pattern)

Every collector implements the same interface. Adding a new data source = adding one file.

```python
class BaseCollector(ABC):
    @abstractmethod
    async def collect(self) -> list[RawRound]:
        """Fetch rounds from source."""
        pass

    @abstractmethod
    def source_type(self) -> str:
        """e.g. 'defillama', 'sec_edgar'"""
        pass
```

### Stack

| Component | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Best for data work, fast to iterate |
| API framework | FastAPI | Async, auto-docs, type-safe |
| ORM | SQLAlchemy 2.0 | Async support, mature, flexible |
| Migrations | Alembic | Standard for SQLAlchemy |
| Database | PostgreSQL 16 | JSONB, GIN indexes, array types, proven at scale |
| Cache | Redis (future) | API caching, rate limiting |
| Task runner | GitHub Actions (MVP), Celery/Temporal (scale) | Scheduled collectors, pipeline orchestration |
| Containerization | Docker Compose (dev), Docker (prod) | Reproducible environments |
| CI/CD | GitHub Actions | Tests, linting, auto-deploy |
| Hosting (future) | Railway or Fly.io (MVP), AWS/GCP (scale) | Start cheap, migrate when needed |

### Project structure

```
tracker/
├── src/
│   ├── collectors/           # data source modules
│   │   ├── __init__.py
│   │   ├── base.py           # BaseCollector ABC
│   │   ├── defillama.py
│   │   ├── edgar.py
│   │   └── news.py
│   ├── pipeline/             # ingestion pipeline
│   │   ├── __init__.py
│   │   ├── normalizer.py
│   │   ├── dedup.py
│   │   ├── entity_resolver.py
│   │   └── validator.py
│   ├── models/               # SQLAlchemy models
│   │   ├── __init__.py
│   │   ├── round.py
│   │   ├── investor.py
│   │   ├── project.py
│   │   └── round_investor.py
│   ├── api/                  # FastAPI routes
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── routes/
│   │   │   ├── rounds.py
│   │   │   ├── investors.py
│   │   │   ├── projects.py
│   │   │   └── stats.py
│   │   ├── schemas.py        # Pydantic request/response models
│   │   ├── deps.py           # dependency injection
│   │   └── middleware.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── session.py
│   │   └── migrations/       # Alembic
│   └── config.py             # settings via pydantic-settings
├── tests/
│   ├── collectors/
│   ├── pipeline/
│   ├── api/
│   └── conftest.py
├── scripts/
│   └── run_collectors.py     # CLI entrypoint for collector runs
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── alembic.ini
├── .github/
│   └── workflows/
│       ├── ci.yml            # lint + test on PR
│       └── collect.yml       # scheduled daily collection
├── PLAN.md
├── README.md
└── LICENSE
```

---

## API Design

All endpoints return JSON. Paginated via `limit`/`offset`. Filterable. Versioned.

### Rounds

```
GET /v1/rounds
  ?sector=defi,infrastructure     # comma-separated, OR logic
  ?chain=ethereum,solana          # comma-separated, OR logic
  ?round_type=seed,series_a       # comma-separated, OR logic
  ?min_amount=1000000
  ?max_amount=50000000
  ?investor=a16z                  # filter by investor slug
  ?project=uniswap                # filter by project slug
  ?after=2024-01-01
  ?before=2025-12-31
  ?sort=date                      # date | amount | -date | -amount
  ?limit=50
  ?offset=0

GET /v1/rounds/:id

Response:
{
  "data": [{
    "id": "...",
    "project": { "id": "...", "name": "Uniswap", "slug": "uniswap" },
    "round_type": "series_b",
    "amount_usd": 165000000,
    "date": "2024-10-09",
    "lead_investors": [{ "id": "...", "name": "a16z", "slug": "a16z" }],
    "other_investors": [...],
    "sector": "defi",
    "chains": ["ethereum"],
    "source_url": "https://...",
    "confidence": 0.95
  }],
  "meta": {
    "total": 4521,
    "limit": 50,
    "offset": 0,
    "has_more": true
  }
}
```

### Investors

```
GET /v1/investors
  ?type=vc,dao
  ?sector=defi                    # investors active in this sector
  ?chain=ethereum
  ?min_rounds=5
  ?min_deployed=10000000
  ?sort=portfolio_count           # portfolio_count | total_deployed | -name
  ?limit=50
  ?offset=0

GET /v1/investors/:slug
GET /v1/investors/:slug/rounds
GET /v1/investors/:slug/co-investors   # who they invest alongside [future]
```

### Projects

```
GET /v1/projects
  ?sector=defi
  ?chain=ethereum
  ?status=active
  ?min_raised=1000000
  ?sort=total_raised
  ?limit=50
  ?offset=0

GET /v1/projects/:slug
GET /v1/projects/:slug/rounds
```

### Stats

```
GET /v1/stats/overview
  ?period=30d | 90d | 1y | all
  → total_rounds, total_capital, avg_round_size, median_round_size

GET /v1/stats/sectors
  ?period=90d
  → rounds_by_sector, capital_by_sector, trend_vs_prior_period

GET /v1/stats/investors
  ?period=90d
  → most_active_investors, biggest_deployers, new_entrants

GET /v1/stats/trends
  ?metric=round_count | total_capital | avg_size
  ?granularity=week | month | quarter
  ?sector=defi
  → time series data
```

### Health

```
GET /health
  → { "status": "ok", "last_collection": "2026-03-05T...", "round_count": 12483 }
```

---

## Expansion Points

Built into the architecture from day one so future modules don't require rewrites:

### 1. Intelligence API (raisefn/intelligence — closed source)

The brain that sits on top of the tracker data. Reads from the same Postgres. Adds:
- `POST /intel/qualify` — is this project ready to raise?
- `POST /intel/match` — which investors fit this project?
- `POST /intel/signal` — what does this investor behavior mean?
- `POST /intel/outreach` — craft approach for specific investor
- `POST /intel/terms` — analyze a term sheet against comps

**Expansion hook:** The tracker's `confidence` score, `raw_data` field, and entity-resolved investor graph are all designed to feed this layer.

### 2. Agent SDK (raisefn/sdk — open source)

Pre-built integrations so agent frameworks can use raisefn natively:
- LangChain tool definitions
- CrewAI tool definitions
- Claude/Anthropic tool use schemas
- MCP server
- OpenAI function calling schemas

**Expansion hook:** The API is already JSON-in/JSON-out with clear schemas — wrapping it for agent frameworks is straightforward.

### 3. On-chain analytics module

Enrich projects with on-chain data:
- TVL, transaction volume, active addresses
- Token performance post-raise
- Treasury wallet monitoring

**Expansion hook:** `Project.chains` field already tracks which chains a project operates on. Add an `on_chain_metrics` JSONB column or separate table.

### 4. News & social intelligence

Real-time signal detection from:
- Twitter/X (VC partner activity, founder announcements)
- Press releases (new rounds before they hit databases)
- Governance forums (DAO funding proposals)

**Expansion hook:** The `news` collector type and NLP pipeline are already in the architecture. The `source_type` enum is extensible.

### 5. Benchmarks & comps engine

"What's the average seed round for a DeFi project on Ethereum in 2025?"

**Expansion hook:** The `/v1/stats` endpoints are the seed. The data model already captures all dimensions needed for comp analysis (sector, chain, stage, date, amount).

### 6. Investor graph / network analysis

Map co-investment patterns, syndicate structures, who follows whom.

**Expansion hook:** The `RoundInvestor` join table + `is_lead` flag are specifically designed for this. Graph queries are just SQL joins on this table.

---

## MVP Scope (v0.1)

### In — ship this first

- [ ] Project structure and tooling (pyproject.toml, Docker, CI)
- [ ] PostgreSQL schema + Alembic migrations
- [ ] SQLAlchemy models (Round, Investor, Project, RoundInvestor)
- [ ] BaseCollector interface
- [ ] DefiLlama collector — pull all historical raises
- [ ] Ingestion pipeline — normalize, validate, dedup, entity resolution (basic)
- [ ] FastAPI with `/v1/rounds`, `/v1/investors`, `/v1/projects` endpoints
- [ ] Filtering, sorting, pagination on all list endpoints
- [ ] `/health` endpoint
- [ ] Docker Compose for local dev (API + Postgres)
- [ ] README with setup instructions and API docs
- [ ] GitHub Action to run collector daily
- [ ] Tests for collectors, pipeline, and API

### v0.2 — expand the data

- [ ] SEC EDGAR collector
- [ ] CryptoPanic / RSS news collector (basic)
- [ ] Improved entity resolution (fuzzy matching, alias tables)
- [ ] `/v1/stats/overview` and `/v1/stats/sectors` endpoints
- [ ] API key auth (optional)
- [ ] Rate limiting

### v0.3 — make it useful

- [ ] `/v1/stats/investors` and `/v1/stats/trends` endpoints
- [ ] Bulk export (CSV/JSON)
- [ ] Co-investor queries
- [ ] Webhook notifications for new rounds matching criteria
- [ ] Hosted public instance

### v1.0 — platform ready

- [ ] Intelligence API (closed source, separate repo)
- [ ] Agent SDK (open source, separate repo)
- [ ] MCP server
- [ ] On-chain enrichment
- [ ] News/social signal pipeline

---

## What success looks like

**Month 1:** Tracker running daily, DefiLlama data flowing, API live, README is good enough that a developer can set it up in 5 minutes.

**Month 3:** Multiple data sources, 10K+ rounds in the database, first community PRs, people starring the repo.

**Month 6:** Intelligence API in beta (closed), first agent developers testing it, early revenue from API calls.

**Month 12:** raisefn is the default data source for crypto fundraising. Agent SDKs in the wild. Revenue growing from the intelligence layer.

The tracker is the eyes. The intelligence layer is the brain. The agent SDK is the hands. Build the eyes first.

---

## Deep Dive: Intelligence API Response Design

The tracker is straightforward — it returns data. The intelligence API is where raisefn lives or dies. It needs to return **structured reasoning**, not just scores. Here's what good vs bad looks like for each endpoint.

### POST /intel/match — "Is this investor a good fit?"

**Bad response** (useless to an agent):
```json
{
  "match_score": 0.82,
  "recommendation": "Good fit"
}
```
An agent can't act on this. Why is it a good fit? What should it do next?

**Good response** (actionable):
```json
{
  "investor": { "slug": "paradigm", "name": "Paradigm" },
  "match_score": 0.82,
  "verdict": "strong_fit",

  "signals": {
    "thesis_alignment": {
      "score": 0.9,
      "evidence": [
        "Paradigm led 3 DeFi infrastructure rounds in the last 6 months",
        "Their partner X published a thesis on cross-chain protocols in Jan 2026"
      ]
    },
    "check_size_fit": {
      "score": 0.85,
      "evidence": [
        "Median seed check: $4M (your ask: $5M)",
        "Range in this sector: $2M-$8M"
      ]
    },
    "stage_fit": {
      "score": 0.95,
      "evidence": [
        "60% of Paradigm's 2025-2026 deals were seed/series_a",
        "They led 2 seed rounds in DeFi infra this quarter"
      ]
    },
    "timing": {
      "score": 0.6,
      "evidence": [
        "Last fund raised: 2024 ($750M). Estimated 40% deployed.",
        "Deployment pace suggests active but selective"
      ]
    },
    "risks": [
      "Paradigm has a portfolio company in adjacent space (CompetitorX)",
      "No known warm intro paths in your network"
    ]
  },

  "recommended_actions": [
    {
      "action": "outreach",
      "priority": "high",
      "suggested_angle": "Lead with cross-chain TVL growth metrics — aligns with their published thesis",
      "contact_path": "Partner X is most relevant. Check for shared connections via LinkedIn."
    }
  ],

  "comps": [
    {
      "project": "SimilarProtocol",
      "round": "seed",
      "amount": 4200000,
      "date": "2025-09-15",
      "note": "Paradigm led this round — similar sector and stage"
    }
  ],

  "confidence": 0.78,
  "confidence_factors": [
    "Fund deployment estimate is inferred, not confirmed (-0.1)",
    "Thesis alignment based on public sources (+0.9)",
    "No direct data on current portfolio conflicts (-0.05)"
  ]
}
```

**Why this works for agents:**
- Every score has **evidence** the agent can relay to the human founder
- **Risks** are explicit so the agent doesn't blindly pursue bad leads
- **Recommended actions** tell the agent what to do next, not just what to think
- **Comps** ground the recommendation in real deals
- **Confidence factors** explain what the system knows vs what it's guessing

### POST /intel/qualify — "Is this project ready to raise?"

```json
{
  "readiness_score": 0.65,
  "verdict": "not_yet",

  "assessment": {
    "strengths": [
      "Sector (DeFi infra) is seeing increased funding — up 35% QoQ",
      "Team has prior exits (signals to investors)",
      "Working product with $2M TVL"
    ],
    "gaps": [
      {
        "issue": "TVL below typical seed threshold for this sector",
        "benchmark": "Median TVL at seed for DeFi infra: $8M (you: $2M)",
        "suggestion": "Focus on TVL growth for 2-3 months before going out"
      },
      {
        "issue": "No notable angel investors yet",
        "benchmark": "80% of funded DeFi projects had at least 1 known angel pre-seed",
        "suggestion": "Target 2-3 angel checks to build social proof"
      }
    ],
    "market_timing": {
      "assessment": "favorable",
      "detail": "DeFi infra funding up 35% QoQ. 12 seed rounds closed this month in sector."
    }
  },

  "recommended_timeline": "2-3 months",
  "next_steps": [
    "Grow TVL to $5M+ (key investor threshold)",
    "Secure 2-3 angel investors for social proof",
    "Then target: Paradigm, a16z crypto, Polychain (best fit based on current data)"
  ]
}
```

### POST /intel/signal — "What does this investor behavior mean?"

```json
{
  "investor": "sequoia_crypto",
  "interaction": {
    "type": "email_reply",
    "response_time_hours": 72,
    "content_summary": "Asked for detailed unit economics"
  },

  "interpretation": {
    "interest_level": 0.55,
    "signal_type": "lukewarm_with_conditions",
    "reasoning": [
      "72-hour response time is slower than Sequoia's average for interested deals (median: 18h)",
      "Requesting unit economics at this stage suggests they're evaluating but not excited",
      "This pattern matches 'due diligence check' rather than 'eager pursuit'"
    ],
    "historical_pattern": {
      "similar_signals_in_data": 47,
      "conversion_rate": 0.12,
      "note": "12% of deals with this signal pattern resulted in a term sheet"
    }
  },

  "recommended_response": {
    "action": "respond_with_data",
    "urgency": "medium",
    "timing": "Within 24 hours — don't rush but don't delay",
    "strategy": "Send clean unit economics. Include a competitive signal if you have one (other investor interest). Don't over-explain — let the numbers speak.",
    "escalation": "If no response within 5 days, this is likely a soft pass. Move on."
  }
}
```

### Key design principle

Every intelligence response follows the same structure:
1. **Score** — a number the agent can use for ranking/filtering
2. **Verdict** — a human-readable label (strong_fit, not_yet, lukewarm)
3. **Evidence** — why, grounded in data from the tracker
4. **Risks/gaps** — what could go wrong
5. **Actions** — what to do next, specifically
6. **Confidence** — how much to trust this answer, and why

---

## Deep Dive: Data Quality & Confidence Scoring

Crypto fundraising data is notoriously unreliable. Rounds get announced but never close. Amounts are inflated. Investors are misattributed. If we don't handle this from day one, the intelligence layer is built on sand.

### Confidence score (0.0 — 1.0)

Every `Round` record gets a confidence score computed from:

```
confidence = base_score
  + source_bonus
  + corroboration_bonus
  - age_penalty
  - anomaly_penalty
```

| Factor | Score impact | Logic |
|---|---|---|
| **Source quality** | +0.1 to +0.3 | DefiLlama (curated) = +0.3, SEC filing = +0.3, news article = +0.1, community submission = +0.0 |
| **Multiple sources** | +0.1 per additional source | Same round confirmed by 2+ sources = higher confidence |
| **Amount consistency** | -0.1 to -0.3 | If sources disagree on amount by >20%, penalize |
| **Investor verification** | +0.05 per verified investor | Investor confirmed via their own portfolio page or press release |
| **Recency** | -0.05 per year old | Older data is less reliable (sources disappear, details fade) |
| **Anomaly flags** | -0.1 to -0.3 | Amount is 10x sector median? Suspiciously round number? Unknown investors only? |

### Entity resolution (critical from day one)

The same investor appears as:
- "a16z" / "a16z crypto" / "Andreessen Horowitz" / "a16z Crypto Fund III"
- "Paradigm" / "Paradigm Fund" / "Paradigm Operations"

If these aren't resolved to a single entity, co-investment analysis is garbage and investor profiles are fragmented.

**MVP approach (v0.1):**
```
investor_aliases table:
  alias           text (pk)     -- "a16z crypto"
  canonical_id    uuid (fk)     -- → investor.id for "Andreessen Horowitz"
  source          text          -- who added this alias
  confidence      float         -- how sure are we this is correct
```

- Seed with a curated alias list for top 200 crypto investors
- During ingestion, check every investor name against the alias table
- If no match, flag for manual review (GitHub issue auto-created)
- Over time, build fuzzy matching (Levenshtein distance + sector context)

**Scale approach (v0.3+):**
- ML-based entity resolution using name similarity + co-occurrence patterns
- "If investor X always appears in rounds alongside investor Y, and a new round has 'X Capital' alongside Y, 'X Capital' is probably X"
- Community-contributed alias PRs (with review process)

### Data validation rules

Every round passes through validation before being stored:

```python
VALIDATION_RULES = [
    # Amount sanity checks
    ("amount_range", lambda r: r.amount_usd is None or 10_000 <= r.amount_usd <= 10_000_000_000),
    ("amount_not_suspiciously_round", lambda r: not is_sus_round_number(r.amount_usd)),

    # Date sanity
    ("date_not_future", lambda r: r.date <= today()),
    ("date_not_ancient", lambda r: r.date >= date(2009, 1, 3)),  # Bitcoin genesis

    # Investor sanity
    ("has_at_least_one_investor", lambda r: len(r.investors) > 0),
    ("investors_not_all_unknown", lambda r: not all(i.name == "Unknown" for i in r.investors)),

    # Dedup
    ("not_duplicate", lambda r: not is_likely_duplicate(r)),
]
```

Rounds that fail validation get flagged, not dropped. They're stored with `confidence: 0.0` and a `validation_failures` JSONB field explaining what's wrong. This preserves data for manual review while keeping the API clean (default queries filter `confidence > 0.3`).

### Audit trail

```
collector_runs table:
  id              uuid
  collector       text          -- "defillama", "edgar"
  started_at      timestamptz
  completed_at    timestamptz
  rounds_fetched  int
  rounds_new      int
  rounds_updated  int
  rounds_flagged  int
  errors          jsonb
```

Every collector run is logged. If data quality degrades, we can trace it back to a specific run and source.

---

## Deep Dive: Feedback Loop & Learning

The intelligence layer needs to get smarter over time. Static rules and one-time analysis won't cut it. Here's how the architecture supports continuous improvement.

### The feedback loop

```
Agent calls /intel/match
  → raisefn returns recommendation
    → Agent acts on it (outreach, pass, etc.)
      → Outcome happens (term sheet, rejection, ghost)
        → Agent reports outcome via /intel/feedback
          → raisefn incorporates signal
            → Future /intel/match calls are better
```

### Feedback API (closed source, intelligence layer)

```
POST /intel/feedback
{
  "request_id": "orig-request-uuid",   // links back to the original /intel/ call
  "outcome": "term_sheet_received",     // enum: term_sheet | passed | ghosted | rejected | closed
  "outcome_details": {
    "time_to_outcome_days": 14,
    "amount_offered": 5000000,
    "valuation_offered": 40000000
  },
  "agent_notes": "Investor responded within 2 hours to initial outreach"  // optional
}
```

### What feedback enables

**1. Signal calibration**
- We said "72-hour response time from Sequoia = lukewarm, 12% conversion"
- Feedback data lets us validate: is it actually 12%? Or is it 8%? Or 20%?
- Over time, conversion predictions are grounded in real outcomes, not assumptions

**2. Investor behavior modeling**
- Track how each investor actually behaves vs. how we predict they behave
- "We predicted Paradigm was a strong fit (0.82). They passed. Why?"
- Patterns emerge: "Paradigm says no to projects with <$10M TVL despite thesis fit"
- These become hidden rules that improve future match scores

**3. Market timing intelligence**
- When agents report outcomes across hundreds of deals, we see macro patterns
- "Conversion rates are dropping across the board this month" = market cooling
- "DeFi infra deals are closing 2x faster than last quarter" = sector heating up
- This feeds directly into `/intel/qualify` market timing assessments

**4. Recommendation effectiveness**
- Did the suggested outreach angle work? Did the recommended contact path convert?
- A/B test different strategies across agents: "Lead with metrics" vs "Lead with narrative"
- Over time, outreach recommendations are backed by real conversion data

### Privacy and data isolation

Critical: agents are sending competitive intelligence. Architecture must guarantee:

- **Tenant isolation** — Agent A's feedback never leaks into Agent B's responses
- **Aggregate only** — Individual deal outcomes are never exposed. Only aggregate patterns ("12% conversion for this signal type") are used to improve the model
- **Opt-in feedback** — Agents choose whether to report outcomes. Better feedback = better intelligence (incentive alignment)
- **Anonymized learning** — When feedback improves the model, it's impossible to reverse-engineer which specific deal informed the improvement

### Data model for feedback

```
IntelRequest {
  id              uuid
  endpoint        text          -- "match", "qualify", "signal"
  input_hash      text          -- hashed input for dedup
  response        jsonb         -- what we returned
  scores          jsonb         -- key scores from the response
  api_key_id      uuid          -- which customer
  created_at      timestamptz
}

IntelFeedback {
  id              uuid
  request_id      uuid (fk → IntelRequest)
  outcome         enum (term_sheet | passed | ghosted | rejected | closed)
  outcome_details jsonb
  reported_at     timestamptz
}

-- Materialized view for model training (aggregated, anonymized)
IntelOutcomeStats {
  endpoint        text
  signal_type     text          -- e.g. "investor_match", "timing_signal"
  score_bucket    float         -- rounded to 0.1 (0.8, 0.9, etc.)
  outcome         text
  count           int
  conversion_rate float
  avg_days_to_outcome float
  last_updated    timestamptz
}
```

### How it gets smarter — concretely

**Month 1-3 (no feedback yet):**
Intelligence is rule-based + LLM-interpreted. Match scores come from static analysis of tracker data (investor history, sector overlap, check sizes). This is "informed guessing."

**Month 3-6 (early feedback):**
First agents report outcomes. Small sample sizes, but enough to calibrate the most confident predictions. "Our match score of 0.9 actually converts at 25%, while 0.5 converts at 3%." The scoring function gets its first real calibration.

**Month 6-12 (meaningful feedback):**
Hundreds of outcomes. Patterns emerge that no human would spot: "Projects that raise after exactly 2 angel rounds have 3x higher Series A conversion than those with 0 or 1." These become features in the scoring model.

**Year 2+ (flywheel):**
The feedback loop is the moat. Every agent interaction makes the system smarter. New entrants can copy the tracker data (it's open source) but they can't copy the outcome data. The intelligence layer's accuracy gap widens over time.

This is the real defensibility of raisefn — not the data, not the code, but the accumulated learning from thousands of real fundraising outcomes that no competitor can replicate without the same volume of agent interactions.
