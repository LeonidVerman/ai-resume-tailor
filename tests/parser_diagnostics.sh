#!/usr/bin/env bash
# tests/parser_diagnostics.sh
#
# Run parser diagnostics against llm-input JSON artefacts.
# Requires artefacts to exist — run parse_samples.sh first.
#
# Usage:
#   ./tests/parser_diagnostics.sh                     -- scan default dirs
#   ./tests/parser_diagnostics.sh --input path/to/dir -- custom input dir
#   ./tests/parser_diagnostics.sh --help              -- show all options

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
PYTHONPATH="$REPO_ROOT/src" python3 scripts/parser_diagnostics.py "$@"
