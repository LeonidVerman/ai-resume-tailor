#!/usr/bin/env bash
# generate_classification_report.sh
#
# Generate classification report from existing artefacts.
# Does NOT re-run classification or call the LLM.
#
# Usage:
#   ./generate_classification_report.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

python3 "$REPO_ROOT/scripts/generate_classification_report.py" "$@"
