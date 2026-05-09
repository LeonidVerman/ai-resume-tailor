#!/usr/bin/env bash
# tests/grade_layout.sh
#
# Grade DOCX layout preservation for all matched samples.
# Renders missing artefacts via the rendering pipeline, then
# produces per-sample + aggregate reports.
#
# Usage:
#   tests/grade_layout.sh                         # grade all samples
#   tests/grade_layout.sh 31                      # grade sample with prefix 31
#   tests/grade_layout.sh 1-Leonid                # match by filename fragment
#   tests/grade_layout.sh --baseline path/to/aggregate.json
#   tests/grade_layout.sh --no-render             # skip rendering step
#   tests/grade_layout.sh --pdf-method local      # use built-in PDF converter
#
# Output:
#   tmp/artefacts/layout_grading/<stem>_grade.json  per-sample grade
#   tmp/artefacts/layout_grading/aggregate.json     aggregate metrics
#   tmp/artefacts/layout_grading/summary.txt        human-readable summary

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$REPO_ROOT"
PYTHONPATH="$REPO_ROOT/src" python "$SCRIPT_DIR/grade_layout.py" "$@"
