"""Tests for skills overflow reflow and summary insertion position.

Covers:
I. Skill lines overflow to unbound paras — no raw concatenation or truncation.
J. Summary inserted after profile/title block, before first major section.
"""
from __future__ import annotations
import pytest


def _make_para(text: str, para_id: str, semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _make_minimal_section(title, semantic_type, body_texts, section_id="sec_1"):
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
    heading = ParaModel(text=title, style=ParaStyle(), semantic="section_heading")
    heading.para_id = f"para_h_{section_id}"
    body = []
    for i, t in enumerate(body_texts):
        pm = ParaModel(text=t, style=ParaStyle(), semantic="paragraph")
        pm.para_id = f"para_b_{section_id}_{i}"
        body.append(pm)
    sec = ResumeSection(title=title, heading=heading, semantic_type=semantic_type, body_paras=body)
    sec.section_id = section_id
    return sec


def _make_layout_doc_skills(n_slots, skill_slots):
    from tailor.compiler.models import (
        LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
    )
    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )
    sec = _make_minimal_section("Technical Skills", "skills", skill_slots[:n_slots], "sec_skills")
    orig = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
    assign_stable_ids(orig)
    orig.layout_blocks = [LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id]
    return orig


# ---------------------------------------------------------------------------
# I. Skills packing separator
# ---------------------------------------------------------------------------

class TestSkillsPackingSeparator:
    """Extra skill lines become unbound overflow paras — no concatenation or truncation."""

    _SKILL_LINES = [
        "Datastores: MySQL, SQL Server, Oracle",
        "DevOps / Automation: Docker, Jenkins, Ansible",
        "Frontend: React, Angular",
        "Other: Agile, Linux",
    ]

    def _apply(self, monkeypatch, skill_lines, n_slots=1):
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.updater import apply_tailored
        from tailor.compiler.text_parser import LlmSection

        orig = _make_layout_doc_skills(n_slots, [f"Slot {i}" for i in range(n_slots)])
        llm_secs = [LlmSection(heading="Technical Skills", semantic_type="skills", body_lines=skill_lines)]
        return apply_tailored(orig, llm_secs)

    def test_all_categories_present(self, monkeypatch):
        updated = self._apply(monkeypatch, self._SKILL_LINES, n_slots=1)
        all_text = " ".join(p.text for p in updated.sections[0].body_paras if p.text.strip())
        for cat in ["Datastores", "DevOps", "Frontend", "Other"]:
            assert cat in all_text, f"Category {cat!r} missing from overflow skills"

    def test_no_direct_concatenation(self, monkeypatch):
        """Category names must not run together without a separator (each in its own para)."""
        updated = self._apply(monkeypatch, self._SKILL_LINES, n_slots=1)
        all_text = " ".join(p.text for p in updated.sections[0].body_paras if p.text.strip())
        # These are the bad concatenations produced by raw-newline packing
        for bad in ["OracleDevOps", "CommunicationAnalysis", "AutomationFrontend", "AnsibleFrontend"]:
            assert bad not in all_text, f"Direct concatenation detected: {bad!r}"

    def test_no_semicolon_in_overflow_paras(self, monkeypatch):
        """Each overflow para contains a single skill line — no '; ' separator needed."""
        updated = self._apply(monkeypatch, self._SKILL_LINES, n_slots=1)
        paras = [p for p in updated.sections[0].body_paras if p.text.strip()]
        # Each para should contain at most one skill line (no packing separator)
        for p in paras:
            # The individual lines contain ':' (category separator) but not '; '
            # between different category lines
            lines_in_para = [l for l in p.text.split("; ") if l.strip()]
            assert len(lines_in_para) == 1, (
                f"Para should contain single skill line, got packing: {p.text!r}"
            )

    def test_no_ellipsis_truncation(self, monkeypatch):
        updated = self._apply(monkeypatch, self._SKILL_LINES, n_slots=1)
        all_text = " ".join(p.text for p in updated.sections[0].body_paras if p.text.strip())
        assert "..." not in all_text, "Skill lines must not be truncated with '...'"

    def test_all_slots_filled_then_overflow(self, monkeypatch):
        """When 2 slots and 4 lines: slots 1-2 get lines 1-2, lines 3-4 become overflow."""
        updated = self._apply(monkeypatch, self._SKILL_LINES, n_slots=2)
        content = [p for p in updated.sections[0].body_paras if p.text.strip()]
        # 4 total: 2 bound + 2 unbound overflow
        assert len(content) == 4
        bound = [p for p in content if p.para_id]
        unbound = [p for p in content if not p.para_id]
        assert len(bound) == 2
        assert len(unbound) == 2
        assert "Datastores" in bound[0].text
        assert "DevOps" in bound[1].text
        # Overflow paras have the remaining lines
        overflow_text = " ".join(p.text for p in unbound)
        for cat in ["Frontend", "Other"]:
            assert cat in overflow_text, f"{cat!r} missing from overflow paras"


