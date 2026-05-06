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
    page.height = page_height

    if content_top is not None and content_bottom is not None:
        page.content_bbox = (0.0, content_top, 612.0, content_bottom)
    else:
        page.content_bbox = None

    # Build mock lines
    lines = []
    for i in range(n_lines):
        ln = MagicMock()
        ln.text = f"Line {i} of content text here"
        lines.append(ln)
    page.lines = lines

    # Build mock blocks
    blocks = []
    for i in range(n_blocks):
        blk = MagicMock()
        blk.block_type = "paragraph"
        blk.lines = [lines[i]] if i < n_lines else []
        blocks.append(blk)
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
        page_num, fill, bottom_empty, n_lines, n_blocks, section = result[0]
        assert page_num == 2
        assert fill < 0.60
        assert bottom_empty > 0.30
        assert n_lines == 20

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
        score, evidence = self._call(p1, p2)
        assert score == 100.0
        assert evidence == []

    def test_sparse_page_score_zero(self):
        """Score is 0 when a qualifying sparse page is found."""
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)
        score, evidence = self._call(p1, p2)
        assert score == 0.0
        assert len(evidence) == 1
        assert "Sparse continuation page" in evidence[0]
        assert "page 2" in evidence[0]

    def test_evidence_contains_fill_and_empty_percentages(self):
        """Evidence message includes fill% and bottom-empty%."""
        p1 = _fake_page(1, 842, 50, 757)
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)
        _, evidence = self._call(p1, p2)
        msg = evidence[0]
        assert "fill" in msg
        assert "empty at bottom" in msg
        assert "lines" in msg
        assert "blocks" in msg

    def test_multiple_sparse_pages_all_reported(self):
        """Each qualifying sparse page produces its own evidence entry."""
        p1 = _fake_page(1, 842, 50, 757)          # 84% full
        p2 = _fake_page(2, 842, 50, 400, n_lines=20)  # sparse, prev 84% ✓
        p3 = _fake_page(3, 842, 50, 400, n_lines=20)  # sparse, prev 43% — NOT flagged
        score, evidence = self._call(p1, p2, p3)
        # Page 2 is flagged (after well-filled page 1)
        # Page 3 is NOT flagged (page 2 fill < 75%)
        assert score == 0.0
        assert len(evidence) == 1
        assert "page 2" in evidence[0]


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
# Regression test: sample 1 must not be PASS ≥ 99
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _ARTIFACTS_PRESENT,
    reason="sample 1 PDF artifacts not present",
)
class TestSample1SparsePageRegression:
    def test_sample1_grade_is_no_longer_pass_99(self):
        """Sample 1 must not receive PASS with composite ≥ 99 — sparse page detected."""
        import json
        from tailor.eval.layout_grader.grader import grade_sample

        grade = grade_sample(
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
        assert grade.status != "pass" or grade.composite_score < 75, (
            f"Sample 1 should no longer be PASS ≥ 75; got status={grade.status} "
            f"composite={grade.composite_score:.1f}"
        )

    def test_sample1_has_sparse_continuation_evidence(self):
        """Sample 1's grade evidence must mention sparse continuation page."""
        from tailor.eval.layout_grader.grader import grade_sample

        grade = grade_sample(
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
        evidence_text = " ".join(grade.evidence).lower()
        assert "sparse" in evidence_text, (
            f"Expected 'sparse' in evidence; got: {grade.evidence}"
        )
        assert "page 2" in evidence_text, (
            f"Expected 'page 2' mentioned in evidence; got: {grade.evidence}"
        )

    def test_sample1_failure_class_set(self):
        """Sample 1 must have C_SPARSE_CONTINUATION_PAGE failure class."""
        from tailor.eval.layout_grader.grader import grade_sample

        grade = grade_sample(
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
        assert "C_SPARSE_CONTINUATION_PAGE" in grade.failure_classes, (
            f"Expected C_SPARSE_CONTINUATION_PAGE in failure_classes; "
            f"got: {grade.failure_classes}"
        )

    def test_sample1_sparse_page_score_is_zero(self):
        """Sample 1's sparse_page_score metric must be 0 (defect detected)."""
        from tailor.eval.layout_grader.grader import grade_sample

        grade = grade_sample(
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
        assert grade.metrics.get("sparse_page_score", 100) == 0.0, (
            f"Expected sparse_page_score=0; got {grade.metrics.get('sparse_page_score')}"
        )
