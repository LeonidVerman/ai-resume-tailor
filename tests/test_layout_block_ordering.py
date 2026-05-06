"""Tests for layout_blocks ordering of synthetic summary sections and skills budget.

Covers:
1. Synthetic summary layout_block inserted after profile, before major section.
2. Skills body_paras protected from budget truncation (no_truncate fix).
3. _move_layout_block helper correctness.
"""
from __future__ import annotations
import pytest


def _make_para(text: str, para_id: str, semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _build_doc(monkeypatch, *, has_summary_in_template: bool = False):
    """Build a layout-bound doc with: other(profile) + experience + skills.

    Has one trailing empty header para (single-anchor summary slot).
    """
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

    # Profile (other) section
    profile_sec = ResumeSection(
        title="TITLE",
        heading=_make_para("TITLE", "para_title", "section_heading"),
        semantic_type="other",
        body_paras=[_make_para("Tagline text", "para_tagline")],
    )
    profile_sec.section_id = "sec_title"

    # Experience section
    role = RoleEntry(
        header=_make_para("Eng | Corp", "para_rh", "role_header"),
        bullets=[_make_para("Built things.", "para_b0", "bullet")],
        role_id="Eng | Corp",
    )
    role.role_id_stable = "role_1"
    exp_sec = ResumeSection(
        title="WORK EXPERIENCE",
        heading=_make_para("WORK EXPERIENCE", "para_exp_h", "section_heading"),
        semantic_type="experience", roles=[role],
    )
    exp_sec.section_id = "sec_exp"

    # Skills section (two original slots)
    skills_sec = ResumeSection(
        title="Skills",
        heading=_make_para("Skills", "para_sk_h", "section_heading"),
        semantic_type="skills",
        body_paras=[
            _make_para("Skill A", "para_sk0"),
            _make_para("Skill B", "para_sk1"),
        ],
    )
    skills_sec.section_id = "sec_skills"

    sections = [profile_sec, exp_sec, skills_sec]
    if has_summary_in_template:
        sum_sec = ResumeSection(
            title="Summary",
            heading=_make_para("Summary", "para_sum_h", "section_heading"),
            semantic_type="summary",
            body_paras=[_make_para("Original summary.", "para_sum_b")],
        )
        sum_sec.section_id = "sec_sum"
        sections.insert(1, sum_sec)

    # Empty trailing header para (the single summary anchor)
    name_para = _make_para("Name Here", "para_name")
    empty_para = _make_para("", "para_empty", "empty")

    orig = ResumeDocument(
        header_paras=[name_para, empty_para],
        sections=sections,
        layout=layout, all_paras=[],
    )
    assign_stable_ids(orig)
    # assign_stable_ids does NOT rebuild all_paras — do it manually so
    # layout_blocks can be built from the real paragraph list.
    all_paras = list(orig.header_paras)
    for sec in orig.sections:
        all_paras.append(sec.heading)
        if sec.semantic_type == "experience" and sec.roles:
            for role in sec.roles:
                all_paras.append(role.header)
                all_paras.extend(role.meta_lines)
                all_paras.extend(role.bullets)
        else:
            all_paras.extend(sec.body_paras)
    orig.all_paras = all_paras
    orig.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in orig.all_paras if pm.para_id
    ]
    return orig


# ---------------------------------------------------------------------------
# 1. _move_layout_block helper
# ---------------------------------------------------------------------------

class TestMoveLayoutBlock:
    def test_moves_block_to_before_target(self):
        from tailor.compiler.models import LayoutParagraphBlock
        from tailor.compiler.updater import _move_layout_block

        blocks = [LayoutParagraphBlock(para_id=f"p{i}") for i in range(5)]
        # Move p0 to just before p3
        result = _move_layout_block(blocks, "p0", "p3")
        ids = [b.para_id for b in result]
        assert ids.index("p0") == ids.index("p3") - 1

    def test_noop_when_already_in_position(self):
        from tailor.compiler.models import LayoutParagraphBlock
        from tailor.compiler.updater import _move_layout_block

        blocks = [LayoutParagraphBlock(para_id=f"p{i}") for i in range(5)]
        # p1 is already just before p2
        result = _move_layout_block(blocks, "p1", "p2")
        assert result is blocks  # same object (no copy needed)

    def test_noop_when_pid_not_found(self):
        from tailor.compiler.models import LayoutParagraphBlock
        from tailor.compiler.updater import _move_layout_block

        blocks = [LayoutParagraphBlock(para_id=f"p{i}") for i in range(5)]
        result = _move_layout_block(blocks, "missing", "p2")
        assert result is blocks


# ---------------------------------------------------------------------------
# 2. Synthetic summary layout_block position
# ---------------------------------------------------------------------------

