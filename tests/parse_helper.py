"""
tests/parse_helper.py

Parse a single DOCX or PDF resume locally and write the classification
LLM-input JSON to disk.  No backend server or LLM call required.

Usage:
    python parse_helper.py <input_file> <output_json>
"""

import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <input_file> <output_json>", file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    ext = input_path.suffix.lower()

    if ext == ".docx":
        from tailor.compiler.docx_parser import parse_docx
        doc = parse_docx(str(input_path))
    elif ext == ".pdf":
        from tailor.compiler.pdf_parser import parse_pdf
        doc = parse_pdf(str(input_path))
    else:
        print(f"Unsupported file type: {ext}", file=sys.stderr)
        sys.exit(1)

    from tailor.compiler.classification_models import build_classification_input
    cls_input = build_classification_input(doc, input_path.stem)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(cls_input.to_dict(), f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
