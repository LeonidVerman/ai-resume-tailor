#!/usr/bin/env bash
# Run calibration-with-data mode for ai-resume-tailor
# Sends pre-generated sample resume/cover letter files to assessment.
# Usage: ./run_calibrate_samples.sh [--positions FILE] [--model MODEL] [--temperature T] [--workers N] [--max_positions N] [--out DIR] [--cache_dir DIR]
# Defaults: positions=tests/data/positions.txt, calibrate-data=tests/samples, model=gpt-5.2, temperature=0.5

set -euo pipefail

# Defaults
POSITIONS="tests/data/positions.txt"
CALIBRATE_DATA="tests/samples"
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
    --calibrate-data "$CALIBRATE_DATA"
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
