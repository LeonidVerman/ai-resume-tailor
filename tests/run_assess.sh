#!/usr/bin/env bash
# Run assessment mode for ai-resume-tailor
# Usage: ./run_assess.sh [--positions FILE] [--model MODEL] [--temperature T] [--workers N] [--max_positions N] [--out DIR] [--cache_dir DIR]
# Defaults: positions=benchmark/positions.txt, model=gpt-5.2, temperature=0.5

set -euo pipefail

# Defaults
POSITIONS="benchmark/positions.txt"
MODEL="gpt-5.2"
TEMPERATURE="0.5"
WORKERS=""
MAX_POSITIONS=""
OUT="reports"
CACHE_DIR=""

# Change to repo root (script lives in tests/, repo root is one level up)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Build positional args array
ARGS=(
    --positions "$POSITIONS"
    --model "$MODEL"
    --temperature "$TEMPERATURE"
    --out "$OUT"
)

[ -n "$WORKERS" ]       && ARGS+=(--workers "$WORKERS")
[ -n "$MAX_POSITIONS" ] && ARGS+=(--max_positions "$MAX_POSITIONS")
[ -n "$CACHE_DIR" ]     && ARGS+=(--cache_dir "$CACHE_DIR")

# Append any extra CLI args passed directly to this script
ARGS+=("$@")

echo "Running: PYTHONPATH=src python -m tailor assess ${ARGS[*]}"
echo

PYTHONPATH=src python -m tailor assess "${ARGS[@]}"
