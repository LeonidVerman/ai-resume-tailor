"""Tests for the five targeted hard-fail grader fixes.

Fix 1: _strip_col_break_para must preserve para_id (samples 3, 37).
Fix 4: _inject_fragmented_experience for decorative templates (sample 29).
Fix 5: Lorem-ipsum placeholder summary replaced by LLM summary (sample 29).

Fix 2 tests are in test_anchored_summary.py (matched summary not duplicated).
Fix 3 tests are in test_layout_grader.py (conditional EMPTY_PARA_ID hard-fail).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _make_para(text: str, para_id: str, semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _make_section(title: str, semantic_type: str, section_id: str,
                  body_texts: list[str] | None = None):
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
    heading = ParaModel(text=title, style=ParaStyle(), semantic="section_heading")
    heading.para_id = f"hd_{section_id}"
    body: list[ParaModel] = []
    for i, t in enumerate(body_texts or []):
        pm = _make_para(t, f"bp_{section_id}_{i}")
        body.append(pm)
    sec = ResumeSection(
        title=title, heading=heading,
        semantic_type=semantic_type, body_paras=body,
    )
    sec.section_id = section_id
    return sec


def _make_role_entry(label: str, idx: int):
    from tailor.compiler.models import ParaModel, ParaStyle, RoleEntry
    h = ParaModel(text=f"Role {label}", style=ParaStyle(), semantic="role_header")
    h.para_id = f"rh_{idx}"
    m = ParaModel(text="2020 – 2022", style=ParaStyle(), semantic="role_meta")
    m.para_id = f"rm_{idx}"
    b = ParaModel(text=f"Bullet {idx}.", style=ParaStyle(), semantic="bullet")
    b.para_id = f"rb_{idx}"
    return RoleEntry(header=h, meta_lines=[m], bullets=[b], role_id=f"role_{idx}")


def _make_layout_doc(
    header_paras: list,
    sections: list,
    layout_bound: bool = True,
):
    from tailor.compiler.models import (
        LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
    )
    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )
    doc = ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=[],
    )
    assign_stable_ids(doc)
    if layout_bound:
        all_paras = list(header_paras)
        for sec in sections:
            all_paras.append(sec.heading)
            for bp in sec.body_paras:
                all_paras.append(bp)
            for role in sec.roles:
                all_paras.append(role.header)
                all_paras.extend(role.meta_lines)
                all_paras.extend(role.bullets)
        doc.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in all_paras if pm.para_id
        ]
    return doc


# ---------------------------------------------------------------------------
# Fix 1: _strip_col_break_para preserves para_id
# ---------------------------------------------------------------------------

class TestStripColBreakParaPreserveParaId:
    """Fix 1: clone_as does not inherit para_id; _strip_col_break_para must fix this."""

    def _make_para_with_col_break(self, text: str, para_id: str):
        """Return a ParaModel whose xml_proto contains a w:br type='column'."""
        from lxml import etree
        from tailor.compiler.models import ParaModel, ParaStyle
        _W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        # Build: <w:p><w:r><w:br w:type="column"/></w:r></w:p>
        ns = f"{{{_W}}}"
        p_el = etree.Element(f"{ns}p")
        r_el = etree.SubElement(p_el, f"{ns}r")
        br_el = etree.SubElement(r_el, f"{ns}br")
        br_el.set(f"{ns}type", "column")
        style = ParaStyle(xml_proto=p_el)
        pm = ParaModel(text=text, style=style, semantic="section_heading")
        pm.para_id = para_id
        return pm

    def test_para_id_preserved_when_col_break_stripped(self):
        """para_id must be copied to the cloned ParaModel produced by _strip_col_break_para."""
        from tailor.compiler.updater import _strip_col_break_para

        pm = self._make_para_with_col_break("Senior Software Engineer", "para_test_42")
        result = _strip_col_break_para(pm)

        assert result.para_id == "para_test_42", (
            f"para_id must be preserved; got {result.para_id!r}"
        )
        assert result.text == "Senior Software Engineer"

    def test_col_break_removed_from_xml(self):
        """The w:br type='column' must be absent in the returned clone."""
        from lxml import etree
        from tailor.compiler.updater import _strip_col_break_para

        _W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        pm = self._make_para_with_col_break("Engineer", "para_br_1")
        result = _strip_col_break_para(pm)

        # No column breaks in the cloned proto
        col_breaks = result.style.xml_proto.findall(
            f".//{{{_W}}}br[@{{{_W}}}type='column']"
        )
        assert col_breaks == [], "Column break must be removed from xml_proto"

    def test_no_col_break_returns_same_object(self):
        """When no col break is present, the same ParaModel is returned (no-op)."""
        from tailor.compiler.models import ParaModel, ParaStyle
        from tailor.compiler.updater import _strip_col_break_para

        pm = ParaModel(text="No break", style=ParaStyle(), semantic="paragraph")
        pm.para_id = "pm_orig"
        result = _strip_col_break_para(pm)
        assert result is pm, "When no column break exists, original object must be returned"

    def test_no_xml_proto_returns_same_object(self):
        """ParaModel with xml_proto=None must be returned unchanged."""
        from tailor.compiler.models import ParaModel, ParaStyle
        from tailor.compiler.updater import _strip_col_break_para

        pm = ParaModel(text="Plain", style=ParaStyle(xml_proto=None), semantic="paragraph")
        pm.para_id = "pm_plain"
        result = _strip_col_break_para(pm)
        assert result is pm

    def test_para_id_preserved_in_apply_tailored_experience_heading(self, monkeypatch):
        """Integration: experience heading stripped of col-break must keep its para_id."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from lxml import etree
        from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        _W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
        ns = f"{{{_W}}}"
        p_el = etree.Element(f"{ns}p")
        r_el = etree.SubElement(p_el, f"{ns}r")
        br_el = etree.SubElement(r_el, f"{ns}br")
        br_el.set(f"{ns}type", "column")

        # Build experience section heading with a col break in its xml_proto.
        heading = ParaModel(
            text="WORK EXPERIENCE",
            style=ParaStyle(xml_proto=p_el),
            semantic="section_heading",
        )
        heading.para_id = "para_exp_hd"

        bullet = _make_para("Old bullet.", "para_b1", "bullet")

        from tailor.compiler.models import RoleEntry
        role = RoleEntry(
            header=_make_para("Engineer | Acme", "para_rh1", "role_header"),
            meta_lines=[_make_para("2020–2022", "para_rm1", "role_meta")],
            bullets=[bullet],
            role_id="role_1",
        )
        role.role_id_stable = "role_1"

        exp_sec = ResumeSection(
            title="WORK EXPERIENCE",
            heading=heading,
            semantic_type="experience",
            roles=[role],
        )
        exp_sec.section_id = "sec_exp"

        doc = _make_layout_doc(header_paras=[], sections=[exp_sec])
        # Capture the stable para_id assigned by assign_stable_ids (called inside _make_layout_doc)
        heading_pid_stable = heading.para_id

        llm = [
            LlmSection(
                heading="WORK EXPERIENCE",
                semantic_type="experience",
                roles=[LlmRole(
                    header="Engineer | Acme",
                    meta_lines=["2020–2022"],
                    bullets=["Updated bullet text."],
                )],
            )
        ]
        updated = apply_tailored(doc, llm)

        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert exp.heading.para_id == heading_pid_stable, (
            f"Experience heading para_id lost after col-break strip; got {exp.heading.para_id!r}"
        )


