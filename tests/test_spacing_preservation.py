"""Tests for spacer/spacing preservation around major section headers.

Covers:
- Spacer paragraphs between synthetic summary and major heading are preserved.
- Summary is not immediately adjacent to heading when original had a spacer.
- No-spacer case still works (graceful degradation).
"""
from __future__ import annotations
import pytest


def _make_para(text: str, para_id: str, semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _build_doc_with_spacer(monkeypatch, has_spacer: bool = True):
    """Template: profile section (other) + optional spacer + experience."""
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

    profile_body = [_make_para("Award text.", "p_profile_b")]
    if has_spacer:
        profile_body.append(_make_para("", "p_spacer", "empty"))  # intentional spacer

    profile_sec = ResumeSection(
        title="PROFILE",
        heading=_make_para("PROFILE", "p_profile_h", "section_heading"),
        semantic_type="other",
        body_paras=profile_body,
    )
    profile_sec.section_id = "sec_profile"

    role = RoleEntry(
        header=_make_para("Eng | Corp", "p_rh", "role_header"),
        bullets=[_make_para("Built things.", "p_b0", "bullet")],
        role_id="Eng | Corp",
    )
    role.role_id_stable = "role_1"
    exp_sec = ResumeSection(
        title="WORK EXPERIENCE",
        heading=_make_para("WORK EXPERIENCE", "p_exp_h", "section_heading"),
        semantic_type="experience", roles=[role],
    )
    exp_sec.section_id = "sec_exp"

    orig = ResumeDocument(
        header_paras=[_make_para("Name", "p_name"), _make_para("", "p_anchor", "empty")],
        sections=[profile_sec, exp_sec],
        layout=layout, all_paras=[],
    )
    assign_stable_ids(orig)
    # Rebuild all_paras (assign_stable_ids does not do this)
    all_paras = list(orig.header_paras)
    for sec in orig.sections:
        all_paras.append(sec.heading)
        all_paras.extend(sec.body_paras)
        for r in sec.roles:
            all_paras.append(r.header)
            all_paras.extend(r.bullets)
    orig.all_paras = all_paras
    orig.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in orig.all_paras if pm.para_id
    ]
    return orig


def _lb_order(updated):
    return [getattr(b, "para_id", None) for b in (updated.layout_blocks or [])]


class TestSpacingPreservation:
    def test_spacer_between_summary_and_heading(self, monkeypatch):
        """Original spacer before WORK EXPERIENCE must appear AFTER summary, not before."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_doc_with_spacer(monkeypatch, has_spacer=True)
        llm_secs = [
            LlmSection(heading="PROFILE", semantic_type="other", body_lines=["Award"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Experienced developer with 10 years."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Built systems."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None
        summary_pid = summary.body_paras[0].para_id if summary.body_paras else None
        exp_h_pid = next(s.heading.para_id for s in updated.sections if s.semantic_type == "experience")

        order = _lb_order(updated)
        sum_pos = order.index(summary_pid) if summary_pid in order else -1
        exp_pos = order.index(exp_h_pid) if exp_h_pid in order else -1
        assert sum_pos >= 0 and exp_pos >= 0

        # At least one block must exist between summary and experience heading
        between = order[sum_pos + 1: exp_pos]
        assert between, (
            f"No blocks between summary (pos {sum_pos}) and heading (pos {exp_pos}); "
            f"spacer was lost. full order: {order}"
        )

    def test_summary_not_immediately_adjacent_to_heading(self, monkeypatch):
        """Summary block and major heading must not be direct neighbors when spacer exists."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_doc_with_spacer(monkeypatch, has_spacer=True)
        llm_secs = [
            LlmSection(heading="PROFILE", semantic_type="other", body_lines=["Award"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text that is meaningful."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        summary_pid = summary.body_paras[0].para_id
        exp_h_pid = next(s.heading.para_id for s in updated.sections if s.semantic_type == "experience")

        order = _lb_order(updated)
        sum_pos = order.index(summary_pid)
        exp_pos = order.index(exp_h_pid)
        assert exp_pos > sum_pos + 1, (
            f"Summary (pos {sum_pos}) is immediately adjacent to heading (pos {exp_pos}) "
            "— original spacer was lost"
        )

    def test_no_spacer_case_still_works(self, monkeypatch):
        """When template has no spacer before heading, insertion still functions correctly."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_doc_with_spacer(monkeypatch, has_spacer=False)
        llm_secs = [
            LlmSection(heading="PROFILE", semantic_type="other", body_lines=["Content"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types, "Summary section missing"
        sum_idx = types.index("summary")
        exp_idx = types.index("experience")
        assert sum_idx < exp_idx, "Summary must appear before experience"

    def test_multiple_spacers_before_heading(self, monkeypatch):
        """Multiple consecutive spacers before heading are all kept between summary and heading."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument,
            ResumeSection, RoleEntry, assign_stable_ids,
        )
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        profile_sec = ResumeSection(
            title="PROFILE",
            heading=_make_para("PROFILE", "p_ph", "section_heading"),
            semantic_type="other",
            body_paras=[
                _make_para("Content.", "p_pb"),
                _make_para("", "p_sp1", "empty"),  # spacer 1
                _make_para("", "p_sp2", "empty"),  # spacer 2
            ],
        )
        profile_sec.section_id = "sec_p"
        role = RoleEntry(
            header=_make_para("Eng | Corp", "p_rh", "role_header"),
            bullets=[_make_para("Work.", "p_b0", "bullet")],
            role_id="Eng | Corp",
        )
        role.role_id_stable = "role_1"
        exp_sec = ResumeSection(
            title="EXP",
            heading=_make_para("EXP", "p_eh", "section_heading"),
            semantic_type="experience", roles=[role],
        )
        exp_sec.section_id = "sec_e"
        orig = ResumeDocument(
            header_paras=[_make_para("Name", "p_n"), _make_para("", "p_anchor", "empty")],
            sections=[profile_sec, exp_sec],
            layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)
        all_paras = list(orig.header_paras)
        for sec in orig.sections:
            all_paras.append(sec.heading)
            all_paras.extend(sec.body_paras)
            for r in sec.roles:
                all_paras.append(r.header)
                all_paras.extend(r.bullets)
        orig.all_paras = all_paras
        orig.layout_blocks = [LayoutParagraphBlock(para_id=pm.para_id) for pm in orig.all_paras if pm.para_id]

        llm_secs = [
            LlmSection(heading="PROFILE", semantic_type="other", body_lines=["Content"]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="EXP", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        summary_pid = summary.body_paras[0].para_id
        exp_h_pid = next(s.heading.para_id for s in updated.sections if s.semantic_type == "experience")

        order = _lb_order(updated)
        sum_pos = order.index(summary_pid)
        exp_pos = order.index(exp_h_pid)
        # Summary must be at least 2 positions before heading (2 spacers between)
        assert exp_pos >= sum_pos + 2, (
            f"Expected 2+ blocks between summary and heading; sum_pos={sum_pos}, exp_pos={exp_pos}"
        )
