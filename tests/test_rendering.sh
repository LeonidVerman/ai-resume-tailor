#!/usr/bin/env bash
# tests/test_rendering.sh
#
# Run the deterministic rendering test for matched (classification + generation) pairs,
# then automatically grade layout preservation for the same sample(s),
# then run cross-pipeline text equivalence comparison.
#
# Usage (from repo root):
#   tests/test_rendering.sh               # render + grade + compare all matched samples
#   tests/test_rendering.sh 1             # render + grade + compare sample with numeric prefix 1
#   tests/test_rendering.sh 1-Leonid      # render + grade + compare sample matching filename fragment
#   tests/test_rendering.sh 2 3 4 14 25   # render + grade + compare specific samples
#
# Flags --docx-only, --pdf-only, --no-render suppress the cross-pipeline comparison.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

RENDER_SCRIPT="${SCRIPT_DIR}/rendering/render_samples.py"
COMPARE_SCRIPT="${SCRIPT_DIR}/rendering/compare_pipelines.py"

if [ ! -f "${RENDER_SCRIPT}" ]; then
    echo "ERROR: render script not found: ${RENDER_SCRIPT}" >&2
    exit 1
fi

cd "${REPO_ROOT}"

RENDER_EXIT=0
if [ $# -gt 0 ]; then
    python3 "${RENDER_SCRIPT}" "$@" || RENDER_EXIT=$?
else
    python3 "${RENDER_SCRIPT}" || RENDER_EXIT=$?
fi

echo ""
echo "============================================================"
echo "  grade_layout -- grading rendered artefacts"
echo "============================================================"
export PYTHONPATH="${REPO_ROOT}/src"
if [ $# -gt 0 ]; then
    python3 "${REPO_ROOT}/tests/grade_layout.py" --no-render "$@" || true
else
    python3 "${REPO_ROOT}/tests/grade_layout.py" --no-render || true
fi

# Detect flags that disable cross-pipeline comparison
SKIP_COMPARE=0
for arg in "$@"; do
    case "$arg" in
        --docx-only|--pdf-only|--no-render) SKIP_COMPARE=1 ;;
    esac
done

if [ "$SKIP_COMPARE" -eq 0 ]; then
    echo ""
    if [ $# -gt 0 ]; then
        python3 "${COMPARE_SCRIPT}" "$@" || true
    else
        python3 "${COMPARE_SCRIPT}" || true
    fi
fi

exit $RENDER_EXIT
