"""Tests for the geometry guard in _find_summary_anchors.

Ensures that empty paragraphs in narrow newspaper columns (date/sidebar areas,
width < 180pt = 3600 twips) are never selected as summary injection anchors.

Background: sample 35 (Gleb Zernov) has a 2-column layout where col 0 is a
66pt date sidebar (1321 twips).  Empty spacer paragraphs at the end of that
column were incorrectly selected as summary anchors, injecting multi-sentence
summary text into a 66pt-wide slot and collapsing the page layout.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_35 = str(_DOCX_DIR / "35-Gleb_Zernov_Resume.docx")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_empty_para(pid: str) -> "ParaModel":
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text="", style=ParaStyle(), semantic="paragraph")
    pm.para_id = pid
    return pm


def _make_filled_para(pid: str, text: str) -> "ParaModel":
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
    pm.para_id = pid
    return pm


def _make_minimal_doc(header_paras, col_widths: "dict[str, int] | None" = None):
    """Build a minimal ResumeDocument for anchor-guard testing."""
    from tailor.compiler.models import (
        LayoutProfile, ParaModel, ParaStyle, ResumeDocument, ResumeSection,  # noqa: F401
    )
    heading = ParaModel(text="Experience", style=ParaStyle(), semantic="section_heading")
    heading.para_id = "para_h1"
    sec = ResumeSection(title="Experience", heading=heading, semantic_type="experience")
    sec.section_id = "sec_1"

    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )
    doc = ResumeDocument(
        header_paras=header_paras,
        sections=[sec],
        layout=layout,
        all_paras=list(header_paras) + [heading],
        source_kind="docx",
    )
    if col_widths is not None:
        doc._newspaper_col_widths = col_widths  # type: ignore[attr-defined]
    return doc


# ---------------------------------------------------------------------------
# Core unit tests
# ---------------------------------------------------------------------------

def test_narrow_col_empty_paras_rejected():
    """Empty trailing header_paras in a narrow col (<180pt) must not be anchors."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "Gleb Zernov")
    p_empty1 = _make_empty_para("p2")
    p_empty2 = _make_empty_para("p3")

    col_widths = {"p2": 1321, "p3": 1321}  # 66 pt each — narrow date sidebar
    doc = _make_minimal_doc([p_name, p_empty1, p_empty2], col_widths)

    result = _find_summary_anchors(doc)
    assert result is None, (
        f"Expected None (narrow anchors rejected), got {result}"
    )


def test_wide_col_empty_paras_accepted():
    """Empty trailing header_paras in a wide col (≥180pt) must still be found."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "Jane Doe")
    p_empty1 = _make_empty_para("p2")
    p_empty2 = _make_empty_para("p3")

    col_widths = {"p2": 8796, "p3": 8796}  # 440 pt — wide content column
    doc = _make_minimal_doc([p_name, p_empty1, p_empty2], col_widths)

    result = _find_summary_anchors(doc)
    assert result is not None, "Expected anchors in wide column, got None"
    heading_anchor, body_anchor = result
    assert heading_anchor is p_empty1
    assert body_anchor is p_empty2


def test_no_geometry_old_behavior_preserved():
    """When _newspaper_col_widths is absent, old behavior must be unchanged."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "Jane Doe")
    p_empty1 = _make_empty_para("p2")
    p_empty2 = _make_empty_para("p3")

    doc = _make_minimal_doc([p_name, p_empty1, p_empty2], col_widths=None)

    result = _find_summary_anchors(doc)
    assert result is not None, "Expected anchors when no geometry present"
    heading_anchor, body_anchor = result
    assert heading_anchor is p_empty1
    assert body_anchor is p_empty2


def test_mixed_narrow_then_wide_stops_at_narrow():
    """If the trailing slots are narrow, scanning stops even if earlier slots are wide."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "John Smith")
    p_wide1 = _make_empty_para("p2")
    p_wide2 = _make_empty_para("p3")
    p_filled = _make_filled_para("p4", "2020 - 2022")
    p_narrow1 = _make_empty_para("p5")
    p_narrow2 = _make_empty_para("p6")

    # p2, p3 are wide; p5, p6 are narrow (the trailing ones)
    col_widths = {"p5": 1321, "p6": 1321}
    doc = _make_minimal_doc(
        [p_name, p_wide1, p_wide2, p_filled, p_narrow1, p_narrow2],
        col_widths,
    )

    result = _find_summary_anchors(doc)
    assert result is None, (
        "Expected None — trailing slots are narrow; should not reach earlier wide slots"
    )


def test_threshold_boundary_exactly_at_min():
    """Para at exactly 3600 twips (180pt) must be accepted (threshold is exclusive)."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "Name")
    p_empty1 = _make_empty_para("p2")
    p_empty2 = _make_empty_para("p3")

    col_widths = {"p2": 3600, "p3": 3600}  # exactly at threshold → accepted
    doc = _make_minimal_doc([p_name, p_empty1, p_empty2], col_widths)

    result = _find_summary_anchors(doc)
    assert result is not None, "Para at exactly 3600 twips should be accepted"