# ---------------------------------------------------------------------------
# J. Summary insertion position
# ---------------------------------------------------------------------------

class TestSummaryInsertionPosition:
    """Summary inserted after profile block, before first major content section."""

    def _build_profile_exp_doc(self, monkeypatch):
        """Template: other (profile/title) + experience. No summary. One empty header slot."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument,
            ResumeSection, RoleEntry, assign_stable_ids,
        )

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )

        profile_sec = ResumeSection(
            title="TITLE BLOCK",
            heading=_make_para("TITLE BLOCK", "para_h0", "section_heading"),
            semantic_type="other",
            body_paras=[_make_para("Senior Engineer tagline", "para_tag")],
        )
        profile_sec.section_id = "sec_profile"

        exp_role = RoleEntry(
            header=_make_para("Dev | Corp", "para_rh", "role_header"),
            bullets=[_make_para("Built systems.", "para_b0", "bullet")],
            role_id="Dev | Corp",
        )
        exp_role.role_id_stable = "role_1"
        exp_sec = ResumeSection(
            title="WORK EXPERIENCE",
            heading=_make_para("WORK EXPERIENCE", "para_h1", "section_heading"),
            semantic_type="experience", roles=[exp_role],
        )
        exp_sec.section_id = "sec_exp"

        orig = ResumeDocument(
            header_paras=[
                _make_para("Name Here", "para_name"),
                _make_para("", "para_empty", "empty"),  # single empty slot
            ],
            sections=[profile_sec, exp_sec],
            layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id
        ]
        return orig

    def test_summary_after_profile_before_experience(self, monkeypatch):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = self._build_profile_exp_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="TITLE BLOCK", semantic_type="other", body_lines=["Tagline"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Strong engineer with 10 years experience."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Dev | Corp", bullets=["Built systems."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types, "Summary section not found"

        sum_idx = types.index("summary")
        exp_idx = types.index("experience")
        assert sum_idx < exp_idx, f"Summary ({sum_idx}) must be before experience ({exp_idx})"
        # Profile (other) comes first, summary after it
        if sum_idx > 0:
            assert types[sum_idx - 1] == "other", (
                f"Section before summary should be 'other' (profile), got {types[sum_idx-1]!r}"
            )

    def test_summary_contains_llm_text(self, monkeypatch):
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = self._build_profile_exp_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="TITLE BLOCK", semantic_type="other", body_lines=["Tagline"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Experienced engineer specializing in distributed systems."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Dev | Corp", bullets=["Built things."])]),
        ]
        updated = apply_tailored(orig, llm_secs)
        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        body_text = " ".join(p.text for p in summary.body_paras if p.text.strip())
        assert "Experienced engineer" in body_text, f"LLM summary text not found: {body_text!r}"

    def test_no_duplicate_summary_when_template_has_one(self, monkeypatch):
        """Template with explicit summary section — no second summary created."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument,
            ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.text_parser import LlmSection
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        sum_sec = ResumeSection(
            title="Summary",
            heading=_make_para("Summary", "para_sh", "section_heading"),
            semantic_type="summary",
            body_paras=[_make_para("Original summary text.", "para_sb")],
        )
        sum_sec.section_id = "sec_sum"
        orig = ResumeDocument(header_paras=[], sections=[sum_sec], layout=layout, all_paras=[])
        assign_stable_ids(orig)
        orig.layout_blocks = [LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id]

        llm_secs = [LlmSection(heading="Summary", semantic_type="summary",
                               body_lines=["Updated summary."])]
        updated = apply_tailored(orig, llm_secs)
        count = sum(1 for s in updated.sections if s.semantic_type == "summary")
        assert count == 1, f"Expected exactly 1 summary section, got {count}"
