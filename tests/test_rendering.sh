#!/usr/bin/env bash
# tests/test_rendering.sh
#
# Run the deterministic rendering test for matched (classification + generation) pairs.
#
# Usage (from repo root):
#   tests/test_rendering.sh           # render all matched samples
#   tests/test_rendering.sh 1         # render sample with numeric prefix 1
#   tests/test_rendering.sh 1-Leonid  # render sample matching the filename fragment
#
# The optional argument is forwarded to tests/rendering/render_samples.py.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RENDER_SCRIPT="${SCRIPT_DIR}/rendering/render_samples.py"

if [ ! -f "${RENDER_SCRIPT}" ]; then
    echo "ERROR: render script not found: ${RENDER_SCRIPT}" >&2
    exit 1
fi

cd "${REPO_ROOT}"

if [ $# -gt 0 ]; then
    python3 "${RENDER_SCRIPT}" "$@"
else
    python3 "${RENDER_SCRIPT}"
fi