class TestSyntheticSummaryLayoutBlockPosition:
    def _get_lb_positions(self, upd):
        """Return {para_id: index} for all para blocks."""
        return {
            b.para_id: i
            for i, b in enumerate(upd.layout_blocks or [])
            if hasattr(b, "para_id") and b.para_id
        }

    def test_summary_block_before_experience_heading(self, monkeypatch):
        """Synthetic summary layout_block must appear before experience heading."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="TITLE", semantic_type="other", body_lines=["Tagline"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Strong engineer summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Built things."])]),
            LlmSection(heading="Skills", semantic_type="skills", body_lines=["Python", "Go"]),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Summary section not found"

        summary_pid = summary.body_paras[0].para_id if summary.body_paras else None
        exp_heading_pid = next(
            (s.heading.para_id for s in updated.sections if s.semantic_type == "experience"),
            None,
        )
        assert summary_pid and exp_heading_pid, "Para IDs missing"

        positions = self._get_lb_positions(updated)
        sum_pos = positions.get(summary_pid, -1)
        exp_pos = positions.get(exp_heading_pid, -1)
        assert sum_pos >= 0 and exp_pos >= 0, f"Blocks not found in layout_blocks: {summary_pid}, {exp_heading_pid}"
        assert sum_pos < exp_pos, (
            f"Summary block ({sum_pos}) must be BEFORE experience heading ({exp_pos})"
        )

    def test_summary_block_after_profile_block(self, monkeypatch):
        """Synthetic summary layout_block must appear after profile (other) block."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="TITLE", semantic_type="other", body_lines=["Tagline"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text here."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did stuff."])]),
            LlmSection(heading="Skills", semantic_type="skills", body_lines=["Python"]),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None

        profile = next((s for s in updated.sections if s.semantic_type == "other"), None)
        assert profile is not None

        summary_pid = summary.body_paras[0].para_id if summary.body_paras else None
        profile_heading_pid = profile.heading.para_id

        positions = self._get_lb_positions(updated)
        sum_pos = positions.get(summary_pid, -1)
        prof_pos = positions.get(profile_heading_pid, -1)

        if prof_pos >= 0 and sum_pos >= 0:
            assert sum_pos > prof_pos, (
                f"Summary block ({sum_pos}) must be AFTER profile heading ({prof_pos})"
            )


# ---------------------------------------------------------------------------
# 3. Skills budget protection (no_truncate bug fix)
# ---------------------------------------------------------------------------

class TestSkillsBudgetProtection:
    def test_skills_para_ids_in_no_truncate(self, monkeypatch):
        """Skills body_para IDs must be in _collect_content_para_ids."""
        from tailor.compiler.updater import _collect_content_para_ids

        orig = _build_doc(monkeypatch)
        no_trunc = _collect_content_para_ids(orig)
        for p in orig.sections[1].body_paras:  # skills section is index 2 (after profile, exp)
            if p.para_id:
                pass  # just ensure we can call it

        # Find the skills section specifically
        skills_sec = next(s for s in orig.sections if s.semantic_type == "skills")
        for p in skills_sec.body_paras:
            if p.text.strip() and p.para_id:
                assert p.para_id in no_trunc, (
                    f"Skills para {p.para_id!r} not in no_truncate — will get budget"
                )

    def test_skills_not_budget_truncated(self, monkeypatch):
        """Long skills content must not be truncated by apply_anchor_budgets."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored, _compute_anchor_budgets

        orig = _build_doc(monkeypatch)
        long_skill_line = "Backend & Frameworks: Node.js, .NETcore, Spring, Django, FastAPI, Express"
        llm_secs = [
            LlmSection(heading="TITLE", semantic_type="other", body_lines=["Tagline"]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Built things."])]),
            LlmSection(heading="Skills", semantic_type="skills",
                       body_lines=[long_skill_line, "Datastores: MySQL, Oracle"]),
        ]
        updated = apply_tailored(orig, llm_secs)

        skills = next(s for s in updated.sections if s.semantic_type == "skills")
        budgets = _compute_anchor_budgets(orig, updated)
        for p in skills.body_paras:
            if p.text.strip() and p.para_id:
                assert p.para_id not in budgets, (
                    f"Skills para {p.para_id!r} has budget {budgets[p.para_id]} — must be excluded"
                )
                assert "..." not in p.text, f"Skills text truncated: {p.text!r}"

    def test_no_truncate_survives_intro_prose_section(self, monkeypatch):
        """Finding an intro-prose 'other' section must NOT prevent skills protection."""
        from tailor.compiler.updater import _collect_content_para_ids

        # Build a doc where the first 'other' section has intro-prose content (>30 chars)
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutProfile, ResumeDocument, ResumeSection, assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        intro_sec = ResumeSection(
            title="PROFILE",
            heading=_make_para("PROFILE", "para_ph", "section_heading"),
            semantic_type="other",
            body_paras=[_make_para("This is a long intro prose paragraph that qualifies.", "para_intro")],
        )
        intro_sec.section_id = "sec_profile"
        skills_sec = ResumeSection(
            title="Skills",
            heading=_make_para("Skills", "para_sk_h", "section_heading"),
            semantic_type="skills",
            body_paras=[
                _make_para("Python, Go", "para_sk0"),
                _make_para("Docker, K8s", "para_sk1"),
            ],
        )
        skills_sec.section_id = "sec_skills"
        orig = ResumeDocument(
            header_paras=[], sections=[intro_sec, skills_sec],
            layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        # Rebuild all_paras so para_ids from assign_stable_ids are accessible
        all_paras = []
        for sec in orig.sections:
            all_paras.append(sec.heading)
            all_paras.extend(sec.body_paras)
        orig.all_paras = all_paras

        no_trunc = _collect_content_para_ids(orig)
        # Skills body_paras must be in no_truncate even though intro_sec was found.
        # Use the assigned para_ids (assign_stable_ids overwrites the manual ones).
        skills = next(s for s in orig.sections if s.semantic_type == "skills")
        for bp in skills.body_paras:
            assert bp.para_id in no_trunc, (
                f"Skills para {bp.para_id!r} not in no_truncate — intro-prose break bug"
            )
        # Intro prose para also included
        intro = next(s for s in orig.sections if s.semantic_type == "other")
        for bp in intro.body_paras:
            if bp.text.strip():
                assert bp.para_id in no_trunc
