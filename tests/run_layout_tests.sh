#!/usr/bin/env bash
# Run format roundtrip tests with artefact saving enabled.
# Generated DOCX files land in tmp/artefacts/ for visual inspection.
# Can be invoked from any directory.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAVE_ARTEFACTS=1 python -m pytest "$SCRIPT_DIR/test_format_roundtrip.py" -v -s "$@"
