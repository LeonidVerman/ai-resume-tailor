#!/usr/bin/env bash
# classify_samples.sh
#
# Run LLM classification against all resume samples and save JSON output.
#
# Optional env vars:
#   CLI_SECRET — X-Cli-Secret value (only needed when CLASSIFICATION_CLI_SECRET
#                is configured on the server; not required in local development)
#   API_URL    — defaults to http://localhost:8000
#
# Output:
#   tmp/artefacts/classification/docx/<basename>.json              classification
#   tmp/artefacts/classification/pdf/<basename>.json               classification
#   tmp/artefacts/classification/llm-input/docx/<basename>_input.json  LLM input
#   tmp/artefacts/classification/llm-input/pdf/<basename>_input.json   LLM input

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
CLI_SECRET="${CLI_SECRET:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

DOCX_INPUT="$SCRIPT_DIR/samples/resume/docx"
PDF_INPUT="$SCRIPT_DIR/samples/resume/pfd"
DOCX_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/docx"
PDF_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/pdf"
DOCX_INPUT_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/llm-input/docx"
PDF_INPUT_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/llm-input/pdf"

mkdir -p "$DOCX_OUTPUT" "$PDF_OUTPUT" "$DOCX_INPUT_OUTPUT" "$PDF_INPUT_OUTPUT"

classify_file() {
  local input_file="$1"
  local out_dir="$2"
  local input_dir="$3"
  local basename
  basename="$(basename "${input_file%.*}")"
  local out_file="$out_dir/$basename.json"
  local input_file_out="$input_dir/${basename}_input.json"
  local tmp_file
  tmp_file="$(mktemp)"

  printf "  %-60s → " "$(basename "$input_file")"

  local extra_headers=()
  if [ -n "$CLI_SECRET" ]; then
    extra_headers=(-H "X-Cli-Secret: $CLI_SECRET")
  fi

  http_code=$(curl -s -o "$tmp_file" -w "%{http_code}" \
    -X POST \
    "${extra_headers[@]}" \
    -F "file=@$input_file" \
    "$API_URL/api/v1/admin/classification/classify-file")

  if [ "$http_code" = "200" ]; then
    python3 "$SCRIPT_DIR/classify_helper.py" "$tmp_file" "$out_file" "$input_file_out"
    echo "OK"
  else
    body=$(cat "$tmp_file" 2>/dev/null || echo "(no body)")
    echo "FAIL (HTTP $http_code) $body"
  fi
  rm -f "$tmp_file"
}

echo "=== Classifying DOCX samples ==="
shopt -s nullglob
docx_files=("$DOCX_INPUT"/*.docx)
if [ ${#docx_files[@]} -eq 0 ]; then
  echo "  No .docx files found in $DOCX_INPUT"
else
  for f in "${docx_files[@]}"; do
    classify_file "$f" "$DOCX_OUTPUT" "$DOCX_INPUT_OUTPUT"
  done
fi

echo ""
echo "=== Classifying PDF samples ==="
pdf_files=("$PDF_INPUT"/*.pdf)
if [ ${#pdf_files[@]} -eq 0 ]; then
  echo "  No .pdf files found in $PDF_INPUT"
else
  for f in "${pdf_files[@]}"; do
    classify_file "$f" "$PDF_OUTPUT" "$PDF_INPUT_OUTPUT"
  done
fi

echo ""
echo "Done."
echo "  Classification: $REPO_ROOT/tmp/artefacts/classification/{docx,pdf}/"
echo "  LLM input:      $REPO_ROOT/tmp/artefacts/classification/llm-input/{docx,pdf}/"
