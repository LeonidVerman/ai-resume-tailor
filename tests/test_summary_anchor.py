"""Unit tests for _inject_llm_summary_into_header in pipeline.py.

Covers:
A. Contiguous block detection — _sum_left stops at first non-summary gap
B. In-place insertion — LLM summary lands at the original summary position
C. Non-summary header content preservation — WORK HISTORY / contact not dropped
D. No original summary → append heuristic using existing col
E. Single-column doc → early return (no injection)
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
from tailor.compiler.pipeline import _inject_llm_summary_into_header


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pp(
    col: "str | None",
    font_size: float = 10.0,
    para_id: str = "para_x",
    bold: bool = False,
) -> ParagraphProfile:
    return ParagraphProfile(
        column_id=col,
        indent_left_pt=0.0,
        font_size_pt=font_size,
        bold=bold,
    )


def _para(
    text: str,
    col: "str | None" = None,
    font_size: float = 10.0,
    para_id: str = "para_x",
    bold: bool = False,
    semantic: str = "paragraph",
) -> ParaModel:
    return ParaModel(
        text=text,
        style=ParaStyle(),
        semantic=semantic,
        paragraph_profile=_pp(col, font_size, para_id, bold),
        para_id=para_id,
    )


def _layout(col_split: "float | None" = 300.0) -> LayoutProfile:
    return LayoutProfile(
        page_width_pt=612.0,
        page_height_pt=792.0,
        margin_top_pt=72.0,
        margin_bottom_pt=72.0,
        margin_left_pt=36.0,
        margin_right_pt=36.0,
        default_font_name="Arial",
        default_font_size_pt=10.0,
        column_split_x=col_split,
    )


def _summary_section(body_texts: list[str], col: "str | None" = "left") -> ResumeSection:
    heading = _para("Professional Summary", col=col, para_id="")
    body = [_para(t, col=col, para_id="") for t in body_texts]
    return ResumeSection(
        title="Professional Summary",
        heading=heading,
        semantic_type="summary",
        body_paras=body,
        roles=[],
        section_id="",
    )


def _make_doc(
    header_paras: list[ParaModel],
    sections: list[ResumeSection],
    col_split: "float | None" = 300.0,
) -> ResumeDocument:
    all_paras: list[ParaModel] = list(header_paras)
    for sec in sections:
        all_paras.append(sec.heading)
        all_paras.extend(sec.body_paras)
    return ResumeDocument(
        layout=_layout(col_split),
        sections=sections,
        all_paras=all_paras,
        header_paras=header_paras,
        footer_paras=[],
    )


# ---------------------------------------------------------------------------
# A. Contiguous block detection
# ---------------------------------------------------------------------------

class TestContiguousBlockDetection:
    """The first contiguous run of summary-like text is replaced; later matching
    lines (role bullets that happen to look like prose) are left in place."""

    def test_summary_lines_followed_by_role_bullets_not_included(self):
        """Lines after a non-summary gap must not be included in summary_indices."""
        # Simulate: GENERAL(short) INFO(short) + summary prose + WORK(short) HISTORY(short)
        #           + role bullets (long prose that would match _is_original_summary_line)
        summary_text = (
            "Software engineer with extensive experience in cloud-based systems."
        )
        role_bullet = (
            "Led a cross-functional team to deliver high-performance APIs for clients."
        )
        header_paras = [
            _para("GENERAL", col="left", para_id="p1"),            # short → not sum
            _para("INFO", col="left", para_id="p2"),               # short → not sum
            _para(summary_text, col="left", para_id="p3"),         # summary line
            _para("WORK", col="left", para_id="p4"),               # short → gap → stop
            _para("HISTORY", col="left", para_id="p5"),            # beyond gap
            _para(role_bullet, col="left", para_id="p6"),          # beyond gap
        ]
        name_para = _para("JOHN", col="right", font_size=32.0, para_id="p7")
        header_paras.append(name_para)

        llm_summary = "New LLM summary text for the section."
        summary_sec = _summary_section([llm_summary])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        # Role bullet should still be in header_paras (not treated as summary)
        texts = [hp.text for hp in doc.header_paras]
        assert role_bullet in texts, "Role bullet should be preserved after the gap"
        assert summary_text not in texts, "Original summary prose should be replaced"
        assert llm_summary in texts, "LLM summary should be injected"

    def test_all_contiguous_summary_lines_replaced(self):
        """When multiple consecutive summary lines exist, all are replaced."""
        lines = [
            "A seasoned software engineer with expertise in backend systems.",
            "Known for delivering high-quality solutions on tight deadlines.",
            "Seeking a challenging role to leverage my skills.",
        ]
        header_paras = [
            _para("JOHN", col="right", font_size=32.0, para_id="ph1"),
        ] + [_para(t, col="left", para_id=f"ps{i}") for i, t in enumerate(lines)]

        llm_summary = "New LLM-generated professional summary."
        summary_sec = _summary_section([llm_summary])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        for orig in lines:
            assert orig not in texts, f"Original summary line should be removed: {orig!r}"
        assert llm_summary in texts, "LLM summary should appear"


# ---------------------------------------------------------------------------
# B. In-place insertion
# ---------------------------------------------------------------------------

class TestInPlaceInsertion:
    """LLM summary is inserted at the original summary position, not appended."""

    def test_summary_inserted_before_work_history(self):
        """When original summary is at the top of left col, LLM summary goes there,
        not after the work history content that follows."""
        summary_text = "Engineer with 10+ years of experience in materials innovation."
        work_heading = "WORK"
        work_role = "Mechanical Engineer at Warner & Spencer"
        work_bullet = "Increased efficiency by 15% by minimizing production bottlenecks."

        header_paras = [
            _para("GENERAL", col="left", para_id="p1"),
            _para("INFO", col="left", para_id="p2"),
            _para(summary_text, col="left", para_id="p3"),   # summary line
            _para(work_heading, col="left", para_id="p4"),   # gap: stops contiguous block
            _para(work_role, col="left", para_id="p5"),
            _para(work_bullet, col="left", para_id="p6"),
            _para("MATTHEW", col="right", font_size=32.0, para_id="p7"),
            _para("TURNER", col="right", font_size=32.0, para_id="p8"),
        ]

        llm_summary = "LLM-generated summary text goes here."
        summary_sec = _summary_section([llm_summary])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        llm_idx = texts.index(llm_summary)
        work_heading_idx = texts.index(work_heading)

        assert llm_idx < work_heading_idx, (
            "LLM summary should appear before WORK heading, not after"
        )

    def test_summary_appears_after_short_section_label(self):
        """Short non-summary labels before the summary (like 'GENERAL', 'INFO')
        should remain before the LLM summary in the output."""
        summary_text = "Experienced nurse specializing in pediatric care and patient advocacy."
        header_paras = [
            _para("PROFILE", col="left", para_id="p1"),   # short all-caps → not sum
            _para(summary_text, col="left", para_id="p2"),  # summary
        ]

        llm_summary = "Registered Nurse with clinical experience in pediatric settings."
        summary_sec = _summary_section([llm_summary])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        profile_idx = texts.index("PROFILE")
        llm_idx = texts.index(llm_summary)

        assert profile_idx < llm_idx, (
            "'PROFILE' label should remain before the injected LLM summary"
        )


# ---------------------------------------------------------------------------
# C. Non-summary header content preservation
# ---------------------------------------------------------------------------

class TestNonSummaryPreservation:
    """Non-summary items in header_paras (work history, contact info) must be kept."""

    def test_work_history_content_preserved(self):
        """Role bullets that come after the summary gap must remain in header_paras."""
        summary_text = "Software engineer with 8+ years building distributed systems."
        role_text = "Led a team of 12 engineers to redesign the order-processing platform."

        header_paras = [
            _para(summary_text, col="left", para_id="ps1"),
            _para("WORK", col="left", para_id="pw1"),            # gap
            _para("HISTORY", col="left", para_id="pw2"),
            _para(role_text, col="left", para_id="pb1"),         # role bullet
            _para("JANE", col="right", font_size=32.0, para_id="pn"),
        ]

        llm_body = "Updated LLM summary paragraph."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        assert role_text in texts, "Role bullet from original template should be preserved"
        assert "WORK" in texts
        assert "HISTORY" in texts

    def test_contact_info_in_left_header_preserved(self):
        """Contact-info items (email, phone) that appear before the summary in
        the left column must be kept even though they are col=left originals."""
        email_text = "john.smith@example.com"
        license_label = "LICENSE NO."
        summary_text = "I have experience in building software for the healthcare domain."

        header_paras = [
            _para(license_label, col="left", para_id="pl1"),
            _para(email_text, col="left", para_id="pe1"),
            _para(summary_text, col="left", para_id="ps1"),
        ]

        llm_body = "Experienced software engineer specializing in healthcare systems."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        assert license_label in texts, "LICENSE NO. label should be preserved"
        assert email_text in texts, "Email address should be preserved"
        assert summary_text not in texts, "Original summary prose should be replaced"
        assert llm_body in texts, "LLM summary should be injected"

    def test_right_col_header_items_preserved(self):
        """Right-column header items (phone, address) must be kept in the left-col
        injection path since they carry real contact information."""
        summary_text = "Engineer with 6+ years of experience in materials development."
        phone = "+123-456-7890"
        address = "123 Anywhere St., Any City"

        header_paras = [
            _para(summary_text, col="left", para_id="ps1"),
            _para("MATTHEW", col="right", font_size=32.0, para_id="pn1"),
            _para("TURNER", col="right", font_size=32.0, para_id="pn2"),
            _para(phone, col="right", para_id="pc1"),
            _para(address, col="right", para_id="pc2"),
        ]

        llm_body = "New LLM summary for the section."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        texts = [hp.text for hp in doc.header_paras]
        assert phone in texts, "Phone number should be preserved"
        assert address in texts, "Address should be preserved"
        assert "MATTHEW" in texts
        assert "TURNER" in texts


# ---------------------------------------------------------------------------
# D. No original summary → append heuristic
# ---------------------------------------------------------------------------

class TestNoOriginalSummaryAppend:
    def test_right_col_name_gives_right_target(self):
        """When header has a right-col name (no left-col summary), the LLM summary
        is appended to the right column."""
        header_paras = [
            _para("JOHN", col="right", font_size=32.0, para_id="p1"),
            _para("DOE", col="right", font_size=32.0, para_id="p2"),
        ]
        llm_body = "Backend engineer focused on scalable distributed systems."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        # LLM summary should be appended and have col=right
        summary_hp = next(
            (hp for hp in doc.header_paras if hp.text == llm_body), None
        )
        assert summary_hp is not None, "LLM summary should be in header_paras"
        assert summary_hp.paragraph_profile.column_id == "right"

    def test_left_col_contact_gives_left_target(self):
        """When header has left-col items only (no matching summary), target=left."""
        header_paras = [
            _para("S O F T W A R E E N G I N E E R", col="left", para_id="p1"),
        ]
        llm_body = "Backend engineer specializing in microservices and cloud infrastructure."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec])

        _inject_llm_summary_into_header(doc)

        summary_hp = next(
            (hp for hp in doc.header_paras if hp.text == llm_body), None
        )
        assert summary_hp is not None
        assert summary_hp.paragraph_profile.column_id == "left"


# ---------------------------------------------------------------------------
# E. Single-column doc → early return
# ---------------------------------------------------------------------------

class TestSingleColumnEarlyReturn:
    def test_single_col_doc_not_modified(self):
        """For single-column docs (col_split=None), the function returns early and
        the LLM summary section is kept in doc.sections unchanged."""
        header_paras = [
            _para("JOHN DOE", col=None, font_size=24.0, para_id="p1"),
        ]
        llm_body = "Engineer with extensive experience in backend development."
        summary_sec = _summary_section([llm_body])
        doc = _make_doc(list(header_paras), [summary_sec], col_split=None)

        _inject_llm_summary_into_header(doc)

        # Single-column: summary section kept in doc.sections
        assert any(s.semantic_type == "summary" for s in doc.sections), (
            "Summary section should remain in doc.sections for single-column docs"
        )
        # header_paras unchanged
        assert len(doc.header_paras) == 1
