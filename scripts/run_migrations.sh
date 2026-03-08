#!/usr/bin/env bash
# scripts/run_migrations.sh
#
# Run Alembic database migrations for the backend.
#
# Status: PLACEHOLDER — database not yet implemented.
#         This script will be implemented in Phase 3 of the task plan.
#
# Future usage:
#   bash scripts/run_migrations.sh              # upgrade to head
#   bash scripts/run_migrations.sh downgrade -1 # downgrade one step
#
# Prerequisites (future):
#   - PostgreSQL running and DATABASE_URL set in backend/.env
#   - pip install -e backend/

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "$0")/../backend" && pwd)"
ACTION="${1:-upgrade head}"

echo "[run_migrations] Migrations not yet implemented (Phase 3)."
echo "  Spec: doc/IMPLEMENTATION_TASK_PLAN.md (Task 14, Task 23)"
echo ""
echo "  Future command:"
echo "    cd backend && alembic $ACTION"
