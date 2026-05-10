"""DOCX Structure Comparison — render integrity check.

Compares the original template DOCX against the generated DOCX on purely
structural metrics, deliberately ignoring text content differences (those
are expected from LLM tailoring).

Metrics:
  paragraph_ratio   generated_paragraphs / original_paragraphs  (ideal 0.85–1.20)
  table_count       table loss → heavy penalty
  word_columns      w:cols/@w:num > 1 lost → HARD FAIL trigger
  section_count     parsed section count (via docx_parser)

HARD FAIL trigger:
  columns_lost — original had w:cols multi-column layout, generated does not

renderer_fallback:
  True when paragraph_ratio < 0.70 or all tables dropped from a multi-table
  template, suggesting the renderer fell back to a simplified output path.
"""
from __future__ import annotations

from dataclasses import dataclass, field

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


@dataclass
class DocxStructureResult:
    score: float                    # 0–100
    paragraph_ratio: float
    para_count_original: int
    para_count_generated: int
    table_count_original: int
    table_count_generated: int
    section_count_original: int
    section_count_generated: int
    has_word_columns_original: bool
    has_word_columns_generated: bool
    columns_lost: bool              # HARD FAIL when True
    renderer_fallback: bool
    evidence: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_word_columns(docx_path: str) -> bool:
    """Return True if any sectPr in the DOCX has w:cols/@w:num > 1.

    Uses findall (not find) to check ALL w:cols children since python-docx
    default templates already include a w:cols element for spacing.
    """
    from docx import Document
    try:
        doc = Document(docx_path)
        for sect in doc.element.body.iter(f"{{{_W}}}sectPr"):
            for cols in sect.findall(f"{{{_W}}}cols"):
                num_str = cols.get(f"{{{_W}}}num") or "1"
                try:
                    if int(num_str) > 1:
                        return True
                except ValueError:
                    pass
        return False
    except Exception:
        return False


def _count_paragraphs(docx_path: str) -> tuple[int, int]:
    """Return (total_paras, paras_with_text).

    Counts ALL paragraphs including those inside table cells so that
    templates whose body paragraphs were converted from native w:cols to a
    2-cell table (for overflow lane stability) are not incorrectly flagged
    as truncated.
    """
    from docx import Document
    doc = Document(docx_path)
    all_p = doc.element.body.findall(f".//{{{_W}}}p")
    total = len(all_p)
    with_text = sum(
        1 for p in all_p
        if "".join(t.text or "" for t in p.findall(f".//{{{_W}}}t")).strip()
    )
    return total, with_text


def _has_table_columns(docx_path: str) -> bool:
    """True if any body-level table has ≥ 2 cells in a single row.

    When native w:cols is converted to a 2-cell layout table for overflow
    stability the result is functionally equivalent to native columns.  This
    check lets the grader distinguish intentional table-based columns from
    a genuine column-layout loss.
    """
    from docx import Document
    try:
        doc = Document(docx_path)
        body = doc.element.body
        for tbl in body.findall(f"{{{_W}}}tbl"):
            for tr in tbl.findall(f"{{{_W}}}tr"):
                if len(tr.findall(f"{{{_W}}}tc")) >= 2:
                    return True
        return False
    except Exception:
        return False


def _count_tables(docx_path: str) -> int:
    from docx import Document
    doc = Document(docx_path)
    return len(doc.tables)


def _count_sections(docx_path: str) -> int:
    """Count resume sections via the compiler parser; 0 on any error."""
    try:
        from tailor.compiler.docx_parser import parse_docx
        return len(parse_docx(docx_path).sections)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compare_docx_structure(original_path: str, generated_path: str) -> DocxStructureResult:
    """Compare two DOCX files on structural metrics and return a scored result."""
    evidence: list[str] = []

    orig_total, _orig_text = _count_paragraphs(original_path)
    gen_total, _gen_text = _count_paragraphs(generated_path)

    orig_tables = _count_tables(original_path)
    gen_tables = _count_tables(generated_path)

    orig_cols = _has_word_columns(original_path)
    gen_cols = _has_word_columns(generated_path)
    # Not "columns lost" when native w:cols was intentionally replaced by a
    # 2-cell layout table for overflow lane stability (Tasks 2+3).
    _gen_has_tbl_cols = _has_table_columns(generated_path) if orig_cols and not gen_cols else False
    columns_lost = orig_cols and not gen_cols and not _gen_has_tbl_cols

    orig_sections = _count_sections(original_path)
    gen_sections = _count_sections(generated_path)

    ratio = gen_total / orig_total if orig_total > 0 else 1.0

    score = 100.0

    # ── Paragraph ratio ───────────────────────────────────────────────────────
    if ratio < 0.70:
        # Heavy penalty — likely renderer fallback or truncation
        penalty = min(55.0, 20.0 + (0.70 - ratio) / 0.70 * 50.0)
        score -= penalty
        evidence.append(
            f"paragraph_ratio={ratio:.2f} (< 0.70) — possible truncation/fallback"
        )
    elif ratio < 0.85:
        penalty = (0.85 - ratio) / 0.15 * 20.0
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (below ideal 0.85)")
    elif ratio > 1.50:
        penalty = min(20.0, (ratio - 1.50) * 25.0)
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (> 1.50) — content over-expansion")
    elif ratio > 1.20:
        penalty = (ratio - 1.20) / 0.30 * 10.0
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (above ideal 1.20)")

    # ── Table loss ────────────────────────────────────────────────────────────
    if orig_tables > 0 and gen_tables < orig_tables:
        drop = (orig_tables - gen_tables) / orig_tables
        score -= drop * 30.0
        evidence.append(
            f"Tables: orig={orig_tables}, gen={gen_tables} "
            f"({drop:.0%} drop — heavy penalty)"
        )

    # ── Word columns lost (also HARD FAIL at grader) ──────────────────────────
    if columns_lost:
        score -= 40.0
        evidence.append(
            "Word multi-column layout (w:cols/@w:num > 1) lost in generated DOCX"
        )

    # ── Section count ─────────────────────────────────────────────────────────
    if orig_sections > 0 and gen_sections < orig_sections * 0.70:
        drop = 1.0 - gen_sections / orig_sections
        score -= drop * 15.0
        evidence.append(
            f"Section count: orig={orig_sections}, gen={gen_sections}"
        )

    score = max(0.0, min(100.0, score))

    renderer_fallback = ratio < 0.70 or (orig_tables > 2 and gen_tables == 0)

    return DocxStructureResult(
        score=score,
        paragraph_ratio=ratio,
        para_count_original=orig_total,
        para_count_generated=gen_total,
        table_count_original=orig_tables,
        table_count_generated=gen_tables,
        section_count_original=orig_sections,
        section_count_generated=gen_sections,
        has_word_columns_original=orig_cols,
        has_word_columns_generated=gen_cols,
        columns_lost=columns_lost,
        renderer_fallback=renderer_fallback,
        evidence=evidence,
    )
