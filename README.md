# raisefn/tracker

Open source crypto fundraising data tracker. Collects, normalizes, and serves funding round data via a REST API.

Part of [raisefn](https://raisefn.com) — the intelligence layer for crypto capital formation.

## What it does

- **Collects** funding round data from public sources (DefiLlama, more coming)
- **Normalizes** project names, investor entities, sectors, and chain labels
- **Scores** data quality with a confidence system (0.0–1.0)
- **Deduplicates** investors across lead/other roles per round
- **Caches** responses in Redis (5-min TTL, auto-invalidated after data collection)
- **Authenticates** API access with hashed API keys and tiered rate limiting
- **Serves** clean data through a paginated, filterable REST API

## Quick start

```bash
# Clone and start
git clone https://github.com/raisefn/tracker.git
cd tracker
docker compose up -d

# Run database migrations
docker compose exec api alembic upgrade head

# Create an API key
docker compose exec api python -m scripts.manage_keys create yourname free

# Collect data
docker compose exec api python -m scripts.run_collectors

# API is live at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Authentication

All endpoints except `/health` require an API key. Pass it via header or query param:

```bash
# Header (recommended)
curl -H "X-API-Key: rfn_yourkey" http://localhost:8000/v1/rounds

# Query param
curl http://localhost:8000/v1/rounds?api_key=rfn_yourkey
```

### Rate limits

| Tier | Requests/hour |
|------|---------------|
| free | 100 |
| basic | 1,000 |
| pro | 10,000 |

### Key management

```bash
# Create a key (prints the key once — save it)
docker compose exec api python -m scripts.manage_keys create <owner> [tier]

# List keys
docker compose exec api python -m scripts.manage_keys list

# Revoke a key by prefix
docker compose exec api python -m scripts.manage_keys revoke <prefix>
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | System health + counts (no auth) |
| `GET` | `/v1/rounds` | List rounds (filterable) |
| `GET` | `/v1/rounds/:id` | Single round detail |
| `GET` | `/v1/investors` | List investors |
| `GET` | `/v1/investors/:slug` | Investor detail |
| `GET` | `/v1/projects` | List projects |
| `GET` | `/v1/projects/:slug` | Project detail |

### Filtering rounds

```
GET /v1/rounds?sector=defi&chain=ethereum&min_amount=1000000&date_from=2024-01-01
```

Query params: `sector`, `chain`, `round_type`, `min_amount`, `max_amount`, `date_from`, `date_to`, `min_confidence`, `investor_slug`, `limit`, `offset`.

## Testing

```bash
# Run full test suite (66 tests)
docker compose exec api pytest --tb=short

# Run specific test file
docker compose exec api pytest tests/api/test_rounds.py
```

## Local development (without Docker)

```bash
# Requires Python 3.12+, PostgreSQL 16, Redis 7
pip install -e ".[dev]"

# Set env vars
export RAISEFN_DATABASE_URL=postgresql+asyncpg://tracker:tracker@localhost:5432/tracker
export RAISEFN_REDIS_URL=redis://localhost:6379/0

# Run migrations
alembic upgrade head

# Start API
uvicorn src.api.app:app --reload

# Run collectors
python -m scripts.run_collectors
```

## Deploy

The tracker runs as three services: API (Python), PostgreSQL, and Redis. Deploy to any platform that supports Docker or containers:

- **Railway**: `railway init`, add Postgres + Redis databases, `railway up`
- **Fly.io**: `fly launch`, add `fly postgres create` + `fly redis create`
- **Docker host**: Use the included `docker-compose.yml` on any VPS

Set these environment variables in production:

```
RAISEFN_DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/tracker
RAISEFN_REDIS_URL=redis://host:6379/0
RAISEFN_DEBUG=false
```

## Architecture

```
src/
├── api/            # FastAPI app, schemas, routes, auth, caching
├── collectors/     # Data source plugins (one file per source)
├── db/             # SQLAlchemy + Redis connection setup
├── models/         # ORM models (Project, Investor, Round, ApiKey, etc.)
├── pipeline/       # Normalization, validation, entity resolution, ingestion
scripts/
├── run_collectors.py
└── manage_keys.py
```

### Adding a new collector

1. Create `src/collectors/your_source.py`
2. Extend `BaseCollector`, implement `collect()` and `source_type()`
3. Register in `scripts/run_collectors.py`

## License

MIT
