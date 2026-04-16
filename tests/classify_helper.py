"""
tests/classify_helper.py

Split the classify-file API response into separate output and input JSON files.

Usage:
    python classify_helper.py <response_json> <output_json> <input_json>

Arguments:
    response_json  Path to the raw API response (contains {"classification": ..., "llm_input": ...})
    output_json    Destination path for the classification JSON
    input_json     Destination path for the LLM input JSON
"""

import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <response_json> <output_json> <input_json>", file=sys.stderr)
        sys.exit(1)

    response_path, output_path, input_path = sys.argv[1], sys.argv[2], sys.argv[3]

    with open(response_path, encoding="utf-8") as f:
        data = json.load(f)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(input_path).parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data["classification"], f, indent=2, ensure_ascii=False)

    with open(input_path, "w", encoding="utf-8") as f:
        json.dump(data["llm_input"], f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
