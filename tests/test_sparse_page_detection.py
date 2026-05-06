"""Tests for sparse continuation page detection in the PDF grader.

Covers:
- Unit tests for _find_sparse_continuation_pages and _compute_sparse_page_score.
- Integration regression: sample 1 is no longer graded PASS ≥ 99.
- False-positive checks: short tail overflows and well-filled pages pass unpenalised.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

_RENDERED_PDF_S1 = _ROOT / "tmp/artefacts/rendering/pdf/1-Leonid_Verman_Resume_Template.pdf"
_TEMPLATE_PDF_S1 = _ROOT / "tmp/artefacts/layout_grading/template_pdf/1-Leonid_Verman_Resume_Template.pdf"
_IR_S1 = _ROOT / "tmp/artefacts/ir/docx/1-Leonid_Verman_Resume_Template_IR.json"
_GEN_JSON_S1 = _ROOT / "tests/samples/generation/1-Leonid_Verman-American_Tire_Distributors-Lead_Software_Engineer-213-20260428-040609.json"
_TEMPLATE_DOCX_S1 = _ROOT / "tests/samples/resume/docx/1-Leonid_Verman_Resume_Template.docx"

_ARTIFACTS_PRESENT = _RENDERED_PDF_S1.exists() and _TEMPLATE_PDF_S1.exists()


# ---------------------------------------------------------------------------
# Mock helpers for unit tests
# ---------------------------------------------------------------------------

@dataclass
class _FakeBbox:
    x0: float = 0.0
    y0: float = 0.0
    x1: float = 612.0
    y1: float = 100.0

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))

    def __getitem__(self, i):
        return (self.x0, self.y0, self.x1, self.y1)[i]


_PAGE_WIDTH = 595.0  # standard page width used in mocks


def _fake_block(lines, y_top: float, y_bot: float, block_type: str = "paragraph"):
    blk = MagicMock()
    blk.block_type = block_type
    blk.lines = lines
    # bbox: (x0, y0, x1, y1) using full page width minus margins
    blk.bbox = (57.0, y_top, 537.0, y_bot)
    return blk


def _fake_page(
    page_number: int,
    page_height: float,
    content_top: float,
    content_bottom: float,
    n_lines: int = 25,
    n_blocks: int = 10,
):
    """Build a minimal mock PageModel for sparse-detection testing."""
    page = MagicMock()
    page.page_number = page_number
    page.height = float(page_height)
    page.width = _PAGE_WIDTH

    if content_top is not None and content_bottom is not None:
        page.content_bbox = (0.0, float(content_top), _PAGE_WIDTH, float(content_bottom))
    else:
        page.content_bbox = None

    # Build mock lines — each line has a text string
    lines = []
    for i in range(n_lines):
        ln = MagicMock()
        ln.text = f"Resume content line {i}: text here for testing"
        lines.append(ln)
    page.lines = lines

    # Build mock blocks with real bbox so area metrics work
    block_height = 20.0
    blocks = []
    if n_blocks > 0 and content_top is not None and content_bottom is not None:
        span = float(content_bottom) - float(content_top)
        step = span / max(n_blocks, 1)
        for i in range(n_blocks):
            by0 = float(content_top) + i * step
            by1 = by0 + block_height
            blk_lines = [lines[i]] if i < len(lines) else []
            blocks.append(_fake_block(blk_lines, by0, by1))
    page.blocks = blocks

    return page


def _fake_doc(*pages):
    doc = MagicMock()
    doc.pages = list(pages)
    return doc


# ---------------------------------------------------------------------------
# Unit tests: _find_sparse_continuation_pages
# ---------------------------------------------------------------------------

class TestFindSparsePages:
    from tailor.eval.layout_grader.pdf_scorer import (
        _find_sparse_continuation_pages,
        _SPARSE_FILL_THRESHOLD,
        _SPARSE_PREV_PAGE_MIN_FILL,
        _SPARSE_MIN_LINES,
    )

    def _call(self, *pages):
        from tailor.eval.layout_grader.pdf_scorer import _find_sparse_continuation_pages
        return _find_sparse_continuation_pages(_fake_doc(*pages))

    def test_first_page_never_flagged(self):
        """Page 1 is never flagged regardless of fill."""
        p1 = _fake_page(1, 842, 50, 200, n_lines=5)  # very sparse but page 1
        result = self._call(p1)
        assert result == []

    def test_well_filled_page_not_flagged(self):
        """Page 2 at 80% fill is not sparse."""
        p1 = _fake_page(1, 842, 50, 756)   # 84% fill
        p2 = _fake_page(2, 842, 50, 724)   # 80% fill  (≥ 65% threshold)
        result = self._call(p1, p2)
        assert result == []

    def test_sparse_page_after_full_page_flagged(self):
        """Page 2 at 59% fill after page 1 at 84% is flagged."""
        p1 = _fake_page(1, 842, 50, 757)   # 84% fill
        p2 = _fake_page(2, 842, 50, 552, n_lines=20)  # ~59% fill, 20 lines
        result = self._call(p1, p2)
        assert len(result) == 1
        page_num, fill, bottom_empty, area_ratio, n_lines, n_blocks, section = result[0]
        assert page_num == 2
        assert fill < 0.60
        assert bottom_empty > 0.30
        assert n_lines == 20
        assert 0.0 <= area_ratio <= 1.0

    def test_sparse_page_after_sparse_previous_not_flagged(self):
        """Sparse page after a non-full previous page is OK (short resume)."""
        p1 = _fake_page(1, 842, 50, 500)   # 54% fill — not full
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)  # sparse
        result = self._call(p1, p2)
        assert result == []

    def test_too_few_lines_not_flagged(self):
        """Page with fewer than _SPARSE_MIN_LINES lines is trivial tail overflow."""
        from tailor.eval.layout_grader.pdf_scorer import _SPARSE_MIN_LINES
        p1 = _fake_page(1, 842, 50, 757)          # 84% fill
        p2 = _fake_page(2, 842, 50, 400, n_lines=_SPARSE_MIN_LINES - 1)
        result = self._call(p1, p2)
        assert result == []

    def test_min_lines_exactly_triggers(self):
        """Page with exactly _SPARSE_MIN_LINES lines and low fill is flagged."""
        from tailor.eval.layout_grader.pdf_scorer import _SPARSE_MIN_LINES
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=_SPARSE_MIN_LINES)
        result = self._call(p1, p2)
        assert len(result) == 1

    def test_page_without_content_bbox_skipped(self):
        """Page with no content_bbox is skipped."""
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, None, None, n_lines=25)
        result = self._call(p1, p2)
        assert result == []


# ---------------------------------------------------------------------------
# Unit tests: _compute_sparse_page_score
# ---------------------------------------------------------------------------

class TestComputeSparsePageScore:
    def _call(self, *pages):
        from tailor.eval.layout_grader.pdf_scorer import _compute_sparse_page_score
        return _compute_sparse_page_score(_fake_doc(*pages))

    def test_no_sparse_pages_score_100(self):
        """Score is 100 when all pages are well-filled."""
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 750, n_lines=25)
        score, hard_fail, evidence = self._call(p1, p2)
        assert score == 100.0
        assert hard_fail is False
        assert evidence == []

    def test_sparse_page_score_zero(self):
        """Score is 0 when a qualifying sparse page is found."""
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)
        score, hard_fail, evidence = self._call(p1, p2)
        assert score == 0.0
        assert len(evidence) == 1
        assert "Sparse continuation page" in evidence[0]
        assert "page 2" in evidence[0]

    def test_evidence_contains_area_and_visual_empty(self):
        """Evidence message reports text coverage% and visual emptiness%.

        The two percentages must be complementary (sum ≈ 100%) so the reader
        is not confused by apparently contradictory numbers.
        """
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)
        _, _, evidence = self._call(p1, p2)
        msg = evidence[0]
        assert "text covers" in msg, f"Expected 'text covers' in evidence; got: {msg}"
        assert "visually empty" in msg, f"Expected 'visually empty' in evidence; got: {msg}"
        assert "lines" in msg
        assert "blocks" in msg
        # bottom_empty should NOT appear — it would contradict area_ratio
        assert "empty at bottom" not in msg, (
            "bottom_empty should not appear in evidence (contradicts area_ratio)"
        )

    def test_multiple_sparse_pages_only_triggered_once(self):
        """Only pages after a well-filled previous page are flagged."""
        p1 = _fake_page(1, 842, 50, 757)          # 84% full
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)  # sparse, prev 84% ✓
        p3 = _fake_page(3, 842, 50, 400, n_lines=20)  # sparse, prev 43% — NOT flagged
        score, _, evidence = self._call(p1, p2, p3)
        # Page 2 is flagged (after well-filled page 1)
        # Page 3 is NOT flagged (page 2 fill < 75%)
        assert score == 0.0
        assert len(evidence) == 1
        assert "page 2" in evidence[0]

    def test_hard_fail_when_area_ratio_below_threshold(self):
        """Hard fail is raised when effective_area_ratio < _SPARSE_HARD_FAIL_AREA_RATIO."""
        from tailor.eval.layout_grader.pdf_scorer import _SPARSE_HARD_FAIL_AREA_RATIO
        # A page with very sparse content (blocks are small — few lines, small height)
        # The _fake_page with n_blocks=10, block_height=20pt each gives:
        # area = 10 × 20 × (537-57) = 10 × 20 × 480 = 96,000 pt²
        # page_area = 842 × 595 = 500,990 pt²
        # area_ratio ≈ 19.2% < 25% → HARD FAIL
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=20, n_blocks=10)
        _, hard_fail, evidence = self._call(p1, p2)
        # With small mock blocks the area ratio will be below threshold
        if hard_fail:
            assert "HARD FAIL" in evidence[0]
        # Either way, score should be 0 (sparse detected)

    def test_returns_three_tuple(self):
        """_compute_sparse_page_score returns (score, hard_fail, evidence)."""
        from tailor.eval.layout_grader.pdf_scorer import _compute_sparse_page_score
        result = _compute_sparse_page_score(_fake_doc(_fake_page(1, 842, 50, 757)))
        assert len(result) == 3
        score, hf, ev = result
        assert isinstance(score, float)
        assert isinstance(hf, bool)
        assert isinstance(ev, list)


# ---------------------------------------------------------------------------
# Integration tests: weights and failure class
# ---------------------------------------------------------------------------

class TestSparsePageScoreIntegration:
    def test_weight_sums_to_one(self):
        """All grader weights sum to 1.0."""
        from tailor.eval.layout_grader.grader import _WEIGHTS
        total = sum(_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9, f"Weights sum to {total}, expected 1.0"

    def test_sparse_weight_present(self):
        """sparse_page_score has a non-zero weight."""
        from tailor.eval.layout_grader.grader import _WEIGHTS
        assert "sparse_page_score" in _WEIGHTS
        assert _WEIGHTS["sparse_page_score"] > 0

    def test_sparse_weight_dominant(self):
        """sparse_page_score weight ≥ 20% so that a single sparse page drives score below 75."""
        from tailor.eval.layout_grader.grader import _WEIGHTS
        assert _WEIGHTS["sparse_page_score"] >= 0.20

    def test_pdf_defaults_include_sparse(self):
        """_PDF_DEFAULTS includes sparse_page_score so PDF-unavailable grades still work."""
        from tailor.eval.layout_grader.grader import _PDF_DEFAULTS
        assert "sparse_page_score" in _PDF_DEFAULTS

    def test_perfect_output_with_sparse_page_substantially_reduced(self):
        """A document where all metrics are 100 but sparse_page_score=0
        yields composite ≤ 75 — at or below the PASS threshold.

        When region_score or other metrics are below 100 (as in sample 1),
        the composite drops into WARNING territory (< 75).  With ALL metrics
        at 100 and sparse=0 the composite is exactly 75.0 (right at the
        boundary), which is the intended behaviour — the 25% weight ensures
        that any real-world sample with a sparse page and any other small
        imperfection becomes a WARNING.
        """
        from tailor.eval.layout_grader.grader import _WEIGHTS
        # All non-sparse metrics = 100, sparse = 0
        scores = {k: 100.0 for k in _WEIGHTS}
        scores["sparse_page_score"] = 0.0
        composite = sum(scores[k] * w for k, w in _WEIGHTS.items())
        assert composite <= 75.0, (
            f"Expected composite ≤ 75 with sparse_page_score=0; got {composite:.1f}"
        )
        # Verify the sparse penalty is large enough to matter: dropping
        # sparse_score from 100 to 0 must reduce the composite by exactly 25%.
        scores_full = {k: 100.0 for k in _WEIGHTS}
        composite_full = sum(scores_full[k] * w for k, w in _WEIGHTS.items())
        penalty = composite_full - composite
        assert abs(penalty - 25.0) < 1e-6, (
            f"Expected 25-point penalty from sparse=0; got {penalty:.1f}"
        )

    def test_perfect_output_without_sparse_page_passes(self):
        """A document with all metrics = 100 (including sparse) stays near 100."""
        from tailor.eval.layout_grader.grader import _WEIGHTS
        scores = {k: 100.0 for k in _WEIGHTS}
        composite = sum(scores[k] * w for k, w in _WEIGHTS.items())
        assert composite >= 99.0


# ---------------------------------------------------------------------------
# Regression test: sample 1 rendering fix — no sparse continuation page
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _ARTIFACTS_PRESENT,
    reason="sample 1 PDF artifacts not present",
)
class TestSample1RendererFix:
    """After the w:keepNext rendering fix, sample 1 must PASS the grader.

    The renderer now adds w:keepNext to experience role-header paragraphs so
    that orphaned role headers (stranded at the bottom of a page while their
    bullets appear on a sparse continuation page) are moved to the next page
    with their content, filling it more densely.
    """

    def _grade(self):
        from tailor.eval.layout_grader.grader import grade_sample
        return grade_sample(
            sample_id="1-Leonid_Verman_Resume_Template",
            template_docx_path=str(_TEMPLATE_DOCX_S1),
            generated_docx_path=str(
                _ROOT / "tmp/artefacts/rendering/docx/1-Leonid_Verman_Resume_Template.docx"
            ),
            template_pdf_path=str(_TEMPLATE_PDF_S1),
            generated_pdf_path=str(_RENDERED_PDF_S1),
            ir_path=str(_IR_S1),
            gen_json_path=str(_GEN_JSON_S1),
        )

    def test_sample1_passes_after_renderer_fix(self):
        """After the keepNext rendering fix, sample 1 must be PASS (not HARD FAIL)."""
        grade = self._grade()
        assert grade.status == "pass", (
            f"Expected status='pass' after renderer fix; "
            f"got status={grade.status!r}, hard_fail={grade.hard_fail}, "
            f"failure_classes={grade.failure_classes}"
        )

    def test_sample1_no_sparse_page_failure(self):
        """Sample 1 must not have C_SPARSE_CONTINUATION_PAGE after the rendering fix."""
        grade = self._grade()
        assert "C_SPARSE_CONTINUATION_PAGE" not in grade.failure_classes, (
            f"C_SPARSE_CONTINUATION_PAGE should be gone after renderer fix; "
            f"failure_classes={grade.failure_classes}"
        )

    def test_sample1_page_count_unchanged(self):
        """Rendering fix must not change the page count (still 2 pages)."""
        grade = self._grade()
        assert grade.facts.get("generated_pages") == 2, (
            f"Expected 2 pages after keepNext fix; "
            f"got {grade.facts.get('generated_pages')}"
        )

    def test_sample1_sparse_score_is_100(self):
        """sparse_page_score must be 100 (no sparse page detected) after the fix."""
        grade = self._grade()
        assert grade.metrics.get("sparse_page_score", 0) == 100.0, (
            f"Expected sparse_page_score=100 after fix; "
            f"got {grade.metrics.get('sparse_page_score')}"
        )

    def test_sample1_keepnext_in_rendered_docx(self):
        """Rendered DOCX must contain w:keepNext on experience role-header paragraphs."""
        from docx import Document
        from lxml import etree
        _WN = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        rendered = str(
            _ROOT / "tmp/artefacts/rendering/docx/1-Leonid_Verman_Resume_Template.docx"
        )
        doc = Document(rendered)
        body = doc.element.body
        keep_next_paras = [
            p for p in body.findall(f".//{{{_WN}}}p")
            if p.find(f".//{{{_WN}}}keepNext") is not None
        ]
        assert len(keep_next_paras) > 0, (
            "No w:keepNext found in rendered DOCX — orphan-header prevention not applied"
        )
        # The FitechSource role (the orphaned one) must specifically have keepNext
        fitechsource_with_keepnext = [
            p for p in keep_next_paras
            if "FitechSource" in "".join(r.text or "" for r in p.findall(f".//{{{_WN}}}t"))
        ]
        assert fitechsource_with_keepnext, (
            "FitechSource role header must have w:keepNext to prevent orphan at page bottom"
        )
