"""Tests for classification-constrained apply_tailored behavior.

Verifies that when a ClassificationOutput is provided:
- experience role headers and meta lines are never modified
- only bullet text is updated
- extra LLM roles are ignored; extra IR roles are kept verbatim
- sections with rewrite_policy="preserve" are returned unchanged
- preserve_heading prevents heading text updates
- preserve_body_structure prevents add/remove of body paragraphs
- missing classification → behavior identical to unconstrained

All tests use in-memory IR objects (no file I/O).
"""
from __future__ import annotations

import pytest

from tailor.compiler.classification_models import (
    ClassificationOutput,
    ClassificationSection,
    ClassificationRole,
    ClassificationBlock,
)
from tailor.compiler.models import (
    LayoutProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)
from tailor.compiler.text_parser import LlmRole, LlmSection
from tailor.compiler.updater import apply_tailored


# ── IR construction helpers ───────────────────────────────────────────────

def _para(text: str, semantic: str = "paragraph", para_id: str = "") -> ParaModel:
    return ParaModel(text=text, style=ParaStyle(), semantic=semantic, para_id=para_id)


def _role(header: str, meta: str, bullets: list[str], role_id: str = "") -> RoleEntry:
    return RoleEntry(
        header=_para(header, "role_header"),
        meta_lines=[_para(meta, "role_meta")],
        bullets=[_para(b, "bullet") for b in bullets],
        role_id=role_id or header,
        role_id_stable=role_id or header,
    )


def _experience_section(section_id: str, roles: list[RoleEntry]) -> ResumeSection:
    return ResumeSection(
        title="Experience",
        heading=_para("Experience", "section_heading"),
        semantic_type="experience",
        roles=roles,
        section_id=section_id,
    )


def _body_section(
    section_id: str,
    title: str,
    semantic_type: str,
    lines: list[str],
) -> ResumeSection:
    return ResumeSection(
        title=title,
        heading=_para(title, "section_heading"),
        semantic_type=semantic_type,
        body_paras=[_para(l) for l in lines],
        section_id=section_id,
    )


_LAYOUT = LayoutProfile(
    page_width_pt=612, page_height_pt=792,
    margin_top_pt=72, margin_bottom_pt=72,
    margin_left_pt=72, margin_right_pt=72,
    default_font_name="Calibri", default_font_size_pt=11.0,
)


def _doc(sections: list[ResumeSection]) -> ResumeDocument:
    all_paras = []
    for sec in sections:
        all_paras.append(sec.heading)
        for bp in sec.body_paras:
            all_paras.append(bp)
        for r in sec.roles:
            all_paras.append(r.header)
            all_paras.extend(r.meta_lines)
            all_paras.extend(r.bullets)
    return ResumeDocument(
        header_paras=[],
        sections=sections,
        layout=_LAYOUT,
        all_paras=all_paras,
    )


# ── Classification construction helpers ──────────────────────────────────

def _cls_output(sections: list[ClassificationSection]) -> ClassificationOutput:
    return ClassificationOutput(
        document_id="test",
        classification_version="1.0",
        source_kind="docx",
        sections=sections,
    )


def _cls_exp(
    section_id: str,
    rewrite_policy: str = "rewrite_bullets_only",
    preserve_heading: bool = True,
) -> ClassificationSection:
    return ClassificationSection(
        section_id=section_id,
        raw_title="Experience",
        display_title="Experience",
        semantic_type="experience",
        rewrite_policy=rewrite_policy,
        preserve_heading=preserve_heading,
        preserve_body_structure=False,
    )


def _cls_body(
    section_id: str,
    semantic_type: str = "summary",
    rewrite_policy: str = "rewrite_body",
    preserve_heading: bool = True,
    preserve_body_structure: bool = False,
) -> ClassificationSection:
    return ClassificationSection(
        section_id=section_id,
        raw_title=semantic_type.capitalize(),
        display_title=semantic_type.capitalize(),
        semantic_type=semantic_type,
        rewrite_policy=rewrite_policy,
        preserve_heading=preserve_heading,
        preserve_body_structure=preserve_body_structure,
    )


