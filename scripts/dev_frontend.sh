#!/usr/bin/env bash
# scripts/dev_frontend.sh
#
# Start the Next.js frontend in development mode.
#
# Usage:
#   bash scripts/dev_frontend.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"

if [ ! -f "$FRONTEND_DIR/package.json" ]; then
  echo "[dev_frontend] Error: frontend/package.json not found."
  exit 1
fi

if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
  echo "[dev_frontend] Installing frontend dependencies..."
  cd "$FRONTEND_DIR" && npm install
fi

echo "[dev_frontend] Starting Next.js development server on http://localhost:3000"
cd "$FRONTEND_DIR" && npm run dev
