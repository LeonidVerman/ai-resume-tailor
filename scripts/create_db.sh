#!/usr/bin/env bash
# scripts/create_db.sh
#
# Create the PostgreSQL database and user for local development.
#
# Usage:
#   bash scripts/create_db.sh                    # use defaults from .env
#   POSTGRES_HOST=localhost bash scripts/create_db.sh
#
# Prerequisites:
#   - PostgreSQL running (docker-compose up postgres -d  OR  local install)
#   - psql available on PATH (or run via docker exec)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

PGHOST="${POSTGRES_HOST:-localhost}"
PGPORT="${POSTGRES_PORT:-5432}"
PGUSER="${POSTGRES_USER:-tailor}"
PGPASSWORD="${POSTGRES_PASSWORD:-tailor}"
PGDB="${POSTGRES_DB:-tailor_dev}"

export PGPASSWORD

echo "[create_db] Host:     $PGHOST:$PGPORT"
echo "[create_db] User:     $PGUSER"
echo "[create_db] Database: $PGDB"
echo ""

# Try connecting via docker exec first (works even without local psql)
if docker compose ps postgres 2>/dev/null | grep -q "running\|Up"; then
  echo "[create_db] Using docker exec to run psql inside postgres container..."
  docker compose exec -T postgres psql -U "$PGUSER" -tc \
    "SELECT 1 FROM pg_database WHERE datname = '$PGDB'" \
    | grep -q 1 && {
      echo "[create_db] Database '$PGDB' already exists."
    } || {
      docker compose exec -T postgres psql -U "$PGUSER" \
        -c "CREATE DATABASE $PGDB;"
      echo "[create_db] Database '$PGDB' created."
    }
elif command -v psql &>/dev/null; then
  echo "[create_db] Using local psql..."
  psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -tc \
    "SELECT 1 FROM pg_database WHERE datname = '$PGDB'" \
    | grep -q 1 && {
      echo "[create_db] Database '$PGDB' already exists."
    } || {
      psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" \
        -c "CREATE DATABASE $PGDB;"
      echo "[create_db] Database '$PGDB' created."
    }
else
  echo "[create_db] ERROR: Neither docker compose postgres nor local psql found."
  echo "  Start postgres first:  docker-compose up postgres -d"
  exit 1
fi

echo ""
echo "[create_db] Done. Next step: bash scripts/run_migrations.sh"
