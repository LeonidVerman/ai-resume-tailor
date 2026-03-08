#!/usr/bin/env bash
# scripts/run_migrations.sh
#
# Run Alembic database migrations for the backend.
#
# Usage:
#   bash scripts/run_migrations.sh              # upgrade to head (default)
#   bash scripts/run_migrations.sh downgrade -1 # downgrade one step
#   bash scripts/run_migrations.sh current      # show current revision
#
# Prerequisites:
#   - PostgreSQL running and DATABASE_URL set in .env or backend/.env
#   - pip install -e backend/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
elif [ -f "$REPO_ROOT/backend/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/backend/.env"
  set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "[run_migrations] ERROR: DATABASE_URL is not set."
  echo "  Set it in .env or export it before running this script."
  exit 1
fi

ACTION="${*:-upgrade head}"

echo "[run_migrations] Running: alembic $ACTION"
cd "$REPO_ROOT"
python -m alembic -c backend/alembic.ini $ACTION
echo "[run_migrations] Done."
