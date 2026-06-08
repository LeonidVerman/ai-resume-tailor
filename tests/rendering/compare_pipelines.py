#!/usr/bin/env python3
"""Cross-pipeline text equivalence check.

Extracts and normalises text from the rendered PDFs produced by both the
DOCX-origin and PDF-origin pipelines for each sample and compares them.
Because both pipelines use the same LLM output (from the same generation
JSON), the normalised text should be identical when injection is correct.

Normalisation applied before comparison:
  - Line breaks replaced with a single space
  - Runs of whitespace collapsed to one space
  - Hyphenation artifacts collapsed: "high- performance" -> "high-performance"
    (word char + hyphen + whitespace + word char -> word char + hyphen + word char)
  - Bullet character variants normalised to a single canonical form (bullet),
    including the Wingdings private-use bullet U+F0B7 used by LibreOffice
  - Trailing standalone list-marker dots stripped (LibreOffice DOCX artifact)
  - Text lowercased (section headings vary in case across pipelines)

For two-column PDFs, text is split by x-coordinate into left and right
columns before comparison, to avoid column-interleaving artifacts that arise
when both pipelines assign slightly different y-coordinates to the same
paragraphs.  The column boundary is detected from the largest x-gap in line
left-edge positions (same logic as the extractor's column detector).  A
similarity threshold of 0.99 is used per column to allow for minor
locked-section differences (e.g. cross-page education content that the PDF
parser cannot attribute to its section).

Missing rendered PDFs are reported as failures.

Usage (from repo root):
    python tests/rendering/compare_pipelines.py           # all samples
    python tests/rendering/compare_pipelines.py 1 5 11   # by numeric prefix
    python tests/rendering/compare_pipelines.py 1-Leonid  # by fragment
"""
from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
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
# : Wingdings/Symbol private-use bullet used in DOCX numPr numbering defs.
_BULLET_CHARS = "·•▪▸►◆◇○●◦‣"
_BULLET_RE = re.compile("[" + re.escape(_BULLET_CHARS) + "]")

# LibreOffice sometimes renders a DOCX numbered-list continuation on page 2 as
# an orphaned ". " text block that ends up at the tail of extracted column text.
_TRAILING_DOT_RE = re.compile(r"\s+\.\s*$")

# Two-column comparison: per-column similarity must meet this threshold.
# 0.99 allows for minor locked-section differences (e.g. cross-page education
# content the PDF parser cannot see) while catching any real content injection
# bug (a missing bullet would be >=2% deviation on a typical column).
_TWO_COL_SIM_THRESHOLD = 0.99

# Samples whose classification data uses DOCX-namespace para_ids that map to
# different content under the PDF parser, causing structural divergence that
# cannot be resolved without separate classification artifacts per source kind.
# These are reported as XFAIL (expected failures) and do not affect the exit code.
#
# Sample 6 (6-Template1): The generation JSON's experience classification has
# role[1].header_blocks=[para_23] (same as role[0]) and role[2].header_blocks=
# [para_28].  In the DOCX namespace, para_28="Office manager, Nod Publishing";
# in the PDF namespace, para_28="Summarize your key responsibilities..." (filler).
# Additionally, the DOCX pipeline detects a date-first layout and uses a
# heuristic rebuild that drops LLM role[0] bullets (Managed x5) because role[0]
# has no target paragraphs after _split_role_by_meta_dates, while the PDF
# pipeline uses the classification-based path that assigns those bullets to
# Phone Company in the right column.  The resulting column-text similarity is
# ~0.61, well below the 0.99 threshold.
_XFAIL_SAMPLES: frozenset[str] = frozenset({"6"})


def _num_prefix(name: str) -> str | None:
    m = _NUM_RE.match(name)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_pdf_lines(pdf_path: Path) -> tuple[list[tuple[str, float]], float]:
    """Return ([(text, left_x), ...], page_width)."""
    from tailor.eval.extractor import extract
    extracted = extract(str(pdf_path))
    lines: list[tuple[str, float]] = []
    for page in extracted.pages:
        for line in page.lines:
            text = line.text.strip()
            if text:
                lines.append((text, line.left_x))
    page_width = extracted.pages[0].width if extracted.pages else 612.0
    return lines, page_width


