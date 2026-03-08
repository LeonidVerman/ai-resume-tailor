#!/usr/bin/env bash
# scripts/run_tests.sh
#
# Run the full test suite.
#
# Usage:
#   bash scripts/run_tests.sh              # run all tests
#   bash scripts/run_tests.sh backend      # run backend tests only
#   bash scripts/run_tests.sh cli          # run CLI generator tests only
#   bash scripts/run_tests.sh fast         # run fast tests (skip slow)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

TARGET="${1:-all}"

case "$TARGET" in
  backend)
    echo "[run_tests] Running backend tests..."
    cd backend && python -m pytest tests/ -v
    ;;
  cli)
    echo "[run_tests] Running CLI generator tests..."
    python -m pytest tests/ -v
    ;;
  fast)
    echo "[run_tests] Running fast tests (no slow markers)..."
    python -m pytest tests/ -v -m "not slow"
    ;;
  all|*)
    echo "[run_tests] Running all tests..."
    python -m pytest tests/ -v
    ;;
esac

echo "[run_tests] Done."
