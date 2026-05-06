"""Tests for layout-bound updater mode and LLM role-continuation repair.

Covers:
A. Summary in-place update — existing para_ids preserved.
B. Skills in-place update — extra lines packed into existing slots.
C. Experience in-place update — extra bullets dropped, para_ids kept.
D. LLM role-continuation repair — "Web Designer" section absorbed as role.
E. Layout binding validation — validate_layout_binding reports metrics.
F. Backward compatibility — flag=false keeps existing behavior.
G. Extras skipped — no _make_extra_section unbound sections in layout-bound mode.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SIMPLE_TEMPLATE = str(_DOCX_DIR / "1-Leonid_Verman_Resume_Template.docx")
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_section(
    title: str,
    semantic_type: str,
    body_texts: list[str],
    section_id: str = "sec_1",
) -> "ResumeSection":
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
    heading = ParaModel(text=title, style=ParaStyle(), semantic="section_heading")
    heading.para_id = f"para_h_{section_id}"
    body = []
    for i, t in enumerate(body_texts):
        pm = ParaModel(text=t, style=ParaStyle(), semantic="paragraph")
        pm.para_id = f"para_b_{section_id}_{i}"
        body.append(pm)
    sec = ResumeSection(
        title=title,
        heading=heading,
        semantic_type=semantic_type,
        body_paras=body,
    )
    sec.section_id = section_id
    return sec


def _make_minimal_role(
    header_text: str,
    bullet_texts: list[str],
    role_id_stable: str = "role_1",
) -> "RoleEntry":
    from tailor.compiler.models import ParaModel, ParaStyle, RoleEntry
    header = ParaModel(text=header_text, style=ParaStyle(), semantic="role_header")
    header.para_id = f"para_rh_{role_id_stable}"
    bullets = []
    for i, t in enumerate(bullet_texts):
        pm = ParaModel(text=t, style=ParaStyle(), semantic="bullet")
        pm.para_id = f"para_rb_{role_id_stable}_{i}"
        bullets.append(pm)
    role = RoleEntry(header=header, bullets=bullets, role_id=header_text)
    role.role_id_stable = role_id_stable
    return role


def _make_llm_section(heading: str, body_lines: list[str], semantic_type: str = "summary"):
    from tailor.compiler.text_parser import LlmSection
    return LlmSection(heading=heading, semantic_type=semantic_type, body_lines=body_lines)


def _make_llm_experience(roles: list["LlmRole"]):
    from tailor.compiler.text_parser import LlmSection
    return LlmSection(heading="Experience", semantic_type="experience", roles=roles)


def _make_llm_role(header: str, bullets: list[str]):
    from tailor.compiler.text_parser import LlmRole
    return LlmRole(header=header, bullets=bullets)


# ---------------------------------------------------------------------------
# A. Summary in-place update
# ---------------------------------------------------------------------------

class TestSummaryInPlaceUpdate:
    def test_existing_summary_para_ids_preserved(self, monkeypatch):
        """In layout-bound mode, updating summary body lines must keep para_ids."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutProfile,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            ResumeSection,
            assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        summary_sec = _make_minimal_section(
            "Professional Summary", "summary", ["Original summary text."], "sec_1"
        )
        orig = ResumeDocument(
            header_paras=[], sections=[summary_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id, xml_proto_xml=None)
            for pm in orig.all_paras if pm.para_id
        ]

        llm_secs = [_make_llm_section("Professional Summary", ["Updated summary content."])]
        updated = apply_tailored(orig, llm_secs)

        # Section must have same section_id
        assert updated.sections[0].section_id == "sec_1"
        # Heading para_id preserved
        assert updated.sections[0].heading.para_id == summary_sec.heading.para_id
        # Body para_id preserved
        orig_pid = summary_sec.body_paras[0].para_id
        updated_pid = updated.sections[0].body_paras[0].para_id
        assert updated_pid == orig_pid, f"para_id lost: was {orig_pid!r}, got {updated_pid!r}"
        # Text updated
        assert "Updated summary" in updated.sections[0].body_paras[0].text

    def test_section_id_always_preserved_regardless_of_flag(self, monkeypatch):
        """section_id is always preserved from the original section (invariant fix)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)

        from tailor.compiler.models import (
            LayoutProfile, ParaModel, ParaStyle, ResumeDocument, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        summary_sec = _make_minimal_section("Summary", "summary", ["text"], "sec_99")
        orig = ResumeDocument(
            header_paras=[], sections=[summary_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig_section_id = orig.sections[0].section_id  # assigned by assign_stable_ids

        llm_secs = [_make_llm_section("Summary", ["New text."])]
        updated = apply_tailored(orig, llm_secs)
        # section_id is always preserved now (regardless of flag)
        assert updated.sections[0].section_id == orig_section_id


# ---------------------------------------------------------------------------
# B. Skills in-place update — packing
# ---------------------------------------------------------------------------

class TestSkillsInPlaceUpdate:
    def test_extra_skill_lines_packed_into_last_slot(self, monkeypatch):
        """LLM producing 5 skill lines with 2 original slots → all lines preserved via packing."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile,
            ResumeDocument, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        skills_sec = _make_minimal_section(
            "Skills", "skills", ["Slot 1", "Slot 2"], "sec_skills"
        )
        orig = ResumeDocument(
            header_paras=[], sections=[skills_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        llm_secs = [_make_llm_section(
            "Skills",
            ["Python", "Go", "Kafka", "Kubernetes", "Docker"],
            semantic_type="skills",
        )]
        updated = apply_tailored(orig, llm_secs)

        skills = updated.sections[0]
        content = [p for p in skills.body_paras if p.text.strip()]
        # 2 content paras (one per original slot)
        assert len(content) == 2, f"expected 2 content paras, got {len(content)}"
        # All content paras must have valid para_ids (no unbound clones)
        for p in content:
            assert p.para_id != "", f"para_id empty on updated skill para: {p.text!r}"
        # First LLM line in slot 1
        assert "Python" in content[0].text
        # Slot 2 contains second line + all packed extras — no content dropped
        combined = content[1].text
        assert "Go" in combined
        assert "Kafka" in combined
        assert "Kubernetes" in combined
        assert "Docker" in combined

    def test_skills_no_unbound_paras_created(self, monkeypatch):
        """No clone_as unbound paragraphs when packing skills in layout-bound mode."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        skills_sec = _make_minimal_section("Skills", "skills", ["A", "B"], "sec_1")
        orig = ResumeDocument(
            header_paras=[], sections=[skills_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        llm_secs = [_make_llm_section("Skills", ["X", "Y", "Z", "W"], "skills")]
        updated = apply_tailored(orig, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0, (
            f"unbound non-empty paras: {metrics['unbound_non_empty_paras']}"
        )


# ---------------------------------------------------------------------------
# C. Experience in-place update — extra bullets packed, not dropped
# ---------------------------------------------------------------------------

class TestExperienceInPlaceUpdate:
    def test_extra_bullet_packed_into_last_slot(self, monkeypatch):
        """Extra LLM bullet is packed into the last slot (not dropped) in layout-bound mode."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument,
            ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        # Long original bullets (conservative merge not possible)
        role = _make_minimal_role(
            "Engineer | Acme",
            ["Built distributed backend services achieving 99.9% uptime.", "Led team of 8."],
        )
        exp_sec = ResumeSection(
            title="Experience", heading=_make_minimal_section("Experience", "experience", []).heading,
            semantic_type="experience", roles=[role],
        )
        exp_sec.section_id = "sec_exp"
        orig = ResumeDocument(
            header_paras=[], sections=[exp_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        llm_exp = _make_llm_experience([
            _make_llm_role("Engineer | Acme", [
                "Updated distributed backend services.",
                "Led team of 10 engineers.",
                "Extra long bullet that pushes length beyond 1.25x original text.",
            ]),
        ])
        updated = apply_tailored(orig, [llm_exp])

        updated_role = updated.sections[0].roles[0]
        # 2 bullets (one per original slot)
        assert len(updated_role.bullets) == 2, (
            f"expected 2 bullets, got {len(updated_role.bullets)}"
        )
        # All bullets have non-empty para_id (no unbound clones)
        for b in updated_role.bullets:
            assert b.para_id != "", f"bullet has empty para_id: {b.text!r}"
        # First bullet: 1:1 replacement
        assert "Updated distributed backend services" in updated_role.bullets[0].text
        # Second bullet: contains 2nd LLM bullet + packed extra — nothing dropped
        combined = updated_role.bullets[1].text
        assert "Led team of 10" in combined
        assert "Extra long bullet" in combined

    def test_role_id_stable_preserved(self, monkeypatch):
        """role_id_stable is preserved from original when layout_bound=True."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        role = _make_minimal_role("Dev | Corp", ["Bullet"])
        exp_sec = ResumeSection(
            title="Experience",
            heading=_make_minimal_section("Experience", "experience", []).heading,
            semantic_type="experience", roles=[role],
        )
        exp_sec.section_id = "sec_e"
        orig = ResumeDocument(
            header_paras=[], sections=[exp_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)  # assigns sequential IDs, e.g. "role_1"
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]
        # Capture the ID assigned by assign_stable_ids
        orig_role_id_stable = orig.sections[0].roles[0].role_id_stable
        assert orig_role_id_stable  # must have been assigned

        llm_exp = _make_llm_experience([_make_llm_role("Dev | Corp", ["New bullet"])])
        updated = apply_tailored(orig, [llm_exp])

        assert updated.sections[0].roles[0].role_id_stable == orig_role_id_stable


# ---------------------------------------------------------------------------
# D. LLM role-continuation repair
# ---------------------------------------------------------------------------

class TestRoleContiniuationRepair:
    def test_role_title_section_absorbed_into_experience(self):
        """'Web Designer' section after Experience is absorbed as an additional role."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _repair_role_continuation_sections

        exp_role = LlmRole(header="Web Developer | Liceria & Co.", bullets=["Built things"])
        exp_sec = LlmSection(heading="Experience", semantic_type="experience", roles=[exp_role])

        # "Web Designer" section with role-like content
        sub_role = LlmRole(header="Borcelle & Co. | 2016 – 2018", bullets=["Designed things"])
        web_sec = LlmSection(
            heading="Web Designer",
            semantic_type="other",
            roles=[sub_role],
        )

        result = _repair_role_continuation_sections([exp_sec, web_sec])

        assert len(result) == 1, "Web Designer section should be absorbed"
        assert result[0].semantic_type == "experience"
        assert len(result[0].roles) == 2
        assert result[0].roles[1].header == "Web Designer"

    def test_non_job_title_section_not_absorbed(self):
        """A section with no job-title words is NOT absorbed as a role."""
        from tailor.compiler.text_parser import LlmSection
        from tailor.compiler.updater import _repair_role_continuation_sections

        exp_sec = LlmSection(heading="Experience", semantic_type="experience", roles=[])
        awards_sec = LlmSection(
            heading="Awards & Honors",
            semantic_type="other",
            body_lines=["Best Employee 2023"],
        )

        result = _repair_role_continuation_sections([exp_sec, awards_sec])
        assert len(result) == 2, "Awards section should NOT be absorbed"

    def test_experience_only_section_no_change(self):
        """If there are no following role-continuation sections, nothing changes."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _repair_role_continuation_sections

        exp_sec = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(header="Dev | Corp", bullets=["Did work"])],
        )
        edu_sec = LlmSection(
            heading="Education", semantic_type="education",
            body_lines=["BS Computer Science, MIT, 2020"],
        )

        result = _repair_role_continuation_sections([exp_sec, edu_sec])
        assert len(result) == 2

    def test_multiple_continuations_all_absorbed(self):
        """Multiple consecutive role-title sections are all absorbed."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import _repair_role_continuation_sections

        exp_sec = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(header="Dev | A", bullets=[])],
        )
        designer_sec = LlmSection(
            heading="Web Designer", semantic_type="other",
            roles=[LlmRole(header="B | 2020", bullets=["Designed"])],
        )
        analyst_sec = LlmSection(
            heading="Data Analyst", semantic_type="other",
            roles=[LlmRole(header="C | 2018", bullets=["Analyzed"])],
        )
        edu_sec = LlmSection(heading="Education", semantic_type="education", body_lines=["BS"])

        result = _repair_role_continuation_sections([exp_sec, designer_sec, analyst_sec, edu_sec])
        assert len(result) == 2  # Experience + Education
        assert len(result[0].roles) == 3  # original + 2 absorbed

    def test_repair_runs_automatically_when_layout_blocks_present(self, monkeypatch):
        """Repair runs whenever layout_blocks exist, regardless of USE_LAYOUT_BOUND_UPDATER."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)  # flag off

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        assert doc.layout_blocks is not None

        exp_section = next(
            (s for s in doc.sections if s.semantic_type == "experience"), None
        )
        if exp_section is None or not exp_section.roles:
            pytest.skip("no experience section")

        role = exp_section.roles[0]
        exp_llm = LlmSection(
            heading=exp_section.title,
            semantic_type="experience",
            roles=[LlmRole(header=role.header.text, bullets=["Updated bullet"])],
        )
        # Add a fake "Web Designer" continuation
        web_designer = LlmSection(
            heading="Web Designer", semantic_type="other",
            roles=[LlmRole(header="Co | 2020", bullets=["Designed"])],
        )

        updated = apply_tailored(doc, [exp_llm, web_designer])

        # "Web Designer" must NOT appear as a top-level section
        section_titles = [s.title for s in updated.sections]
        assert "Web Designer" not in section_titles, (
            "Web Designer should have been absorbed as a role, not a section"
        )


# ---------------------------------------------------------------------------
# E. Layout binding validation
# ---------------------------------------------------------------------------

class TestValidateLayoutBinding:
    def test_all_bound_after_layout_bound_update(self, monkeypatch):
        """With layout_bound=True and matching content, all paras should be bound."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        sec = _make_minimal_section("Skills", "skills", ["A", "B"], "sec_1")
        orig = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        # LLM has exactly the same number of lines as template
        llm_secs = [_make_llm_section("Skills", ["X", "Y"], "skills")]
        updated = apply_tailored(orig, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0

    def test_validate_detects_unbound_paras_no_layout_blocks(self, monkeypatch):
        """validate_layout_binding detects clone_as unbound paras when NO layout_blocks."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)  # allow unbound

        from tailor.compiler.models import LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        sec = _make_minimal_section("Skills", "skills", ["A"], "sec_1")
        orig = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        # No layout_blocks → finalize_layout_bound_ir won't run → clone_as unbound paras survive

        # LLM has MORE lines → flag=False + no layout_blocks → clone_as → unbound
        llm_secs = [_make_llm_section("Skills", ["X", "Y", "Z"], "skills")]
        updated = apply_tailored(orig, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] > 0, (
            "expected unbound paras when flag is off, no layout_blocks, and LLM has extras"
        )


# ---------------------------------------------------------------------------
# F. Backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    def test_extra_bullets_rendered_when_flag_off(self, tmp_path, monkeypatch):
        """Without layout_bound, extra bullets from LLM appear in rendered output."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)
        # Also disable the layout_blocks renderer so extra clone_as bullets
        # (para_id="") are written via xml_proto rather than being silently
        # skipped by _render_from_layout_blocks.
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", False)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        n_orig = len(role.bullets)
        extra_bullet_text = "UNIQUE_EXTRA_BULLET_NOT_IN_TEMPLATE_12345"
        llm_exp = LlmSection(
            heading=exp.title, semantic_type="experience",
            roles=[LlmRole(
                header=role.header.text,
                bullets=[b.text for b in role.bullets] + [extra_bullet_text],
            )],
        )
        updated = apply_tailored(doc, [llm_exp])
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        from tailor.docx.template_fill import read_docx
        text = read_docx(out)
        # Extra bullet must appear (legacy behavior preserved)
        assert extra_bullet_text in text


# ---------------------------------------------------------------------------
# G. Extras skipped in layout-bound mode
# ---------------------------------------------------------------------------

class TestExtrasSkippedInLayoutBoundMode:
    def test_unmatched_summary_not_inserted(self, monkeypatch):
        """In layout-bound mode, a summary section absent from the template is NOT inserted."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        skills_sec = _make_minimal_section("Skills", "skills", ["Python"], "sec_1")
        orig = ResumeDocument(
            header_paras=[], sections=[skills_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        # LLM adds a "Professional Summary" that doesn't exist in template
        llm_secs = [
            _make_llm_section("Professional Summary", ["I am a developer."], "summary"),
            _make_llm_section("Skills", ["Python", "Go"], "skills"),
        ]
        updated = apply_tailored(orig, llm_secs)

        section_types = [s.semantic_type for s in updated.sections]
        assert "summary" not in section_types, (
            "Unmatched summary should NOT be inserted in layout-bound mode"
        )

    def test_unmatched_summary_inserted_when_flag_off(self, monkeypatch):
        """Without layout_bound, unmatched summary section IS inserted (existing behavior)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)

        from tailor.compiler.models import LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        skills_sec = _make_minimal_section("Skills", "skills", ["Python"], "sec_1")
        orig = ResumeDocument(
            header_paras=[], sections=[skills_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        llm_secs = [
            _make_llm_section("Professional Summary", ["I am a developer."], "summary"),
            _make_llm_section("Skills", ["Python"], "skills"),
        ]
        updated = apply_tailored(orig, llm_secs)

        section_types = [s.semantic_type for s in updated.sections]
        assert "summary" in section_types, "Summary should be inserted when flag is off"

    def test_no_layout_blocks_behaves_as_before(self, monkeypatch):
        """When layout_blocks is None, everything works as before regardless of flag."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        from tailor.compiler.text_parser import LlmSection

        doc = parse_docx(_SIMPLE_TEMPLATE)
        doc.layout_blocks = None  # forcibly remove

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None:
            pytest.skip("no experience")

        llm_secs = [LlmSection(heading=exp.title, semantic_type="experience", roles=[])]
        updated = apply_tailored(doc, llm_secs)
        # Must not crash; should produce a valid document
        assert updated is not None
        assert len(updated.sections) > 0


# ---------------------------------------------------------------------------
# H. No content truncation — full LLM text preserved in layout-bound mode
# ---------------------------------------------------------------------------

class TestNoContentTruncation:
    """Verify that meaningful LLM content is never silently truncated."""

    def _make_layout_doc(self, body_texts, semantic):
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
        )
        import tailor.config as cfg
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        sec = _make_minimal_section("Section", semantic, body_texts, "sec_1")
        orig = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id
        ]
        return orig

    def test_long_summary_not_truncated(self, monkeypatch):
        """A summary body paragraph longer than the original budget is preserved in full."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.updater import apply_tailored

        long_text = (
            "Senior engineer with 15 years of experience across distributed systems, "
            "cloud infrastructure, and platform engineering. Led multiple cross-functional "
            "initiatives delivering measurable impact. Recognized for technical excellence "
            "and strategic execution across high-stakes projects.".strip()
        )
        orig = self._make_layout_doc(["Short original."], "summary")
        llm_secs = [_make_llm_section("Section", [long_text])]
        updated = apply_tailored(orig, llm_secs)

        body = [p for p in updated.sections[0].body_paras if p.text.strip()]
        combined = " ".join(p.text for p in body)
        # Full text must be present — no "..." truncation
        assert "..." not in combined, f"Truncation introduced: {combined[:200]!r}"
        assert long_text[:50] in combined, f"Text missing from summary: {combined[:200]!r}"

    def test_skills_overflow_packed_not_dropped(self, monkeypatch):
        """Extra skill lines beyond slot count are packed into last slot, not dropped."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.updater import apply_tailored

        orig = self._make_layout_doc(["Slot A"], "skills")
        llm_secs = [_make_llm_section("Section", ["Python", "Go", "Rust", "C++"], "skills")]
        updated = apply_tailored(orig, llm_secs)

        body = [p for p in updated.sections[0].body_paras if p.text.strip()]
        combined = " ".join(p.text for p in body)
        assert "Python" in combined
        assert "Go" in combined
        assert "Rust" in combined
        assert "C++" in combined

    def test_experience_bullets_not_budget_truncated(self, monkeypatch):
        """Experience bullets exceeding original text length are NOT truncated with "..."."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        # Short original bullet (will be far exceeded by LLM replacement)
        role = _make_minimal_role("Engineer | Acme", ["Short.", "Also short."])
        exp_sec = ResumeSection(
            title="Experience",
            heading=_make_minimal_section("Experience", "experience", []).heading,
            semantic_type="experience", roles=[role],
        )
        exp_sec.section_id = "sec_exp"
        orig = ResumeDocument(header_paras=[], sections=[exp_sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        orig.layout_blocks = [LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id]

        long_bullet = (
            "Architected and delivered a distributed microservices platform handling 50k RPS, "
            "reducing P99 latency by 40% through systematic profiling and optimization of "
            "hot paths in the data pipeline, working closely with infrastructure and SRE teams."
        )
        llm_exp = _make_llm_experience([_make_llm_role("Engineer | Acme", [long_bullet, "Led team."])])
        updated = apply_tailored(orig, [llm_exp])

        bullet_text = updated.sections[0].roles[0].bullets[0].text
        assert "..." not in bullet_text, f"Truncation in bullet: {bullet_text!r}"
        assert "Architected" in bullet_text
        assert "40%" in bullet_text

    def test_experience_extra_bullets_packed(self, monkeypatch):
        """Three LLM bullets with only 2 slots → third bullet packed into last slot."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        role = _make_minimal_role("Dev | Corp", ["Bullet 1.", "Bullet 2."])
        exp_sec = ResumeSection(
            title="Experience",
            heading=_make_minimal_section("Experience", "experience", []).heading,
            semantic_type="experience", roles=[role],
        )
        exp_sec.section_id = "sec_exp"
        orig = ResumeDocument(header_paras=[], sections=[exp_sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        orig.layout_blocks = [LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id]

        llm_exp = _make_llm_experience([_make_llm_role("Dev | Corp", ["A.", "B.", "C extra."])])
        updated = apply_tailored(orig, [llm_exp])

        role_out = updated.sections[0].roles[0]
        # Still 2 bullet slots
        assert len(role_out.bullets) == 2
        # All para_ids intact (no unbound)
        assert all(b.para_id for b in role_out.bullets)
        # All 3 LLM bullet texts are present somewhere
        all_text = " ".join(b.text for b in role_out.bullets)
        assert "A." in all_text
        assert "B." in all_text
        assert "C extra." in all_text
        # No unbound paras
        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0
