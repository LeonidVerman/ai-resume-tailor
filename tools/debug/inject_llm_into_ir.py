#!/usr/bin/env python3
"""Deterministic LLM injection into sample 31 IR.

Loads the DOCX template for sample 31, injects the saved LLM resume output
from a generation JSON, and writes the final updated IR to:
    tmp/artefacts/ir/sample31_final_ir.json

Runs the full layout-bound invariant health check and exits non-zero on
any hard violation.

Usage:
    python tools/debug/inject_llm_into_ir.py [--gen-json PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

_DOCX_PATH = str(
    _ROOT / "tests/samples/resume/docx"
    / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
)
_DEFAULT_GEN_JSON = str(
    _ROOT / "tests/samples/generation"
    / "Gabriel_Mitchell-American_Tire_Distributors-Lead_Software_Engineer-201-20260427-170826.json"
)
_OUTPUT = _ROOT / "tmp/artefacts/ir/sample31_final_ir.json"

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_BULLET_PREFIXES = ("- ", "• ", "● ", "– ", "* ")


def _preprocess_llm_text(text: str) -> str:
    """Pre-process LLM resume text before parsing.

    1. Strips lines that start with "Current Date:" (injected timestamp).
    2. Merges "title-line\\ncompany|date-line" pairs into a single
       "title | company | date" line so parse_llm_output produces clean
       pipe-delimited role headers instead of embedding the title in
       the experience section's body_lines.
    """
    lines = text.splitlines()

    # 1. Strip Current Date lines
    lines = [
        line for line in lines
        if not re.match(r"^\s*Current\s+Date\s*:", line, re.IGNORECASE)
    ]

    # 2. Merge title-without-pipe + company|date-with-pipe
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        is_title_candidate = (
            bool(stripped)
            and "|" not in stripped
            and not any(stripped.startswith(p) for p in _BULLET_PREFIXES)
            and stripped != stripped.upper()        # not ALL-CAPS section heading
            and len(stripped) <= 60
            and not stripped[-1:] in ".!?,:;"       # not end-of-sentence
        )

        if is_title_candidate:
            # Find the next non-blank line
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines):
                next_stripped = lines[j].strip()
                if "|" in next_stripped and _YEAR_RE.search(next_stripped):
                    # Merge: "title | company | date"
                    merged = f"{stripped} | {next_stripped}"
                    result.append(merged)
                    # Re-emit blank lines that were between the two
                    for k in range(i + 1, j):
                        result.append(lines[k])
                    i = j + 1
                    continue

        result.append(line)
        i += 1

    return "\n".join(result)


def run(gen_json_path: str, verbose: bool = True) -> dict:
    """Run the injection and return the health check violations dict."""
    import tailor.config as cfg
    cfg.USE_LAYOUT_BOUND_UPDATER = True

    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.updater import apply_tailored
    from check_layout_bound_ir_health import (
        assert_layout_bound_clean,
        check_layout_bound_ir_health,
        _print_summary,
    )

    if verbose:
        print(f"Parsing DOCX: {_DOCX_PATH}")
    doc = parse_docx(_DOCX_PATH)
    if verbose:
        print(f"  sections={len(doc.sections)}  "
              f"layout_blocks={len(doc.layout_blocks) if doc.layout_blocks else 0}")

    with open(gen_json_path, encoding="utf-8") as f:
        gen = json.load(f)
    llm_text: str = gen["llm_response"]["resume"]
    if verbose:
        print(f"LLM text: {len(llm_text)} chars")

    processed = _preprocess_llm_text(llm_text)
    if verbose:
        print(f"Pre-processed: {len(processed)} chars")

    llm_sections = parse_llm_output(processed)
    if verbose:
        print(f"\nParsed LLM sections ({len(llm_sections)}):")
        for s in llm_sections:
            print(f"  [{s.semantic_type:14}] {s.heading!r:<45} "
                  f"roles={len(s.roles)}  body={len(s.body_lines)}")
            for r in s.roles:
                print(f"    role {r.header!r}  "
                      f"meta={len(r.meta_lines)}  bullets={len(r.bullets)}")

    if verbose:
        print("\nApplying tailored update (USE_LAYOUT_BOUND_UPDATER=True)...")
    updated = apply_tailored(doc, llm_sections)

    if verbose:
        print("\nRunning health check...")
    violations = check_layout_bound_ir_health(updated)
    _print_summary(updated, violations)

    hard = {
        k: v for k, v in violations.items()
        if k not in ("role_count", "layout_blocks_count", "layout_semantic_mismatches")
    }
    failures = {k: v for k, v in hard.items() if v}
    role_count = violations["role_count"]
    if role_count != 3:
        failures["role_count (expected 3)"] = role_count

    if failures:
        print(f"\nFAILED: {failures}")
        sys.exit(1)

    print("\nAll hard invariants PASSED.")

    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    out = updated.to_dict()
    with open(_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Output written to: {_OUTPUT}")
    return violations


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gen-json", default=_DEFAULT_GEN_JSON,
        help="Path to generation JSON (default: sample 31 Gabriel Mitchell)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="Suppress progress output",
    )
    args = parser.parse_args()
    run(args.gen_json, verbose=not args.quiet)


if __name__ == "__main__":
    main()