# ── LLM output helpers ────────────────────────────────────────────────────

def _llm_role(header: str, meta: str, bullets: list[str]) -> LlmRole:
    return LlmRole(header=header, meta_lines=[meta], bullets=bullets)


def _llm_exp(heading: str, roles: list[LlmRole]) -> LlmSection:
    return LlmSection(heading=heading, semantic_type="experience", roles=roles, body_lines=[])


def _llm_body(heading: str, semantic_type: str, lines: list[str]) -> LlmSection:
    return LlmSection(heading=heading, semantic_type=semantic_type, roles=[], body_lines=lines)


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestExperienceClassified:
    """Experience section behavior under classification."""

    def test_bullets_updated(self):
        """Bullet text is replaced with LLM output."""
        orig_role = _role("Eng | Corp", "2020–2022", ["Old bullet 1", "Old bullet 2"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [_llm_role("Eng | Corp", "2020–2022", ["New bullet 1", "New bullet 2"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        exp = result.sections[0]
        assert exp.roles[0].bullets[0].text == "New bullet 1"
        assert exp.roles[0].bullets[1].text == "New bullet 2"

    def test_header_never_modified(self):
        """Role header text is never changed even when LLM writes different text."""
        orig_role = _role("Original Header | Corp", "2020–2022", ["bullet"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [_llm_role("DIFFERENT HEADER | Corp", "2020–2022", ["new bullet"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].roles[0].header.text == "Original Header | Corp"

    def test_meta_lines_never_modified(self):
        """Role meta lines (dates/location) are never changed."""
        orig_role = _role("Eng | Corp", "Jan 2020 – Dec 2022", ["bullet"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [_llm_role("Eng | Corp", "DIFFERENT DATE", ["new bullet"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].roles[0].meta_lines[0].text == "Jan 2020 – Dec 2022"

    def test_extra_llm_roles_ignored(self):
        """When LLM produces more roles than IR, extra LLM roles are ignored."""
        orig_roles = [
            _role("Eng | Corp A", "2022–2024", ["bullet A"], "r1"),
            _role("Dev | Corp B", "2020–2022", ["bullet B"], "r2"),
        ]
        doc = _doc([_experience_section("sec_1", orig_roles)])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [
            _llm_role("Eng | Corp A", "2022–2024", ["new A"]),
            _llm_role("Dev | Corp B", "2020–2022", ["new B"]),
            _llm_role("Extra Role | Corp C", "2018–2020", ["extra bullet"]),  # extra
        ])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        exp = result.sections[0]
        assert len(exp.roles) == 2
        assert exp.roles[0].bullets[0].text == "new A"
        assert exp.roles[1].bullets[0].text == "new B"

    def test_extra_ir_roles_kept_verbatim(self):
        """When LLM produces fewer roles than IR, extra IR roles are kept unchanged."""
        orig_roles = [
            _role("Eng | Corp A", "2022–2024", ["bullet A"], "r1"),
            _role("Dev | Corp B", "2020–2022", ["bullet B"], "r2"),
            _role("Junior | Corp C", "2018–2020", ["bullet C"], "r3"),
        ]
        doc = _doc([_experience_section("sec_1", orig_roles)])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [
            _llm_role("Eng | Corp A", "2022–2024", ["new A"]),
            _llm_role("Dev | Corp B", "2020–2022", ["new B"]),
            # LLM omits the third role
        ])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        exp = result.sections[0]
        assert len(exp.roles) == 3
        assert exp.roles[0].bullets[0].text == "new A"
        assert exp.roles[1].bullets[0].text == "new B"
        # Third role untouched from IR
        assert exp.roles[2].header.text == "Junior | Corp C"
        assert exp.roles[2].bullets[0].text == "bullet C"

    def test_roles_not_merged(self):
        """Each IR role remains a separate RoleEntry — no merging ever occurs."""
        orig_roles = [
            _role("Eng | Corp A", "2022–2024", ["a1", "a2"], "r1"),
            _role("Dev | Corp B", "2020–2022", ["b1", "b2"], "r2"),
        ]
        doc = _doc([_experience_section("sec_1", orig_roles)])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [
            _llm_role("Eng | Corp A", "2022–2024", ["new a1", "new a2"]),
            _llm_role("Dev | Corp B", "2020–2022", ["new b1", "new b2"]),
        ])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert len(result.sections[0].roles) == 2
        assert result.sections[0].roles[0].header.text == "Eng | Corp A"
        assert result.sections[0].roles[1].header.text == "Dev | Corp B"

    def test_preserve_policy_skips_section(self):
        """experience section with rewrite_policy='preserve' is returned verbatim."""
        orig_role = _role("Eng | Corp", "2020–2022", ["original bullet"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1", rewrite_policy="preserve")])
        llm_sections = [_llm_exp("Experience", [_llm_role("Eng | Corp", "2020–2022", ["new bullet"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].roles[0].bullets[0].text == "original bullet"

    def test_preserve_heading_keeps_heading_text(self):
        """preserve_heading=True: section heading text unchanged even if LLM differs."""
        orig_role = _role("Eng | Corp", "2020–2022", ["bullet"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1", preserve_heading=True)])
        llm_sections = [_llm_exp("WORK EXPERIENCE", [_llm_role("Eng | Corp", "2020–2022", ["new"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].heading.text == "Experience"

    def test_bullet_count_mismatch_truncated(self):
        """When LLM writes fewer bullets than IR, only those bullets are updated."""
        orig_role = _role("Eng | Corp", "2020–2022", ["b1", "b2", "b3"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_1")])
        llm_sections = [_llm_exp("Experience", [_llm_role("Eng | Corp", "2020–2022", ["new b1"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        # _update_role_bullets_only: only matching bullets updated, no extras
        assert result.sections[0].roles[0].bullets[0].text == "new b1"
        assert len(result.sections[0].roles[0].bullets) == 1


class TestBodySectionClassified:
    """Non-experience section behavior under classification."""

    def test_body_text_updated(self):
        """Body paragraph text is replaced with LLM output."""
        sec = _body_section("sec_1", "Summary", "summary", ["Old summary text."])
        doc = _doc([sec])
        cls = _cls_output([_cls_body("sec_1", "summary")])
        llm_sections = [_llm_body("Summary", "summary", ["New summary text."])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].body_paras[0].text == "New summary text."

    def test_preserve_heading_body_section(self):
        """preserve_heading=True: heading text unchanged even if LLM writes different text."""
        sec = _body_section("sec_1", "Professional Summary", "summary", ["Text"])
        doc = _doc([sec])
        cls = _cls_output([_cls_body("sec_1", "summary", preserve_heading=True)])
        llm_sections = [_llm_body("SUMMARY", "summary", ["New text"])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].heading.text == "Professional Summary"

    def test_preserve_body_structure_no_new_paras(self):
        """preserve_body_structure=True: LLM extra lines are not added to the output."""
        sec = _body_section("sec_1", "Summary", "summary", ["Line 1", "Line 2"])
        doc = _doc([sec])
        cls = _cls_output([_cls_body("sec_1", "summary", preserve_body_structure=True)])
        llm_sections = [_llm_body("Summary", "summary", ["New 1", "New 2", "Extra 3", "Extra 4"])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert len(result.sections[0].body_paras) == 2
        assert result.sections[0].body_paras[0].text == "New 1"
        assert result.sections[0].body_paras[1].text == "New 2"

    def test_preserve_body_structure_fewer_llm_lines_keeps_originals(self):
        """When LLM has fewer lines than IR and preserve_body_structure, unmatched paras kept."""
        sec = _body_section("sec_1", "Skills", "skills", ["Python", "Java", "Go"])
        doc = _doc([sec])
        cls = _cls_output([_cls_body("sec_1", "skills", preserve_body_structure=True)])
        llm_sections = [_llm_body("Skills", "skills", ["Python 3"])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        # First para updated, rest kept verbatim
        assert result.sections[0].body_paras[0].text == "Python 3"
        assert result.sections[0].body_paras[1].text == "Java"
        assert result.sections[0].body_paras[2].text == "Go"

    def test_preserve_section_unchanged(self):
        """Body section with rewrite_policy='preserve' returned verbatim."""
        sec = _body_section("sec_1", "Skills", "skills", ["Python", "Java"])
        doc = _doc([sec])
        cls = _cls_output([_cls_body("sec_1", "skills", rewrite_policy="preserve")])
        llm_sections = [_llm_body("Skills", "skills", ["Rust", "Go"])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].body_paras[0].text == "Python"
        assert result.sections[0].body_paras[1].text == "Java"


class TestFallbackBehavior:
    """Backward-compatibility: no classification → identical behavior to before."""

    def test_no_classification_experience_updates_header(self):
        """Without classification, experience headers are updated as before."""
        orig_role = _role("Old Header | Corp", "2020–2022", ["bullet"], "r1")
        doc = _doc([_experience_section("sec_1", [orig_role])])
        llm_sections = [_llm_exp("Experience", [_llm_role("New Header | Corp", "2020–2022", ["new"])])]

        result = apply_tailored(doc, llm_sections, classification=None)

        # Without classification, header IS updated (existing behavior)
        assert result.sections[0].roles[0].header.text == "New Header | Corp"

    def test_no_classification_allows_extra_roles(self):
        """Without classification, extra LLM roles are appended (existing behavior)."""
        orig_roles = [_role("Eng | A", "2022–2024", ["b"], "r1")]
        doc = _doc([_experience_section("sec_1", orig_roles)])
        llm_sections = [_llm_exp("Experience", [
            _llm_role("Eng | A", "2022–2024", ["new"]),
            _llm_role("New Role | B", "2020–2022", ["extra"]),
        ])]

        result = apply_tailored(doc, llm_sections, classification=None)

        # Existing behavior: extra LLM role cloned from last original
        assert len(result.sections[0].roles) == 2

    def test_missing_section_id_falls_back(self):
        """Section not found in classification index falls back to existing behavior."""
        orig_role = _role("Eng | Corp", "2020–2022", ["original"], "r1")
        # section_id="sec_1" but classification only has "sec_99" → no match
        doc = _doc([_experience_section("sec_1", [orig_role])])
        cls = _cls_output([_cls_exp("sec_99")])  # wrong section_id
        llm_sections = [_llm_exp("Experience", [_llm_role("Eng | Corp", "2020–2022", ["new"])])]

        result = apply_tailored(doc, llm_sections, classification=cls)

        # Falls back to existing behavior: header updated, extra roles allowed
        assert result.sections[0].roles[0].header.text == "Eng | Corp"
        assert result.sections[0].roles[0].bullets[0].text == "new"

    def test_classification_none_body_section_unchanged_structure(self):
        """Without classification, body sections can grow (extra LLM lines appended)."""
        sec = _body_section("sec_1", "Summary", "summary", ["Old line"])
        doc = _doc([sec])
        llm_sections = [_llm_body("Summary", "summary", ["New line 1", "New line 2"])]

        result = apply_tailored(doc, llm_sections, classification=None)

        # Existing behavior: extra LLM line cloned and appended
        assert len(result.sections[0].body_paras) == 2


class TestMultipleSections:
    """Classification applied across multiple sections with mixed policies."""

    def test_preserve_one_rewrite_other(self):
        """One section preserved, another rewritten."""
        exp_roles = [_role("Eng | Corp", "2022–2024", ["bullet"], "r1")]
        summary = _body_section("sec_1", "Summary", "summary", ["Old summary"])
        experience = _experience_section("sec_2", exp_roles)
        doc = _doc([summary, experience])

        cls = _cls_output([
            _cls_body("sec_1", "summary", rewrite_policy="preserve"),
            _cls_exp("sec_2"),
        ])
        llm_sections = [
            _llm_body("Summary", "summary", ["New summary"]),
            _llm_exp("Experience", [_llm_role("Eng | Corp", "2022–2024", ["new bullet"])]),
        ]

        result = apply_tailored(doc, llm_sections, classification=cls)

        assert result.sections[0].body_paras[0].text == "Old summary"
        assert result.sections[1].roles[0].bullets[0].text == "new bullet"
        assert result.sections[1].roles[0].header.text == "Eng | Corp"