# ---------------------------------------------------------------------------
# Fix 4: Fragmented-experience injection helpers
# ---------------------------------------------------------------------------

class TestIsRoleLikeHeading:
    """Unit tests for the _is_role_like_heading predicate."""

    def test_standard_job_title_matches(self):
        from tailor.compiler.updater import _is_role_like_heading
        assert _is_role_like_heading("Senior Software Engineer")
        assert _is_role_like_heading("Software Developer")
        assert _is_role_like_heading("Product Manager")
        assert _is_role_like_heading("Data Scientist")
        assert _is_role_like_heading("Lead Architect")
        assert _is_role_like_heading("Intern")

    def test_section_headings_do_not_match(self):
        from tailor.compiler.updater import _is_role_like_heading
        assert not _is_role_like_heading("Work Experience")
        assert not _is_role_like_heading("Technical Skills")
        assert not _is_role_like_heading("Education")
        assert not _is_role_like_heading("Projects")

    def test_company_names_do_not_match(self):
        from tailor.compiler.updater import _is_role_like_heading
        # Company names rarely contain job-title words
        assert not _is_role_like_heading("Acme Corporation")
        assert not _is_role_like_heading("Google LLC")

    def test_empty_string_does_not_match(self):
        from tailor.compiler.updater import _is_role_like_heading
        assert not _is_role_like_heading("")

    def test_very_long_heading_does_not_match(self):
        from tailor.compiler.updater import _is_role_like_heading
        # 8+ words → False
        long_heading = "Senior Principal Staff Software Engineer and Technical Lead Architect"
        assert not _is_role_like_heading(long_heading)

    def test_strips_date_prefix(self):
        """Date-range prefix before job title must be ignored."""
        from tailor.compiler.updater import _is_role_like_heading
        assert _is_role_like_heading("May 2018 - Dec 2019 Senior Engineer")


