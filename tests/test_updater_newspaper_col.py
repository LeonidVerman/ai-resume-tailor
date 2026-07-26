"""Tests for newspaper-column fixes in apply_tailored.

Covers two bugs fixed for sample 35 (two-column newspaper DOCX layout):

Bug 1 — _ext_ column-break inheritance:
  Extra layout blocks (_ext_) cloned from a column-start anchor paragraph
  must not inherit <w:br type="column"/>; doing so pushes generated bullets
  to the wrong column/page.

Bug 2 — DATE_COL_HEADER_CLEARED overwrites visible date sidebar:
  When _dates_injected=True, apply_tailored previously cleared ALL
  date-fragment header_paras.  For newspaper templates the narrow left column
  IS the visible date display, so those paragraphs must be preserved.
  Guard: skip clearing when the paragraph is in a narrow column
  (< 3600 twips) per original._newspaper_col_widths.

Tests:
  1. test_s35_date_sidebar_text_preserved
  2. test_date_col_narrow_not_cleared_when_col_widths_set
  3. test_date_col_para_cleared_when_no_col_widths
  4. test_ext_blocks_strip_column_break
  5. test_s35_layout_blocks_col_break_count
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR  = Path(__file__).parent / "samples" / "resume" / "docx"
_GEN_DIR   = Path(__file__).parent / "samples" / "generation"

_S35_DOCX  = _DOCX_DIR / "35-Gleb_Zernov_Resume.docx"
_S35_GEN   = _GEN_DIR  / "35-Gleb_Zernov-American_Tire_Distributors-Lead_Software_Engineer-444-20260609-204948.json"

_S35_AVAIL = _S35_DOCX.exists() and _S35_GEN.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _para(text: str, semantic: str = "paragraph", para_id: str = ""):
    from tailor.compiler.models import ParaModel, ParaStyle
    return ParaModel(text=text, style=ParaStyle(), semantic=semantic, para_id=para_id)


def _load_s35():
    """Parse sample 35 template and load matching generation JSON."""
    import json
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.pipeline import apply_layout_fitting
    from tailor.compiler.classification_models import ClassificationOutput

    original = parse_docx(str(_S35_DOCX))
    with open(_S35_GEN, encoding="utf-8") as fh:
        gen = json.load(fh)
    llm_text = gen["llm_response"]["resume"]
    cls_data = gen.get("structured_resume", {}).get("classification")
    classification = ClassificationOutput.from_dict(cls_data) if cls_data else None
    llm_secs = apply_layout_fitting(original, parse_llm_output(llm_text))
    return original, llm_secs, classification


def _make_newspaper_doc(col_widths: "dict[str, int] | None"):
    """Minimal doc with a date-like header_para and one experience role.

    The header_para ('para_date_1', text='February 2026') sits in a column
    whose width is given by col_widths.  The experience role has no dates in
    header or meta lines, so when the LLM provides '... | February 2026'
    the _dates_injected flag fires and the cleanup path is exercised.
    """
    from tailor.compiler.classification_models import (
        ClassificationBlock,
        ClassificationOutput,
        ClassificationRole,
        ClassificationSection,
    )
    from tailor.compiler.models import (
        LayoutParagraphBlock,
        LayoutProfile,
        ParaModel,
        ParaStyle,
        ResumeDocument,
        ResumeSection,
        RoleEntry,
    )
    from tailor.compiler.text_parser import LlmRole, LlmSection

    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )

    date_hp  = _para("February 2026", "header", "para_date_1")
    exp_head = _para("Experience", "section_heading", "para_exp_h")
    r_header = _para("Software Engineer", "role_header", "para_r1_h")
    r_bullet = _para("Did existing work.", "bullet", "para_r1_b1")

    role = RoleEntry(
        header=r_header,
        meta_lines=[],
        bullets=[r_bullet],
        role_id="Software Engineer",
        role_id_stable="Software Engineer",
    )
    exp_sec = ResumeSection(
        title="Experience",
        heading=exp_head,
        semantic_type="experience",
        roles=[role],
        section_id="sec_exp",
    )

    all_paras = [date_hp, exp_head, r_header, r_bullet]
    doc = ResumeDocument(
        header_paras=[date_hp],
        sections=[exp_sec],
        layout=layout,
        all_paras=all_paras,
        layout_blocks=[
            LayoutParagraphBlock(para_id=p.para_id)
            for p in all_paras if p.para_id
        ],
    )
    if col_widths is not None:
        doc._newspaper_col_widths = col_widths  # type: ignore[attr-defined]

    # LLM provides a date suffix → triggers _dates_injected in classified path
    llm_sec = LlmSection(
        heading="Experience",
        semantic_type="experience",
        roles=[LlmRole(
            header="Software Engineer | February 2026 – current",
            meta_lines=[],
            bullets=["Did new work."],
        )],
        body_lines=[],
    )

    # Classification: update bullets only
    cls = ClassificationOutput(
        document_id="test",
        classification_version="1.0",
        source_kind="docx",
        sections=[
            ClassificationSection(
                section_id="sec_exp",
                raw_title="Experience",
                display_title="Experience",
                semantic_type="experience",
                rewrite_policy="update",
                preserve_heading=False,
                preserve_body_structure=False,
                roles=[
                    ClassificationRole(
                        role_id="Software Engineer",
                        body_blocks=[
                            ClassificationBlock(
                                block_id="blk_r1_b1",
                                para_id="para_r1_b1",
                                semantic_type="bullet",
                                rewrite_policy="update",
                            )
                        ],
                    )
                ],
            )
        ],
    )

    return doc, [llm_sec], cls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 DOCX/gen not found")
def test_s35_date_sidebar_text_preserved():
    """Bug 2: date-fragment sidebar paragraphs retain original text after apply_tailored.

    Sample 35 has 22 date-fragment header_paras (e.g. 'February 2026 –',
    'current', 'Full-time') in a 1321-twip left column.  Before the fix,
    all 22 were cleared to '' by DATE_COL_HEADER_CLEARED.

    Only _DATE_COL_PARA_RE-matching paragraphs are checked; other header_paras
    may legitimately be cleared by unrelated paths (e.g. icon-artifact cleanup).
    """
    import re
    from tailor.compiler.updater import apply_tailored, _DATE_COL_PARA_RE

    original, llm_secs, classification = _load_s35()

    orig_date_texts = {
        pm.para_id: pm.text
        for pm in original.header_paras
        if pm.para_id and pm.text.strip()
        and _DATE_COL_PARA_RE.match(pm.text.strip())
    }

    updated = apply_tailored(original, llm_secs, classification=classification)

    upd_hp = {pm.para_id: pm for pm in updated.header_paras if pm.para_id}

    cleared = [
        pid for pid, orig_text in orig_date_texts.items()
        if upd_hp.get(pid) is not None and upd_hp[pid].text == ""
    ]

    assert not cleared, (
        f"{len(cleared)} date sidebar para(s) were incorrectly cleared: "
        + ", ".join(f"{p}={orig_date_texts[p]!r}" for p in cleared[:5])
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 DOCX/gen not found")
def test_s35_date_sidebar_count():
    """Bug 2 (count): all 22 date-fragment sidebar paras survive apply_tailored."""
    from tailor.compiler.updater import apply_tailored, _DATE_COL_PARA_RE

    original, llm_secs, classification = _load_s35()

    orig_date_count = sum(
        1 for pm in original.header_paras
        if pm.para_id and pm.text.strip()
        and _DATE_COL_PARA_RE.match(pm.text.strip())
    )

    updated = apply_tailored(original, llm_secs, classification=classification)

    surviving = sum(
        1 for pm in updated.header_paras
        if pm.para_id and pm.text.strip()
        and _DATE_COL_PARA_RE.match(pm.text.strip())
    )

    assert surviving == orig_date_count, (
        f"Expected {orig_date_count} date-fragment sidebar paras, got {surviving}"
    )


def test_date_col_narrow_not_cleared_when_col_widths_set():
    """Bug 2 (unit): date-like header_para in narrow col preserved even when _dates_injected.

    When _newspaper_col_widths maps the para to a width < 3600 twips,
    DATE_COL_HEADER_CLEARED must skip it.
    """
    from tailor.compiler.updater import apply_tailored

    # 1321 twips is sample 35's actual left-column width
    doc, llm_secs, cls = _make_newspaper_doc(col_widths={"para_date_1": 1321})
    updated = apply_tailored(doc, llm_secs, classification=cls)

    hp_map = {pm.para_id: pm for pm in updated.header_paras}
    pm = hp_map.get("para_date_1")
    assert pm is not None, "para_date_1 missing from updated.header_paras"
    assert pm.text == "February 2026", (
        f"Expected 'February 2026', got {pm.text!r} — narrow-col para was wrongly cleared"
    )


def test_date_col_para_cleared_when_no_col_widths():
    """Bug 2 (unit): date-like header_para is still cleared when _newspaper_col_widths absent.

    When the document has no _newspaper_col_widths attribute (non-newspaper
    template), the old behaviour is preserved: orphaned date-fragment header
    paras are cleared when _dates_injected fires.
    """
    from tailor.compiler.updater import apply_tailored

    doc, llm_secs, cls = _make_newspaper_doc(col_widths=None)
    updated = apply_tailored(doc, llm_secs, classification=cls)

    hp_map = {pm.para_id: pm for pm in updated.header_paras}
    pm = hp_map.get("para_date_1")
    assert pm is not None, "para_date_1 missing from updated.header_paras"
    assert pm.text == "", (
        f"Expected '' (cleared), got {pm.text!r} — non-newspaper date para should be cleared"
    )


def test_date_col_wide_col_cleared():
    """Bug 2 (unit): date-like header_para in a WIDE column (>= 3600 twips) is cleared.

    Only paragraphs in narrow columns are protected; wide-column date fragments
    are still removed as before.
    """
    from tailor.compiler.updater import apply_tailored

    # 5000 twips > 3600 threshold → not a narrow sidebar
    doc, llm_secs, cls = _make_newspaper_doc(col_widths={"para_date_1": 5000})
    updated = apply_tailored(doc, llm_secs, classification=cls)

    hp_map = {pm.para_id: pm for pm in updated.header_paras}
    pm = hp_map.get("para_date_1")
    assert pm is not None, "para_date_1 missing from updated.header_paras"
    assert pm.text == "", (
        f"Expected '' (cleared), got {pm.text!r} — wide-col date para should be cleared"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 DOCX/gen not found")
def test_ext_blocks_strip_column_break():
    """Bug 1: _ext_ layout blocks must not contain <w:br type='column'/>.

    Sample 35 role para_121 (FitechSource) starts the newspaper right column
    and carries a column break.  Before the fix, para_121_ext_1 and
    para_121_ext_2 inherited that break verbatim, pushing generated bullets
    to the wrong column/page.
    """
    from tailor.compiler.updater import apply_tailored

    original, llm_secs, classification = _load_s35()
    updated = apply_tailored(original, llm_secs, classification=classification)

    assert updated.layout_blocks is not None, "layout_blocks should be present"

    offenders = [
        lb.para_id
        for lb in updated.layout_blocks
        if getattr(lb, "para_id", "") and "_ext_" in (lb.para_id or "")
        and getattr(lb, "xml_proto_xml", None)
        and 'type="column"' in lb.xml_proto_xml
    ]

    assert not offenders, (
        f"_ext_ layout block(s) still carry column-break: {offenders}"
    )


@pytest.mark.skipif(not _S35_AVAIL, reason="sample 35 DOCX/gen not found")
def test_s35_layout_blocks_col_break_count():
    """Bug 1 (count): updated layout_blocks should have exactly 2 col-break protos.

    The template has 2 column-break paragraphs (para_120, para_121).  Before
    the fix, 2 spurious breaks were added via _ext_ blocks (total 4).
    After the fix the count must remain 2.
    """
    from tailor.compiler.updater import apply_tailored

    original, llm_secs, classification = _load_s35()

    # Baseline: count col-breaks in the ORIGINAL layout_blocks
    orig_cb_count = sum(
        1 for lb in (original.layout_blocks or [])
        if getattr(lb, "xml_proto_xml", None)
        and 'type="column"' in lb.xml_proto_xml
    )

    updated = apply_tailored(original, llm_secs, classification=classification)

    upd_cb_count = sum(
        1 for lb in (updated.layout_blocks or [])
        if getattr(lb, "xml_proto_xml", None)
        and 'type="column"' in lb.xml_proto_xml
    )

    assert upd_cb_count == orig_cb_count, (
        f"Expected {orig_cb_count} col-break layout block(s) (same as template), "
        f"got {upd_cb_count} — _ext_ blocks may still carry column-break"
    )
