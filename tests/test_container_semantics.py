"""Tests for container_semantics inference.

Covers:
- _section_is_preserve: experience/summary always rewriteable
- _section_is_preserve: contact/skills/certifications always preserved
- _section_is_preserve: short-paragraph heuristic for unlabeled contact cards
- infer_container_semantics: single-column document classification
- infer_container_semantics: profile section not mis-classified as preserve
"""
from __future__ import annotations

import pytest

from tailor.compiler.container_semantics import (
    _section_is_preserve,
    infer_container_semantics,
)
from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParagraphProfile,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _para(text: str, semantic: str = "paragraph", para_id: str = "") -> ParaModel:
    return ParaModel(text=text, style=ParaStyle(), semantic=semantic, para_id=para_id)


def _sec(
    title: str,
    semantic_type: str = "other",
    body_texts: list[str] | None = None,
    roles: list[RoleEntry] | None = None,
) -> ResumeSection:
    body = [_para(t) for t in (body_texts or [])]
    return ResumeSection(
        title=title,
        heading=_para(title, "section_heading"),
        semantic_type=semantic_type,
        body_paras=body,
        roles=roles or [],
        section_id=f"sec_{title.lower().replace(' ', '_')}",
    )


def _doc(*sections: ResumeSection) -> ResumeDocument:
    layout = LayoutProfile(
        page_width_pt=612.0,
        page_height_pt=792.0,
        margin_left_pt=72.0,
        margin_right_pt=72.0,
        margin_top_pt=72.0,
        margin_bottom_pt=72.0,
        default_font_name="Calibri",
        default_font_size_pt=11.0,
        column_split_x=None,
        section_row_table=False,
        table_layout_mode=None,
    )
    sec_list = list(sections)
    all_paras: list[ParaModel] = []
    for s in sec_list:
        all_paras.append(s.heading)
        all_paras.extend(s.body_paras)
    doc = ResumeDocument(
        header_paras=[],
        sections=sec_list,
        layout=layout,
        all_paras=all_paras,
    )
    return doc


# ---------------------------------------------------------------------------
# _section_is_preserve
# ---------------------------------------------------------------------------

class TestSectionIsPreserve:
    def test_experience_always_rewriteable(self):
        sec = _sec("Work History", "experience")
        assert not _section_is_preserve(sec)

    def test_summary_always_rewriteable(self):
        """PROFILE (summary) must not be mis-classified as preserve even though
        'profile' appears in _PRESERVE_TITLE_KEYWORDS."""
        sec = _sec("PROFILE", "summary")
        assert not _section_is_preserve(sec)

    def test_summary_with_matching_keyword_still_rewriteable(self):
        """Summaries titled 'Profile Summary' remain rewriteable."""
        sec = _sec("Profile Summary", "summary")
        assert not _section_is_preserve(sec)

    def test_skills_preserved(self):
        sec = _sec("Technical Skills", "skills", body_texts=["Python, Java"])
        assert _section_is_preserve(sec)

    def test_contact_by_keyword(self):
        sec = _sec("Contact Info", "other", body_texts=["555-1234", "me@example.com"])
        assert _section_is_preserve(sec)

    def test_certifications_by_semantic_type(self):
        sec = _sec("Certs", "certifications", body_texts=["AWS Certified"])
        assert _section_is_preserve(sec)

    def test_short_body_heuristic(self):
        """A section with ≤6 short lines and no roles is treated as a contact card."""
        sec = _sec("Links", "other", body_texts=["linkedin.com/in/me", "github.com/me"])
        assert _section_is_preserve(sec)

    def test_other_semantic_type_always_preserved(self):
        """'other' is in _PRESERVE_SEMANTIC_TYPES so all other sections are preserved."""
        long_lines = [
            "This is a long line about my technical background in distributed systems."
        ] * 10
        sec = _sec("About", "other", body_texts=long_lines)
        # "other" semantic type is preserved regardless of content length.
        assert _section_is_preserve(sec)

    def test_experience_with_profile_title_not_preserved(self):
        """Experience sections titled 'Profile' (edge case) remain rewriteable."""
        sec = _sec("Profile", "experience")
        assert not _section_is_preserve(sec)


# ---------------------------------------------------------------------------
# infer_container_semantics — single-column
# ---------------------------------------------------------------------------

class TestInferSingleColumn:
    def test_experience_gets_rewriteable(self):
        doc = _doc(
            _sec("Experience", "experience"),
            _sec("Skills", "skills", body_texts=["Python"]),
        )
        tree = infer_container_semantics(doc)
        exp_sec = doc.sections[0]
        skills_sec = doc.sections[1]
        assert exp_sec.container_type == "rewriteable_region"
        assert skills_sec.container_type == "preserve_region"

    def test_summary_gets_rewriteable(self):
        doc = _doc(
            _sec("PROFILE", "summary", body_texts=["Experienced engineer..."]),
            _sec("EXPERIENCE", "experience"),
        )
        tree = infer_container_semantics(doc)
        assert doc.sections[0].container_type == "rewriteable_region"
        assert doc.sections[1].container_type == "rewriteable_region"

    def test_education_gets_none(self):
        """Education has neither experience nor a preserve keyword → None."""
        doc = _doc(
            _sec("Education", "education", body_texts=["BS Computer Science"]),
        )
        infer_container_semantics(doc)
        # education semantic_type is not in _REWRITEABLE_SEMANTIC_TYPES,
        # and "education" is not in _PRESERVE_TITLE_KEYWORDS.
        # Short body → short_body heuristic → preserve_region.
        assert doc.sections[0].container_type == "preserve_region"

    def test_other_section_gets_preserve_region(self):
        """'other' semantic type sections always get preserve_region."""
        long_lines = ["Line about professional achievements and metrics." * 3] * 7
        doc = _doc(
            _sec("Achievements", "other", body_texts=long_lines),
        )
        infer_container_semantics(doc)
        assert doc.sections[0].container_type == "preserve_region"

    def test_document_mode_is_none_for_single_col(self):
        doc = _doc(_sec("Experience", "experience"))
        tree = infer_container_semantics(doc)
        assert tree.document_mode is None

    def test_container_tree_returned(self):
        doc = _doc(_sec("Experience", "experience"), _sec("Skills", "skills"))
        tree = infer_container_semantics(doc)
        assert tree is not None
        assert isinstance(tree.containers, list)

    def test_section_row_table_sets_synchronized_row(self):
        doc = _doc(
            _sec("Experience", "experience"),
            _sec("Skills", "skills"),
        )
        doc.layout.section_row_table = True
        # Need paragraph_profile for column_id access inside the function.
        pp = ParagraphProfile(y_top_pt=100.0)
        for sec in doc.sections:
            sec.heading.paragraph_profile = pp
        infer_container_semantics(doc)
        for sec in doc.sections:
            assert sec.container_type == "synchronized_row"
