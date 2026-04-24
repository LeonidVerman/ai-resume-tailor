#!/usr/bin/env bash
# parse_samples.sh
#
# Run the DOCX/PDF parser locally and save the LLM-input JSON to disk.
# No backend server or LLM call required.
#
# Usage:
#   ./parse_samples.sh                                   -- parse all samples
#   ./parse_samples.sh samples/resume/docx/10-Template5.docx  -- single file
#
# Output:
#   tmp/artefacts/classification/llm-input/docx/<basename>_input.json
#   tmp/artefacts/classification/llm-input/pdf/<basename>_input.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
PARSE_HELPER="$SCRIPT_DIR/parse_helper.py"
DOCX_INPUT="$SCRIPT_DIR/samples/resume/docx"
PDF_INPUT="$SCRIPT_DIR/samples/resume/pfd"
DOCX_OUT="$REPO_ROOT/tmp/artefacts/classification/llm-input/docx"
PDF_OUT="$REPO_ROOT/tmp/artefacts/classification/llm-input/pdf"

mkdir -p "$DOCX_OUT" "$PDF_OUT"

parse_file() {
  local input_file="$1"
  local out_file="$2"
  printf "  %-60s → " "$(basename "$input_file")"
  if PYTHONPATH="$REPO_ROOT/src" python3 "$PARSE_HELPER" "$input_file" "$out_file"; then
    echo "OK"
  else
    echo "FAIL"
  fi
}

if [ $# -gt 0 ]; then
  input_file="$1"
  [[ "$input_file" != /* ]] && input_file="$SCRIPT_DIR/$input_file"
  ext="${input_file##*.}"
  [[ "${ext,,}" == "docx" ]] && out_dir="$DOCX_OUT" || out_dir="$PDF_OUT"
  basename_no_ext="$(basename "${input_file%.*}")"
  echo "Parsing: $input_file"
  parse_file "$input_file" "$out_dir/${basename_no_ext}_input.json"
  echo "Done.  LLM input: $out_dir/${basename_no_ext}_input.json"
  exit 0
fi

echo "=== Parsing DOCX samples ==="
shopt -s nullglob
docx_files=("$DOCX_INPUT"/*.docx)
[ ${#docx_files[@]} -eq 0 ] && echo "  No .docx files found" || {
  for f in "${docx_files[@]}"; do
    parse_file "$f" "$DOCX_OUT/$(basename "${f%.*}")_input.json"
  done
}

echo ""
echo "=== Parsing PDF samples ==="
pdf_files=("$PDF_INPUT"/*.pdf)
[ ${#pdf_files[@]} -eq 0 ] && echo "  No .pdf files found" || {
  for f in "${pdf_files[@]}"; do
    parse_file "$f" "$PDF_OUT/$(basename "${f%.*}")_input.json"
  done
}

echo ""
echo "Done.  LLM input: $REPO_ROOT/tmp/artefacts/classification/llm-input/"