def _find_col_split(lines: list[tuple[str, float]], page_width: float) -> float | None:
    """Return x midpoint of the largest left-edge gap, or None if no two-column gap.

    Uses the same clustering logic as extractor._detect_columns.
    """
    left_xs = [round(x) for t, x in lines if len(t.strip()) > 5]
    if len(left_xs) < 4:
        return None
    sorted_xs = sorted(set(left_xs))
    if len(sorted_xs) < 2:
        return None
    gaps = [(sorted_xs[i + 1] - sorted_xs[i], i) for i in range(len(sorted_xs) - 1)]
    max_gap, max_gap_idx = max(gaps, key=lambda g: g[0])
    if max_gap < page_width * 0.15:
        return None
    left_count  = sum(1 for x in left_xs if x <= sorted_xs[max_gap_idx])
    right_count = sum(1 for x in left_xs if x >= sorted_xs[max_gap_idx + 1])
    if left_count < 3 or right_count < 3:
        return None
    return (sorted_xs[max_gap_idx] + sorted_xs[max_gap_idx + 1]) / 2.0


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = re.sub(r"[\r\n]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    # Collapse line-break hyphenation: "high- performance" -> "high-performance"
    text = re.sub(r"(\w)-\s+(\w)", r"\1-\2", text)
    # Normalise bullet variants to a single canonical character
    text = _BULLET_RE.sub("•", text)
    # Strip leading/trailing standalone list-marker dots (LibreOffice rendering artifact)
    text = re.sub(r"^\.\s+", "", text)
    text = _TRAILING_DOT_RE.sub("", text)
    text = text.lower()
    return text.strip()


# ---------------------------------------------------------------------------
# Diff description
# ---------------------------------------------------------------------------

def _first_diff_desc(a: str, b: str, context: int = 80) -> str:
    """Return a human-readable description of the first difference."""
    for i in range(min(len(a), len(b))):
        if a[i] != b[i]:
            start = max(0, i - 30)
            prefix = "..." if start > 0 else ""
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
            f"({shorter} ends first; {longer} continues: '{extra}...')"
        )
    return ""


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _compare_pdf_pair(docx_pdf: Path, pdf_pdf: Path) -> tuple[bool, str]:
    """Compare two rendered PDFs.  Returns (is_match, failure_description)."""
    docx_lines, dwidth = _extract_pdf_lines(docx_pdf)
    pdf_lines,  pwidth = _extract_pdf_lines(pdf_pdf)

    # Detect actual column split from line left-edge clustering.
    # Using geometry-based detection is more reliable than the extractor's
    # col_count flag, which can disagree between the two PDFs.
    docx_split = _find_col_split(docx_lines, dwidth)
    pdf_split  = _find_col_split(pdf_lines,  pwidth)
    split_x = docx_split or pdf_split

    if split_x is not None:
        # Two-column: split by x then compare each column to avoid interleaving
        # artifacts from slight y-coordinate differences between pipelines.
        def _col(lines: list[tuple[str, float]], left: bool) -> str:
            parts = [t for t, x in lines if (x < split_x) == left]
            return _normalize(" ".join(parts))

        docx_left  = _col(docx_lines, True)
        docx_right = _col(docx_lines, False)
        pdf_left   = _col(pdf_lines,  True)
        pdf_right  = _col(pdf_lines,  False)

        sim_left  = SequenceMatcher(None, docx_left,  pdf_left).ratio()
        sim_right = SequenceMatcher(None, docx_right, pdf_right).ratio()

        if sim_left >= _TWO_COL_SIM_THRESHOLD and sim_right >= _TWO_COL_SIM_THRESHOLD:
            return True, ""

        parts: list[str] = []
        if sim_left < _TWO_COL_SIM_THRESHOLD:
            parts.append(
                f"Left column similarity {sim_left:.3f} < {_TWO_COL_SIM_THRESHOLD}"
            )
            parts.append(_first_diff_desc(docx_left, pdf_left))
        if sim_right < _TWO_COL_SIM_THRESHOLD:
            parts.append(
                f"Right column similarity {sim_right:.3f} < {_TWO_COL_SIM_THRESHOLD}"
            )
            parts.append(_first_diff_desc(docx_right, pdf_right))
        return False, "\n".join(parts)

    # Single-column: exact match after normalisation
    docx_text = _normalize(" ".join(t for t, _ in docx_lines))
    pdf_text  = _normalize(" ".join(t for t, _ in pdf_lines))
    if docx_text == pdf_text:
        return True, ""
    return False, _first_diff_desc(docx_text, pdf_text)


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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

    failures: list[tuple[str, str, str]] = []
    xfails:   list[tuple[str, str, str]] = []
    n_pass = 0

    for n, stem, docx_pdf, pdf_pdf in pairs:
        is_xfail = n in _XFAIL_SAMPLES

        if docx_pdf is None:
            reason = "DOCX-origin rendered PDF not found"
            if is_xfail:
                xfails.append((n, stem, reason))
            else:
                failures.append((n, stem, reason))
            continue
        if pdf_pdf is None:
            reason = "PDF-origin rendered PDF not found"
            if is_xfail:
                xfails.append((n, stem, reason))
            else:
                failures.append((n, stem, reason))
            continue

        try:
            ok, diff = _compare_pdf_pair(docx_pdf, pdf_pdf)
        except Exception as exc:
            reason = f"Extraction error: {exc}"
            if is_xfail:
                xfails.append((n, stem, reason))
            else:
                failures.append((n, stem, reason))
            continue

        if ok:
            if is_xfail:
                # Unexpectedly passing — treat as a normal pass but note it.
                print(f"  [{n}] {stem}  XPASS (was xfail, now passes — remove from _XFAIL_SAMPLES)")
            n_pass += 1
        else:
            if is_xfail:
                xfails.append((n, stem, diff))
            else:
                failures.append((n, stem, diff))

    if xfails:
        print("XFAIL (known divergent, not counted as failures):")
        for n, stem, reason in xfails:
            first_line = reason.splitlines()[0] if reason else ""
            print(f"\n  [{n}] {stem}  [XFAIL] {first_line}")

    if failures:
        print("\nFAILURES:")
        for n, stem, reason in failures:
            print(f"\n  [{n}] {stem}")
            for line in reason.splitlines():
                print(f"    {line}")

    n_fail  = len(failures)
    n_xfail = len(xfails)
    total   = n_pass + n_fail + n_xfail

    print(f"\n{'=' * 60}")
    status = "OK" if n_fail == 0 else "FAILED"
    summary = f"{n_pass}/{total} PASS  |  {n_fail}/{total} FAIL"
    if n_xfail:
        summary += f"  |  {n_xfail}/{total} XFAIL"
    print(f"  Cross-pipeline [{status}]: {summary}")
    print("=" * 60)

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
