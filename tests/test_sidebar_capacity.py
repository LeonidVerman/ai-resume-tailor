"""Unit tests for sidebar capacity guard helpers in pipeline.py.

Covers:
A. _estimate_col_section_height — height estimation based on column width and text length
B. _sidebar_overflow_check — detects when items wrap more than max_wrap_lines
C. _fix_extra_left_sections — capacity guard moves verbose skills to right column
"""
from __future__ import annotations

import pytest

from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParaStyle,
    ParagraphProfile,
    ResumeDocument,
    ResumeSection,
)
from tailor.compiler.pipeline import (
    _estimate_col_section_height,
    _fix_extra_left_sections,
    _sidebar_overflow_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pp(col: str, font_size: float = 10.0, space_before: float = 0.0) -> ParagraphProfile:
    return ParagraphProfile(
        column_id=col,
        indent_left_pt=0.0,
        font_size_pt=font_size,
        space_before_pt=space_before,
    )


def _para(text: str, col: str = "left", font_size: float = 10.0, space_before: float = 0.0) -> ParaModel:
    return ParaModel(
        text=text,
        style=ParaStyle(),
        semantic="paragraph",
        paragraph_profile=_pp(col, font_size, space_before),
    )


def _section(title: str, sem: str, body_texts: list[str], col: str = "left",
             section_id: str = "") -> ResumeSection:
    heading = _para(title, col)
    body = [_para(t, col) for t in body_texts]
    sec = ResumeSection(
        title=title,
        heading=heading,
        semantic_type=sem,
        body_paras=body,
        roles=[],
        section_id=section_id,
    )
    return sec


def _layout(col_split: float = 200.0, page_h: float = 792.0,
            margin_t: float = 72.0, margin_b: float = 72.0) -> LayoutProfile:
    return LayoutProfile(
        page_width_pt=612.0,
        page_height_pt=page_h,
        margin_top_pt=margin_t,
        margin_bottom_pt=margin_b,
        margin_left_pt=36.0,
        margin_right_pt=36.0,
        default_font_name="Arial",
        default_font_size_pt=10.0,
        column_split_x=col_split,
    )


def _doc(layout: LayoutProfile, sections: list[ResumeSection]) -> ResumeDocument:
    all_paras = []
    for sec in sections:
        all_paras.append(sec.heading)
        all_paras.extend(sec.body_paras)
    return ResumeDocument(
        layout=layout,
        sections=sections,
        all_paras=all_paras,
        header_paras=[],
        footer_paras=[],
    )


# ---------------------------------------------------------------------------
# A. _estimate_col_section_height
# ---------------------------------------------------------------------------

class TestEstimateColSectionHeight:
    def test_single_short_para(self):
        sec = _section("Skills", "skills", ["Python"])
        h = _estimate_col_section_height(sec, col_width_pt=200.0, default_font_size_pt=10.0)
        # heading: 10 * 1.3 = 13pt, body: 10 * 1.3 = 13pt → total ~26pt
        assert 20.0 <= h <= 35.0

    def test_empty_paras_skipped(self):
        sec = _section("Skills", "skills", ["", "   ", "Python"])
        h = _estimate_col_section_height(sec, col_width_pt=200.0, default_font_size_pt=10.0)
        # only heading + one real body para
        assert h > 0.0

    def test_long_text_wraps(self):
        short_sec = _section("Skills", "skills", ["Python"])
        long_sec = _section("Skills", "skills", ["A" * 200])  # very long line
        h_short = _estimate_col_section_height(short_sec, col_width_pt=100.0, default_font_size_pt=10.0)
        h_long = _estimate_col_section_height(long_sec, col_width_pt=100.0, default_font_size_pt=10.0)
        assert h_long > h_short

    def test_larger_font_increases_height(self):
        # Build sections with explicit font size via paragraph_profile
        def _sec_with_font(fs: float) -> ResumeSection:
            heading = _para("Skills", font_size=fs)
            body = [_para("Python", font_size=fs)]
            sec = ResumeSection(
                title="Skills", heading=heading, semantic_type="skills",
                body_paras=body, roles=[], section_id="",
            )
            return sec

        h12 = _estimate_col_section_height(_sec_with_font(12.0), col_width_pt=200.0, default_font_size_pt=12.0)
        h10 = _estimate_col_section_height(_sec_with_font(10.0), col_width_pt=200.0, default_font_size_pt=10.0)
        assert h12 > h10

    def test_space_before_adds_to_height(self):
        sec_sp = _section("Skills", "skills", [])
        sec_sp.body_paras = [_para("Python", space_before=20.0)]
        sec_nosp = _section("Skills", "skills", [])
        sec_nosp.body_paras = [_para("Python", space_before=0.0)]
        h_sp = _estimate_col_section_height(sec_sp, col_width_pt=200.0, default_font_size_pt=10.0)
        h_nosp = _estimate_col_section_height(sec_nosp, col_width_pt=200.0, default_font_size_pt=10.0)
        assert h_sp > h_nosp


# ---------------------------------------------------------------------------
# B. _sidebar_overflow_check
# ---------------------------------------------------------------------------

class TestSidebarOverflowCheck:
    def test_short_item_does_not_overflow(self):
        sec = _section("Skills", "skills", ["Python"])
        # Python is 6 chars, col=200pt with 10pt font: ~36 chars/line → 1 line
        assert _sidebar_overflow_check(sec, col_width_pt=200.0, default_font_size_pt=10.0,
                                       max_wrap_lines=3) is False

    def test_very_long_item_overflows(self):
        # 300 chars in a 100pt column with 10pt font: ~18 chars/line → ~17 lines > 3
        sec = _section("Skills", "skills", ["X" * 300])
        assert _sidebar_overflow_check(sec, col_width_pt=100.0, default_font_size_pt=10.0,
                                       max_wrap_lines=3) is True

    def test_empty_body_does_not_overflow(self):
        sec = _section("Skills", "skills", [])
        assert _sidebar_overflow_check(sec, col_width_pt=100.0, default_font_size_pt=10.0,
                                       max_wrap_lines=3) is False

    def test_mixed_items_triggers_on_long_one(self):
        # First item short, second item very long
        sec = _section("Skills", "skills", ["Short", "B" * 300])
        assert _sidebar_overflow_check(sec, col_width_pt=100.0, default_font_size_pt=10.0,
                                       max_wrap_lines=3) is True

    def test_custom_max_wrap_lines(self):
        # 60 chars in 100pt at 10pt font: ~18 chars/line → ~4 lines
        sec = _section("Skills", "skills", ["X" * 60])
        assert _sidebar_overflow_check(sec, col_width_pt=100.0, default_font_size_pt=10.0,
                                       max_wrap_lines=2) is True
        assert _sidebar_overflow_check(sec, col_width_pt=100.0, default_font_size_pt=10.0,
                                       max_wrap_lines=10) is False


# ---------------------------------------------------------------------------
# C. _fix_extra_left_sections — capacity guard integration
# ---------------------------------------------------------------------------

class TestFixExtraLeftSectionsCapacityGuard:
    def _make_template_with_left_sidebar(self) -> ResumeDocument:
        """Template: CONTACT (other/left) + EDUCATION (education/right)."""
        contact = _section("CONTACT", "other", ["email@example.com"], col="left", section_id="sec_1")
        education = _section("EDUCATION", "education", ["BSc Computer Science"], col="right", section_id="sec_2")
        layout = _layout(col_split=225.0)
        return _doc(layout, [contact, education])

    def test_short_skills_stays_in_sidebar(self):
        template = self._make_template_with_left_sidebar()
        # Extra skills section with short items (will fit in sidebar)
        skills = _section("Technical Skills", "skills", ["Python", "AWS", "Docker"], col="left")
        layout = _layout(col_split=225.0)
        updated = _doc(layout, [
            _section("CONTACT", "other", ["email@example.com"], col="left", section_id="sec_1"),
            _section("EDUCATION", "education", ["BSc Computer Science"], col="right", section_id="sec_2"),
            skills,
        ])
        _fix_extra_left_sections(template, updated)
        h_pp = skills.heading.paragraph_profile
        assert h_pp is not None
        assert h_pp.column_id == "left", "Short skill items should stay in sidebar"

    def test_verbose_skills_moves_to_right(self):
        template = self._make_template_with_left_sidebar()
        # Extra skills section with verbose sentence-length items (will overflow sidebar)
        verbose_items = ["Clinical Care " + "W" * 120, "Patient management " + "X" * 120]
        skills = _section("Technical Skills", "skills", verbose_items, col="left")
        layout = _layout(col_split=225.0)
        updated = _doc(layout, [
            _section("CONTACT", "other", ["email@example.com"], col="left", section_id="sec_1"),
            _section("EDUCATION", "education", ["BSc Computer Science"], col="right", section_id="sec_2"),
            skills,
        ])
        _fix_extra_left_sections(template, updated)
        h_pp = skills.heading.paragraph_profile
        assert h_pp is not None
        assert h_pp.column_id == "right", "Verbose skill items should move to right column"
        for bp in skills.body_paras:
            assert bp.paragraph_profile is not None
            assert bp.paragraph_profile.column_id == "right"

    def test_no_left_sidebar_template_moves_skills_to_right(self):
        """When template has no left sidebar (only experience/education in left),
        extra skills should always go to right regardless of content length."""
        education = _section("EDUCATION", "education", ["BSc CS"], col="left", section_id="sec_1")
        experience = _section("EXPERIENCE", "experience", [], col="left", section_id="sec_2")
        contact = _section("CONTACT", "other", ["email@x.com"], col="right", section_id="sec_3")
        layout = _layout(col_split=382.0)
        template = _doc(layout, [education, experience, contact])

        # Short skills — no left sidebar, so should still be moved to right
        skills = _section("Technical Skills", "skills", ["Python", "AWS"], col="left")
        updated = _doc(layout, [
            _section("EDUCATION", "education", ["BSc CS"], col="left", section_id="sec_1"),
            _section("EXPERIENCE", "experience", [], col="left", section_id="sec_2"),
            _section("CONTACT", "other", ["email@x.com"], col="right", section_id="sec_3"),
            skills,
        ])
        _fix_extra_left_sections(template, updated)
        h_pp = skills.heading.paragraph_profile
        assert h_pp is not None
        assert h_pp.column_id == "right", "No-sidebar template: skills must go to right"

    def test_single_column_doc_unchanged(self):
        """For single-column documents (column_split_x=None), nothing should change."""
        layout = LayoutProfile(
            page_width_pt=612.0, page_height_pt=792.0,
            margin_top_pt=72.0, margin_bottom_pt=72.0,
            margin_left_pt=72.0, margin_right_pt=72.0,
            default_font_name="Arial", default_font_size_pt=10.0,
            column_split_x=None,
        )
        template_sections = [_section("SKILLS", "skills", ["Python"], col="left", section_id="sec_1")]
        template = _doc(layout, template_sections)
        skills = _section("Technical Skills", "skills", ["X" * 300], col="left")
        updated_sections = [
            _section("SKILLS", "skills", ["Python"], col="left", section_id="sec_1"),
            skills,
        ]
        updated = _doc(layout, updated_sections)
        _fix_extra_left_sections(template, updated)
        # Nothing moves — no column_split_x
        h_pp = skills.heading.paragraph_profile
        assert h_pp is not None
        assert h_pp.column_id == "left", "Single-column: should not move any sections"