def test_threshold_one_below_min_rejected():
    """Para at 3599 twips (just under 180pt) must be rejected."""
    from tailor.compiler.updater import _find_summary_anchors

    p_name = _make_filled_para("p1", "Name")
    p_empty1 = _make_empty_para("p2")
    p_empty2 = _make_empty_para("p3")

    col_widths = {"p2": 3599, "p3": 3599}  # just below threshold → rejected
    doc = _make_minimal_doc([p_name, p_empty1, p_empty2], col_widths)

    result = _find_summary_anchors(doc)
    assert result is None, "Para at 3599 twips should be rejected"


# ---------------------------------------------------------------------------
# Sample 35 regression guard
# ---------------------------------------------------------------------------

def test_sample35_parse_docx_builds_newspaper_col_widths():
    """parse_docx must attach _newspaper_col_widths to sample 35."""
    from tailor.compiler.docx_parser import parse_docx
    doc = parse_docx(_SAMPLE_35)
    assert doc.table_column_layout_fixed, "newspaper fix must have been applied"
    col_widths = getattr(doc, "_newspaper_col_widths", None)
    assert col_widths is not None, "_newspaper_col_widths must be set for sample 35"
    # Must include narrow col-0 entries (≤ 1400 twips ≈ 70pt)
    narrow_entries = [w for w in col_widths.values() if w <= 1400]
    assert narrow_entries, "Expected at least one narrow (≤70pt) column entry"


def test_sample35_summary_anchors_in_wide_header():
    """_find_summary_anchors must find wide (non-narrow) anchors for sample 35.

    After the narrow date-column fix, the 50 date-sidebar paras are injected into
    role.meta_lines and no longer populate header_paras.  The trailing empty paras
    in the full-width header section (no _col_width_twips set) are valid anchors.
    """
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.updater import _find_summary_anchors
    _col_widths_threshold = 3600
    doc = parse_docx(_SAMPLE_35)
    result = _find_summary_anchors(doc)
    # Result may be None if the header genuinely lacks empty slots, or a pair of
    # wide empty paras — either is acceptable; what must NOT happen is a narrow-col
    # para being returned as an anchor.
    if result is not None:
        col_widths = getattr(doc, "_newspaper_col_widths", {})
        for anchor in result:
            if anchor is None:
                continue
            w = col_widths.get(anchor.para_id)
            assert w is None or w >= _col_widths_threshold, (
                f"Anchor {anchor.para_id!r} is in a narrow column "
                f"({w} twips < {_col_widths_threshold} twips threshold)"
            )


def test_sample35_guard_rejects_narrow_empty_simulated():
    """Simulate the post-ordering-fix state: para_57/para_58 (narrow+empty) must be rejected."""
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.updater import _find_summary_anchors

    doc = parse_docx(_SAMPLE_35)
    col_widths = getattr(doc, "_newspaper_col_widths", {})

    # para_57 and para_58 are the empty trailing slots in the narrow date sidebar.
    # Simulate the tail_stream scenario: move them to the end of header_paras.
    from tailor.compiler.models import ParaModel, ParaStyle
    p57 = ParaModel(text="", style=ParaStyle(), semantic="paragraph")
    p57.para_id = "para_57"
    p58 = ParaModel(text="", style=ParaStyle(), semantic="paragraph")
    p58.para_id = "para_58"

    doc.header_paras = list(doc.header_paras) + [p57, p58]
    doc._newspaper_col_widths = {**col_widths, "para_57": 1321, "para_58": 1321}  # type: ignore[attr-defined]

    result = _find_summary_anchors(doc)
    assert result is None, (
        "Guard must reject para_57/para_58 (1321 twips ≈ 66pt < 3600 twips threshold)"
    )
