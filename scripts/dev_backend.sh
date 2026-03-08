#!/usr/bin/env bash
# scripts/dev_backend.sh
#
# Start the FastAPI backend in development mode with hot reload.
#
# Usage:
#   bash scripts/dev_backend.sh
#
# Prerequisites:
#   - PostgreSQL running (docker-compose up postgres  OR  external DB)
#   - .env configured with DATABASE_URL and OPENAI_API_KEY
#   - pip install -e backend/

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Load .env if present
if [ -f "$REPO_ROOT/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$REPO_ROOT/.env"
  set +a
fi

cd "$REPO_ROOT"

echo "[dev_backend] Starting FastAPI backend on http://localhost:8000"
echo "[dev_backend] API docs: http://localhost:8000/docs"
echo "[dev_backend] Health:   http://localhost:8000/api/v1/health"
echo ""

uvicorn backend.app.main:app \
  --reload \
  --host 0.0.0.0 \
  --port 8000 \
  --log-level info
