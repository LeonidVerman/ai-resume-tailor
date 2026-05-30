"""PDF Structure Comparison — render integrity check for PDF-origin templates.

Uses PDF block counts as a proxy for paragraph ratio and PDF-based column
detection to determine whether the multi-column template layout was preserved.
Returns DocxStructureResult for interface compatibility with the grader.

For PDF-origin samples the template DOCX is not available, so:
  - paragraph_ratio is computed from meaningful block counts per page
  - has_word_columns_original reflects template PDF column detection (page 1)
  - columns_lost is always False (Word column tracking is not available)
  - table counts are 0
"""
from __future__ import annotations

from .docx_comparator import DocxStructureResult


def compare_pdf_structure(
    template_pdf_path: str,
    generated_pdf_path: str,
) -> DocxStructureResult:
    """Compare template PDF against generated PDF on structural metrics.

    Used for PDF-origin samples where no template DOCX is available.
    Shares the same scoring thresholds as compare_docx_structure() so that
    paragraph_ratio penalties are consistent across both pipelines.
    """
    from tailor.eval.extractor import extract
    from .pdf_scorer import _estimate_page_column_count, _compute_effective_area_metrics

    evidence: list[str] = []

    orig_extracted = extract(template_pdf_path)
    gen_extracted = extract(generated_pdf_path)

    def _total_blocks(extracted) -> int:
        total = 0
        for page in extracted.pages:
            _, _, _, block_count, _ = _compute_effective_area_metrics(page)
            total += block_count
        return total

    orig_blocks = _total_blocks(orig_extracted)
    gen_blocks = _total_blocks(gen_extracted)

    ratio = gen_blocks / orig_blocks if orig_blocks > 0 else 1.0

    # Column detection from template PDF page 1.
    # For PDF-origin, the template IS a PDF so its column layout is authoritative.
    # has_word_columns_original=True gates the COLUMN_LAYOUT_LOST hard-fail in the
    # grader — we want that signal to fire when a multi-column PDF template collapses
    # to single-column in the generated output.
    orig_col_count = 1
    if orig_extracted.pages:
        orig_col_count = _estimate_page_column_count(orig_extracted.pages[0])

    score = 100.0
    if ratio < 0.70:
        penalty = min(55.0, 20.0 + (0.70 - ratio) / 0.70 * 50.0)
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (< 0.70) — possible truncation/fallback")
    elif ratio < 0.85:
        penalty = (0.85 - ratio) / 0.15 * 20.0
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (below ideal 0.85)")
    elif ratio > 1.50:
        penalty = min(20.0, (ratio - 1.50) * 25.0)
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (> 1.50) — content over-expansion")
    elif ratio > 1.35:
        penalty = (ratio - 1.35) / 0.15 * 10.0
        score -= penalty
        evidence.append(f"paragraph_ratio={ratio:.2f} (above ideal 1.35)")
    elif ratio > 1.20:
        evidence.append(f"paragraph_ratio={ratio:.2f} (above ideal 1.20)")

    score = max(0.0, min(100.0, score))
    renderer_fallback = ratio < 0.70

    return DocxStructureResult(
        score=score,
        paragraph_ratio=ratio,
        para_count_original=orig_blocks,
        para_count_generated=gen_blocks,
        table_count_original=0,
        table_count_generated=0,
        section_count_original=0,
        section_count_generated=0,
        has_word_columns_original=(orig_col_count >= 2),
        has_word_columns_generated=False,
        columns_lost=False,
        renderer_fallback=renderer_fallback,
        evidence=evidence,
    )
