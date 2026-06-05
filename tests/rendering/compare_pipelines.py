#!/usr/bin/env python3
"""Cross-pipeline text equivalence check.

Extracts and normalises text from the rendered PDFs produced by both the
DOCX-origin and PDF-origin pipelines for each sample and compares them.
Because both pipelines use the same LLM output (from the same generation
JSON), the normalised text should be identical when injection is correct.

Normalisation applied before comparison:
  - Line breaks replaced with a single space
  - Runs of whitespace collapsed to one space
  - Hyphenation artifacts collapsed: "high- performance" → "high-performance"
    (word char + hyphen + whitespace + word char → word char + hyphen + word char)
  - Bullet character variants normalised to a single canonical form (•)
  - Leading / trailing whitespace stripped

Missing rendered PDFs are reported as failures.

Usage (from repo root):
    python tests/rendering/compare_pipelines.py           # all samples
    python tests/rendering/compare_pipelines.py 1 5 11   # by numeric prefix
    python tests/rendering/compare_pipelines.py 1-Leonid  # by fragment
"""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_REND_DOCX_DIR = _REPO / "tmp" / "artefacts" / "rendering" / "docx"
_REND_PDF_DIR  = _REPO / "tmp" / "artefacts" / "rendering" / "pdf"

_NUM_RE = re.compile(r"^(\d+)-")

# Bullet/list-marker characters that are visually equivalent across pipelines.
# PDF renderers and LibreOffice substitute these freely; collapse all to one form.
# : Symbol/Wingdings private-use bullet used in DOCX numPr numbering defs;
# LibreOffice renders it as a separate text block (distinct from the inline •
# that the PDF-origin pipeline emits via build_para_element).
_BULLET_RE = re.compile(r"[·•▪▸►◆◇○●◦‣]")


def _num_prefix(name: str) -> str | None:
    m = _NUM_RE.match(name)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Text extraction + normalisation
# ---------------------------------------------------------------------------

def _extract_pdf_text(pdf_path: Path) -> str:
    """Return concatenated line text from all pages of a PDF."""
    from tailor.eval.extractor import extract
    extracted = extract(str(pdf_path))
    parts: list[str] = []
    for page in extracted.pages:
        for line in page.lines:
            text = line.text.strip()
            if text:
                parts.append(text)
    return " ".join(parts)


def _normalize(text: str) -> str:
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    # Collapse line-break hyphenation: "high- performance" → "high-performance"
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)
    # Normalise bullet variants to a single canonical character
    text = _BULLET_RE.sub("•", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Diff description
# ---------------------------------------------------------------------------

def _first_diff_desc(a: str, b: str, context: int = 80) -> str:
    """Return a human-readable description of the first difference."""
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            start = max(0, i - 30)
            prefix = "…" if start > 0 else ""   # …
            a_snip = prefix + a[start : i + context]
            b_snip = prefix + b[start : i + context]
            return (
                f"First difference at char {i}:\n"
                f"  DOCX: '{a_snip}'\n"
                f"  PDF:  '{b_snip}'"
            )
    if len(a) != len(b):
        shorter = "DOCX" if len(a) < len(b) else "PDF"
        longer  = "PDF"  if len(a) < len(b) else "DOCX"
        tail_start = min(len(a), len(b))
        extra = (b if len(b) > len(a) else a)[tail_start : tail_start + context]
        return (
            f"Length mismatch: DOCX={len(a)} chars, PDF={len(b)} chars "
            f"({shorter} ends first; {longer} continues: '{extra}…')"
        )
    return ""


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def discover_pairs(
    filter_prefixes: set[str] | None,
    filter_frags: list[str] | None,
) -> list[tuple[str, str, Path | None, Path | None]]:
    """Return [(prefix, stem, docx_pdf, pdf_pdf)] ordered by numeric prefix."""
    docx_index: dict[str, Path] = {}
    if _REND_DOCX_DIR.is_dir():
        for p in _REND_DOCX_DIR.glob("*.pdf"):
            n = _num_prefix(p.name)
            if n:
                docx_index[n] = p

    pdf_index: dict[str, Path] = {}
    if _REND_PDF_DIR.is_dir():
        for p in _REND_PDF_DIR.glob("*.pdf"):
            n = _num_prefix(p.name)
            if n:
                pdf_index[n] = p

    all_prefixes = sorted(set(docx_index) | set(pdf_index), key=int)

    if filter_prefixes:
        all_prefixes = [n for n in all_prefixes if n in filter_prefixes]
    elif filter_frags:
        def _matches(n: str) -> bool:
            p = docx_index.get(n) or pdf_index.get(n)
            return p is not None and any(f in p.stem.lower() for f in filter_frags)
        all_prefixes = [n for n in all_prefixes if _matches(n)]

    pairs = []
    for n in all_prefixes:
        docx_p = docx_index.get(n)
        pdf_p  = pdf_index.get(n)
        stem   = (docx_p or pdf_p).stem  # type: ignore[union-attr]
        pairs.append((n, stem, docx_p, pdf_p))
    return pairs


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Cross-pipeline text equivalence: DOCX-origin vs PDF-origin.",
        add_help=False,
    )
    parser.add_argument(
        "filter", nargs="*",
        help="Numeric prefix(es) or filename fragment(s) to narrow scope",
    )
    parser.add_argument("-h", "--help", action="help")

    # parse_known_args: silently ignore flags that belong to other scripts
    # when this script is invoked via %* from test_rendering.cmd.
    args, _ = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    filter_prefixes: set[str] | None = None
    filter_frags: list[str] | None = None
    if args.filter:
        nums  = {a for a in args.filter if a.isdigit()}
        frags = [a.lower() for a in args.filter if not a.isdigit()]
        if nums:
            filter_prefixes = nums
        if frags:
            filter_frags = frags

    pairs = discover_pairs(filter_prefixes, filter_frags)

    print("=" * 60)
    print("  compare_pipelines -- cross-pipeline text equivalence")
    print("=" * 60)

    if not pairs:
        print("\n  No matched PDF pairs found -- nothing to compare.")
        return 0

    print(f"\n  Comparing {len(pairs)} sample(s) ...\n")

    failures: list[tuple[str, str, str]] = []   # (prefix, stem, reason)
    n_pass = 0

    for n, stem, docx_pdf, pdf_pdf in pairs:
        if docx_pdf is None:
            failures.append((n, stem, "DOCX-origin rendered PDF not found"))
            continue
        if pdf_pdf is None:
            failures.append((n, stem, "PDF-origin rendered PDF not found"))
            continue

        try:
            docx_text = _normalize(_extract_pdf_text(docx_pdf))
            pdf_text  = _normalize(_extract_pdf_text(pdf_pdf))
        except Exception as exc:
            failures.append((n, stem, f"Extraction error: {exc}"))
            continue

        if docx_text == pdf_text:
            n_pass += 1
        else:
            diff = _first_diff_desc(docx_text, pdf_text)
            failures.append((n, stem, diff))

    if failures:
        print("FAILURES:")
        for n, stem, reason in failures:
            print(f"\n  [{n}] {stem}")
            for line in reason.splitlines():
                print(f"    {line}")

    n_fail = len(failures)
    total  = n_pass + n_fail

    print(f"\n{'=' * 60}")
    status = "OK" if n_fail == 0 else "FAILED"
    print(
        f"  Cross-pipeline [{status}]: "
        f"{n_pass}/{total} PASS  |  {n_fail}/{total} FAIL"
    )
    print("=" * 60)

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