class TestFindRoleLikeOtherSections:
    """Unit tests for _find_role_like_other_sections."""

    def test_returns_other_sections_with_role_titles(self):
        from tailor.compiler.updater import _find_role_like_other_sections

        secs = [
            _make_section("Senior Software Engineer", "other", "s1"),
            _make_section("Software Developer", "other", "s2"),
            _make_section("Work Experience", "experience", "s3"),
            _make_section("Technical Skills", "skills", "s4"),
        ]
        result = _find_role_like_other_sections(secs)
        titles = [s.title for s in result]
        assert "Senior Software Engineer" in titles
        assert "Software Developer" in titles
        assert "Work Experience" not in titles
        assert "Technical Skills" not in titles

    def test_returns_empty_when_no_role_like(self):
        from tailor.compiler.updater import _find_role_like_other_sections

        secs = [
            _make_section("Work Experience", "experience", "s1"),
            _make_section("Skills", "skills", "s2"),
        ]
        assert _find_role_like_other_sections(secs) == []

    def test_skips_non_other_sections_with_role_titles(self):
        from tailor.compiler.updater import _find_role_like_other_sections

        # A "summary" section with a job-title-looking heading must be ignored
        secs = [
            _make_section("Software Engineer", "summary", "s1"),
        ]
        assert _find_role_like_other_sections(secs) == []


class TestInjectFragmentedExperience:
    """Unit tests for _inject_fragmented_experience."""

    def _build_fragmented_doc(self):
        """Build a template with 3 role-like 'other' sections (no experience section)."""
        role_secs = [
            _make_section("Senior Software Engineer", "other", f"sec_role_{i}",
                          body_texts=[f"Old bullet {j}." for j in range(2)])
            for i in range(3)
        ]
        return role_secs

    def test_headings_updated_with_llm_role_headers(self):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _inject_fragmented_experience

        role_secs = self._build_fragmented_doc()
        original_sections = list(role_secs)
        llm_exp = LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[
                LlmRole("Backend Engineer | TechCorp", bullets=["Owned microservices."]),
                LlmRole("Junior Developer | StartupX", bullets=["Built features."]),
            ],
        )
        result = _inject_fragmented_experience(role_secs, original_sections, llm_exp)
        titles = [s.title for s in result]
        assert "Backend Engineer | TechCorp" in titles
        assert "Junior Developer | StartupX" in titles
        # Third section has no matching LLM role → title unchanged
        assert "Senior Software Engineer" in titles

    def test_section_ids_preserved(self):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _inject_fragmented_experience

        role_secs = self._build_fragmented_doc()
        orig_ids = [s.section_id for s in role_secs]
        original_sections = list(role_secs)
        llm_exp = LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[LlmRole("Engineer | Co", bullets=["Did work."])],
        )
        result = _inject_fragmented_experience(role_secs, original_sections, llm_exp)
        result_ids = [s.section_id for s in result]
        assert orig_ids == result_ids, "Section IDs must be preserved"

    def test_empty_role_like_sections_returns_unchanged(self):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _inject_fragmented_experience

        non_role_secs = [
            _make_section("Work Experience", "experience", "sec_exp"),
            _make_section("Skills", "skills", "sec_sk"),
        ]
        llm_exp = LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[LlmRole("Engineer", bullets=["Bullet."])],
        )
        result = _inject_fragmented_experience(non_role_secs, non_role_secs, llm_exp)
        assert result is non_role_secs, "No role-like sections → must return original list unchanged"

    def test_heading_para_id_preserved_after_injection(self):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _inject_fragmented_experience

        role_secs = self._build_fragmented_doc()
        orig_heading_pid = role_secs[0].heading.para_id
        original_sections = list(role_secs)
        llm_exp = LlmSection(
            heading="Experience",
            semantic_type="experience",
            roles=[LlmRole("Principal Engineer | FAANG", bullets=["Led platform."])],
        )
        result = _inject_fragmented_experience(role_secs, original_sections, llm_exp)
        # Heading's para_id must be preserved (it uses with_text, which keeps para_id)
        assert result[0].heading.para_id == orig_heading_pid


# ---------------------------------------------------------------------------
# Fix 4+5: Integration test using apply_tailored on fragmented + lorem template
# ---------------------------------------------------------------------------

