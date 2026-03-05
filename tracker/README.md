# raisefn/tracker

Open source crypto fundraising data tracker. Collects, normalizes, and serves funding round data via a REST API.

Part of [raisefn](https://raisefn.com) — the intelligence layer for crypto capital formation.

## What it does

- **Collects** funding round data from public sources (DefiLlama, more coming)
- **Normalizes** project names, investor entities, sectors, and chain labels
- **Scores** data quality with a confidence system (0.0–1.0)
- **Serves** clean data through a paginated, filterable REST API

## Quick start

```bash
# Clone and start
git clone https://github.com/raisefn/tracker.git
cd tracker
docker compose up -d

# Run database migrations
docker compose exec api alembic upgrade head

# Collect data
docker compose exec api python -m scripts.run_collectors

# API is live at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | System health + counts |
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

## Local development (without Docker)

```bash
# Requires Python 3.12+ and PostgreSQL 16
pip install -e ".[dev]"

# Set database URL
export RAISEFN_DATABASE_URL=postgresql+asyncpg://tracker:tracker@localhost:5432/tracker

# Run migrations
alembic upgrade head

# Start API
uvicorn src.api.app:app --reload

# Run collectors
python -m scripts.run_collectors
```

## Architecture

```
src/
├── api/            # FastAPI app, schemas, routes
├── collectors/     # Data source plugins (one file per source)
├── db/             # SQLAlchemy session setup
├── models/         # ORM models (Project, Investor, Round, etc.)
├── pipeline/       # Normalization, validation, entity resolution, ingestion
scripts/
└── run_collectors.py
```

### Adding a new collector

1. Create `src/collectors/your_source.py`
2. Extend `BaseCollector`, implement `collect()` and `source_type()`
3. Register in `scripts/run_collectors.py`

## License

MIT
