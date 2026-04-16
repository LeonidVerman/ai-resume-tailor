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
#   tmp/artefacts/classification/docx/<basename>.json  (from tests/samples/resume/docx/*.docx)
#   tmp/artefacts/classification/pdf/<basename>.json   (from tests/samples/resume/pfd/*.pdf)

set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
CLI_SECRET="${CLI_SECRET:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

DOCX_INPUT="$SCRIPT_DIR/samples/resume/docx"
PDF_INPUT="$SCRIPT_DIR/samples/resume/pfd"
DOCX_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/docx"
PDF_OUTPUT="$REPO_ROOT/tmp/artefacts/classification/pdf"

mkdir -p "$DOCX_OUTPUT" "$PDF_OUTPUT"

classify_file() {
  local input_file="$1"
  local output_dir="$2"
  local basename
  basename="$(basename "${input_file%.*}")"
  local out_file="$output_dir/$basename.json"

  printf "  %-60s → " "$(basename "$input_file")"

  local extra_headers=()
  if [ -n "$CLI_SECRET" ]; then
    extra_headers=(-H "X-Cli-Secret: $CLI_SECRET")
  fi

  http_code=$(curl -s -o "$out_file" -w "%{http_code}" \
    -X POST \
    "${extra_headers[@]}" \
    -F "file=@$input_file" \
    "$API_URL/api/v1/admin/classification/classify-file")

  if [ "$http_code" = "200" ]; then
    echo "OK  $out_file"
  else
    body=$(cat "$out_file" 2>/dev/null || echo "(no body)")
    echo "FAIL (HTTP $http_code) $body"
    rm -f "$out_file"
  fi
}

echo "=== Classifying DOCX samples ==="
shopt -s nullglob
docx_files=("$DOCX_INPUT"/*.docx)
if [ ${#docx_files[@]} -eq 0 ]; then
  echo "  No .docx files found in $DOCX_INPUT"
else
  for f in "${docx_files[@]}"; do
    classify_file "$f" "$DOCX_OUTPUT"
  done
fi

echo ""
echo "=== Classifying PDF samples ==="
pdf_files=("$PDF_INPUT"/*.pdf)
if [ ${#pdf_files[@]} -eq 0 ]; then
  echo "  No .pdf files found in $PDF_INPUT"
else
  for f in "${pdf_files[@]}"; do
    classify_file "$f" "$PDF_OUTPUT"
  done
fi

echo ""
echo "Done. Results saved to $REPO_ROOT/tmp/artefacts/classification/"