class TestFragmentedExperienceAndLoremIntegration:
    """Integration tests for Fixes 4 and 5 together via apply_tailored."""

    def _build_decorative_doc(self):
        """Build a minimal decorative template:
        - header_paras: name + lorem ipsum summary placeholder
        - sections: 3 role-like 'other' sections (no umbrella 'Experience' section)
        """
        name_para = _make_para("JANE DOE", "para_name", "section_heading")
        lorem_para = _make_para(
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore.",
            "para_lorem",
            "paragraph",
        )

        role_secs = []
        for i in range(3):
            sec = _make_section(
                ["Senior Developer", "Backend Engineer", "Junior Programmer"][i],
                "other",
                f"sec_role_{i}",
                body_texts=[f"Old bullet A{i}.", f"Old bullet B{i}."],
            )
            role_secs.append(sec)

        return _make_layout_doc(
            header_paras=[name_para, lorem_para],
            sections=role_secs,
            layout_bound=True,
        )

    def _llm_sections(self):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        return [
            LlmSection(
                heading="Professional Summary",
                semantic_type="summary",
                body_lines=["Experienced full-stack developer with 8 years."],
            ),
            LlmSection(
                heading="Work Experience",
                semantic_type="experience",
                roles=[
                    LlmRole("Principal Engineer | BigCo",
                            bullets=["Architected microservices platform."]),
                    LlmRole("Senior Backend Engineer | StartupY",
                            bullets=["Built REST APIs."]),
                    LlmRole("Junior Developer | AgencyZ",
                            bullets=["Developed web applications."]),
                ],
            ),
        ]

    def test_fix4_experience_roles_injected_into_other_sections(self, monkeypatch):
        """Fix 4: LLM experience roles are injected into role-like 'other' sections."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = self._build_decorative_doc()
        updated = apply_tailored(doc, self._llm_sections())

        # The sections must now have LLM role titles
        section_titles = [s.title for s in updated.sections]
        assert "Principal Engineer | BigCo" in section_titles, (
            f"LLM role title not injected; titles: {section_titles}"
        )
        assert "Senior Backend Engineer | StartupY" in section_titles

    def test_fix4_section_ids_preserved_after_injection(self, monkeypatch):
        """Fix 4: Section IDs must be preserved after role injection."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = self._build_decorative_doc()
        orig_ids = {s.section_id for s in doc.sections}
        updated = apply_tailored(doc, self._llm_sections())
        result_ids = {s.section_id for s in updated.sections}
        assert orig_ids == result_ids, (
            f"Section IDs changed: orig={orig_ids!r} result={result_ids!r}"
        )

    def test_fix5_lorem_placeholder_replaced_with_summary(self, monkeypatch):
        """Fix 5: Lorem ipsum header_para is replaced with LLM summary text."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = self._build_decorative_doc()
        updated = apply_tailored(doc, self._llm_sections())

        # The header_para that contained lorem ipsum must now contain summary text
        header_texts = [p.text for p in updated.header_paras]
        combined = " ".join(header_texts)
        assert "lorem ipsum" not in combined.lower(), (
            "Lorem ipsum must be replaced in header_paras"
        )
        assert "Experienced full-stack developer" in combined, (
            f"LLM summary text not found in header_paras; got: {header_texts!r}"
        )

    def test_fix5_lorem_para_id_preserved_after_replacement(self, monkeypatch):
        """Fix 5: The replaced lorem para must retain its original para_id."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = self._build_decorative_doc()
        lorem_pid = next(
            p.para_id for p in doc.header_paras if "lorem" in p.text.lower()
        )
        updated = apply_tailored(doc, self._llm_sections())

        # The para_id of the lorem para must be preserved
        updated_header_pids = {p.para_id for p in updated.header_paras}
        assert lorem_pid in updated_header_pids, (
            f"para_id={lorem_pid!r} disappeared from header_paras after lorem replacement"
        )

    def test_no_lorem_means_no_lorem_injection(self, monkeypatch):
        """Fix 5: Templates without lorem ipsum are not affected."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        # Build a doc with a non-lorem summary placeholder
        name_para = _make_para("BOB SMITH", "para_name2", "section_heading")
        plain_para = _make_para("Generic placeholder text here.", "para_plain", "paragraph")

        role_sec = _make_section("Software Engineer", "other", "sec_r1",
                                 body_texts=["Old bullet."])
        doc = _make_layout_doc(
            header_paras=[name_para, plain_para],
            sections=[role_sec],
        )
        # Capture the stable para_id assigned by assign_stable_ids (called inside _make_layout_doc)
        plain_pid_stable = plain_para.para_id

        llm = [
            LlmSection("Professional Summary", "summary",
                       body_lines=["Strong engineer with 5 years experience."]),
            LlmSection("Work Experience", "experience",
                       roles=[LlmRole("Senior Engineer | Corp", bullets=["Led team."])]),
        ]
        updated = apply_tailored(doc, llm)

        # Non-lorem para must not be overwritten; look up by stable para_id
        plain_updated = next(
            (p for p in updated.header_paras if p.para_id == plain_pid_stable), None
        )
        assert plain_updated is not None, (
            f"para_id={plain_pid_stable!r} not found in header_paras; "
            f"got {[(p.para_id, p.text[:30]) for p in updated.header_paras]}"
        )
        assert "Generic placeholder text here." in plain_updated.text, (
            "Non-lorem paragraph must not be overwritten by summary injection"
        )
