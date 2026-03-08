#!/usr/bin/env bash
# scripts/dev_frontend.sh
#
# Start the Next.js frontend in development mode.
#
# Status: PLACEHOLDER — frontend not yet implemented beyond skeleton.
#         This script will be fully implemented in Phase 9 of the task plan.
#
# Usage (once dependencies are installed):
#   bash scripts/dev_frontend.sh

set -euo pipefail

FRONTEND_DIR="$(cd "$(dirname "$0")/../frontend" && pwd)"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
  echo "[dev_frontend] Error: frontend/package.json not found."
  exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "[dev_frontend] Installing frontend dependencies..."
  cd "$FRONTEND_DIR" && npm install
fi

echo "[dev_frontend] Starting Next.js development server..."
cd "$FRONTEND_DIR" && npm run dev
