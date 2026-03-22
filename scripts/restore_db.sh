#!/bin/bash
# restore_db.sh — Restore a raisefn PostgreSQL backup
#
# Usage: ./scripts/restore_db.sh <backup_file>
#   e.g. ./scripts/restore_db.sh backups/raisefn_2026-03-21_120000.sql.gz
#
# Expects DATABASE_URL or RAISEFN_DATABASE_URL in the environment.

set -euo pipefail

PSQL="/opt/homebrew/opt/libpq/bin/psql"
BACKUP_DIR="$(cd "$(dirname "$0")/.." && pwd)/backups"

# --- Validate arguments ---
if [ $# -lt 1 ]; then
  echo "Usage: $0 <backup_file>"
  echo ""
  echo "Available backups:"
  ls -1t "$BACKUP_DIR"/raisefn_*.sql.gz 2>/dev/null || echo "  (none)"
  exit 1
fi

BACKUP_FILE="$1"

# Resolve relative paths against the backup directory
if [ ! -f "$BACKUP_FILE" ]; then
  BACKUP_FILE="${BACKUP_DIR}/${1}"
fi

if [ ! -f "$BACKUP_FILE" ]; then
  echo "Error: Backup file not found: $1"
  exit 1
fi

# --- Resolve database URL ---
DB_URL="${DATABASE_URL:-${RAISEFN_DATABASE_URL:-}}"
if [ -z "$DB_URL" ]; then
  echo "Error: Set DATABASE_URL or RAISEFN_DATABASE_URL before running this script."
  exit 1
fi

# --- Check psql exists ---
if [ ! -x "$PSQL" ]; then
  echo "Error: psql not found at $PSQL"
  echo "Install it with: brew install libpq"
  exit 1
fi

# --- Confirm before restoring ---
echo "WARNING: This will overwrite the current database with:"
echo "  ${BACKUP_FILE}"
echo ""
read -p "Are you sure? (y/N) " CONFIRM
if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
  echo "Restore cancelled."
  exit 0
fi

# --- Restore ---
echo "Restoring from ${BACKUP_FILE}..."
if gunzip -c "$BACKUP_FILE" | "$PSQL" "$DB_URL"; then
  echo "Restore complete."
else
  echo "Error: Restore failed."
  exit 1
fi
