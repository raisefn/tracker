#!/bin/bash
# backup_db.sh — Create a compressed PostgreSQL backup of the raisefn database
#
# Usage: ./scripts/backup_db.sh
#
# Expects DATABASE_URL or RAISEFN_DATABASE_URL in the environment.
# Saves timestamped backups to ./backups/ and keeps only the last 7.

set -euo pipefail

PG_DUMP="/opt/homebrew/opt/libpq/bin/pg_dump"
BACKUP_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"
TIMESTAMP="$(date +%Y-%m-%d_%H%M%S)"
FILENAME="raisefn_${TIMESTAMP}.sql.gz"

# --- Resolve database URL ---
DB_URL="${DATABASE_URL:-${RAISEFN_DATABASE_URL:-}}"
if [ -z "$DB_URL" ]; then
  echo "Error: Set DATABASE_URL or RAISEFN_DATABASE_URL before running this script."
  exit 1
fi

# --- Check pg_dump exists ---
if [ ! -x "$PG_DUMP" ]; then
  echo "Error: pg_dump not found at $PG_DUMP"
  echo "Install it with: brew install libpq"
  exit 1
fi

# --- Create backup directory if needed ---
mkdir -p "$BACKUP_DIR"

# --- Run the backup ---
echo "Starting backup → ${FILENAME}"
if "$PG_DUMP" "$DB_URL" | gzip > "${BACKUP_DIR}/${FILENAME}"; then
  SIZE=$(du -h "${BACKUP_DIR}/${FILENAME}" | cut -f1)
  echo "Backup complete: ${BACKUP_DIR}/${FILENAME} (${SIZE})"
else
  echo "Error: Backup failed."
  rm -f "${BACKUP_DIR}/${FILENAME}"
  exit 1
fi

# --- Prune old backups (keep last 7) ---
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR"/raisefn_*.sql.gz 2>/dev/null | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt 7 ]; then
  REMOVE_COUNT=$((BACKUP_COUNT - 7))
  echo "Pruning ${REMOVE_COUNT} old backup(s)..."
  ls -1t "$BACKUP_DIR"/raisefn_*.sql.gz | tail -n "$REMOVE_COUNT" | xargs rm -f
fi

echo "Done. ${BACKUP_COUNT} backup(s) in ${BACKUP_DIR} (max 7 kept)."
