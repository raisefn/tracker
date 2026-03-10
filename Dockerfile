FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

COPY alembic.ini .
COPY alembic/ alembic/
COPY scripts/ scripts/
RUN chmod +x scripts/start.sh

RUN adduser --disabled-password --gecos '' appuser
USER appuser

CMD ["scripts/start.sh"]
