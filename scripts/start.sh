#!/bin/bash
set -e

echo "Running database migrations..."
alembic upgrade head

echo "Starting scheduler in background..."
python -m scripts.run_scheduler &

echo "Starting uvicorn on port ${PORT:-8000}..."
exec uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}
