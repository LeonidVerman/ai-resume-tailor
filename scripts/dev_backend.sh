#!/usr/bin/env bash
# scripts/dev_backend.sh
#
# Start the FastAPI backend in development mode with hot reload.
#
# Status: PLACEHOLDER — backend not yet implemented.
#         This script will be implemented in Phase 2 of the task plan.
#
# Future usage:
#   bash scripts/dev_backend.sh
#
# Prerequisites (future):
#   - PostgreSQL running (see docker-compose.yml postgres service)
#   - backend/.env configured
#   - pip install -e backend/

set -euo pipefail

echo "[dev_backend] Backend not yet implemented (Phase 2)."
echo "  Spec: doc/IMPLEMENTATION_TASK_PLAN.md"
echo ""
echo "  Future command:"
echo "    cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
