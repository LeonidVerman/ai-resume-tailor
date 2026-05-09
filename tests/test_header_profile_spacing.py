"""Tests for header→profile spacing repair when summary anchor was the sole spacer.

Covers:
1. Spacer inserted when the former header spacer becomes the summary body anchor.
2. Replacement spacer appears before first title section (not duplicated later).
3. No synthetic spacer inserted when existing spacing already survives the move.
"""
from __future__ import annotations
import pytest


def _make_para(text: str, para_id: str, semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _build_single_anchor_doc(monkeypatch):
    """Template that mirrors Sample 5: header ends with ONE empty para (the spacer).

    Layout:
      header_paras: [name(p2), phone(p5), spacer(p6)]
      sections:     [other(p7 "SENIOR SOFTWARE DEVELOPER"), experience(p14 "WORK EXPERIENCE")]

    para_6 is both the visual spacer between contact block and SENIOR SOFTWARE DEVELOPER
    AND the only trailing empty header para → becomes summary body anchor.
    After anchoring, para_6 moves to near WORK EXPERIENCE and the header→profile
    boundary collapses unless the fix inserts a synthetic spacer.
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

    # Profile (other) section — the "title block" that comes right after the header.
    # Trailing empty para mirrors the original spacer before WORK EXPERIENCE.
    profile_sec = ResumeSection(
        title="SENIOR SOFTWARE DEVELOPER",
        heading=_make_para("SENIOR SOFTWARE DEVELOPER", "p7", "section_heading"),
        semantic_type="other",
        body_paras=[
            _make_para("Tagline / profile text.", "p8"),
            _make_para("", "p_sp_we", "empty"),  # spacer before WORK EXPERIENCE
        ],
    )
    profile_sec.section_id = "sec_profile"

    # Experience section
    role = RoleEntry(
        header=_make_para("Eng | Corp", "p_rh", "role_header"),
        bullets=[_make_para("Built things.", "p_b0", "bullet")],
        role_id="Eng | Corp",
    )
    role.role_id_stable = "role_1"
    exp_sec = ResumeSection(
        title="WORK EXPERIENCE",
        heading=_make_para("WORK EXPERIENCE", "p14", "section_heading"),
        semantic_type="experience",
        roles=[role],
    )
    exp_sec.section_id = "sec_exp"

    orig = ResumeDocument(
        header_paras=[
            _make_para("Name Here", "p2"),
            _make_para("phone@email.com", "p5"),
            _make_para("", "p6", "empty"),  # sole spacer AND future summary anchor
        ],
        sections=[profile_sec, exp_sec],
        layout=layout,
        all_paras=[],
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
    orig.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in orig.all_paras if pm.para_id
    ]
    return orig


def _build_two_anchor_doc(monkeypatch):
    """Template with TWO trailing empty header paras (heading + body anchors).

    The spacer between header and first section heading is a separate block
    (not an anchor), so it survives the summary insertion unchanged.
    No synthetic spacer should be added.
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

    profile_sec = ResumeSection(
        title="PROFILE",
        heading=_make_para("PROFILE", "p_ph", "section_heading"),
        semantic_type="other",
        body_paras=[_make_para("Tagline.", "p_pb")],
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
        heading=_make_para("WORK EXPERIENCE", "p_eh", "section_heading"),
        semantic_type="experience",
        roles=[role],
    )
    exp_sec.section_id = "sec_exp"

    orig = ResumeDocument(
        header_paras=[
            _make_para("Name Here", "p_name"),
            _make_para("phone@email.com", "p_contact"),
            _make_para("", "p_spacer", "empty"),   # non-anchor spacer (stays in place)
            _make_para("", "p_h_anchor", "empty"),  # heading anchor (will be used for summary heading)
            _make_para("", "p_b_anchor", "empty"),  # body anchor (will be used for summary body)
        ],
        sections=[profile_sec, exp_sec],
        layout=layout,
        all_paras=[],
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
    orig.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in orig.all_paras if pm.para_id
    ]
    return orig


def _lb_order(updated):
    return [getattr(b, "para_id", None) for b in (updated.layout_blocks or [])]


# ---------------------------------------------------------------------------
# 1. Spacer inserted when former spacer becomes summary anchor
# ---------------------------------------------------------------------------

class TestHeaderProfileSpacerSynthesis:
    def test_spacer_inserted_between_header_and_title_section(self, monkeypatch):
        """After summary insertion, spacer_header_auto_1 must appear between
        last contact header para and first section heading."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_single_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="SENIOR SOFTWARE DEVELOPER", semantic_type="other",
                       body_lines=["Tagline text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Innovative engineer with 10+ years experience."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Built systems."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        order = _lb_order(updated)
        assert "spacer_header_auto_1" in order, (
            "Synthetic spacer 'spacer_header_auto_1' missing from layout_blocks after "
            "summary anchor repurposed the header→profile spacer"
        )

    def test_spacer_positioned_between_contact_and_title_heading(self, monkeypatch):
        """spacer_header_auto_1 must be between the last contact header para (p5)
        and the first section heading (p7 / SENIOR SOFTWARE DEVELOPER)."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_single_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="SENIOR SOFTWARE DEVELOPER", semantic_type="other",
                       body_lines=["Profile text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Strong engineer with deep expertise."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Delivered work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        order = _lb_order(updated)
        # Use the stable IDs assigned by assign_stable_ids (p2→para_1, p5→para_2, etc.)
        # Locate last header para and first section heading by their assigned IDs
        last_header_pid = updated.header_paras[-1].para_id if updated.header_paras else None
        first_sec_hpid = updated.sections[0].heading.para_id if updated.sections else None

        spacer_pos = order.index("spacer_header_auto_1") if "spacer_header_auto_1" in order else -1
        last_hp_pos = order.index(last_header_pid) if last_header_pid in order else -1
        first_sh_pos = order.index(first_sec_hpid) if first_sec_hpid in order else -1

        assert spacer_pos >= 0, "spacer_header_auto_1 not found"
        assert last_hp_pos >= 0 and first_sh_pos >= 0, "Reference para IDs not in layout_blocks"
        assert last_hp_pos < spacer_pos < first_sh_pos, (
            f"Expected: last_header ({last_hp_pos}) < spacer ({spacer_pos}) < "
            f"first_heading ({first_sh_pos}); order={order[:first_sh_pos + 2]}"
        )

    def test_summary_remains_after_title_block(self, monkeypatch):
        """Summary must appear AFTER the profile/title section body, not before it."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_single_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="SENIOR SOFTWARE DEVELOPER", semantic_type="other",
                       body_lines=["Profile text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types, "Summary section missing"
        assert "other" in types, "Profile (other) section missing"
        assert types.index("other") < types.index("summary"), (
            "Profile (other) section must appear before summary"
        )

    def test_spacing_before_work_experience_preserved(self, monkeypatch):
        """Original spacer before WORK EXPERIENCE is also preserved (cumulative fix)."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_single_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="SENIOR SOFTWARE DEVELOPER", semantic_type="other",
                       body_lines=["Profile text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        order = _lb_order(updated)
        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert summary and exp, "Missing summary or experience section"

        sum_pid = summary.body_paras[0].para_id if summary.body_paras else None
        exp_hpid = exp.heading.para_id

        if sum_pid in order and exp_hpid in order:
            sum_pos = order.index(sum_pid)
            exp_pos = order.index(exp_hpid)
            between = order[sum_pos + 1: exp_pos]
            assert between, (
                f"No blocks between summary ({sum_pos}) and WORK EXPERIENCE ({exp_pos}); "
                f"spacer before heading was lost"
            )


# ---------------------------------------------------------------------------
# 3. No duplicate spacer when existing spacing already survives
# ---------------------------------------------------------------------------

class TestNoSpuriousSpacerInsertion:
    def test_no_synthetic_spacer_when_spacing_survives(self, monkeypatch):
        """When the non-anchor spacer block stays between header and first section
        heading after summary insertion, spacer_header_auto_1 must NOT appear."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_two_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="PROFILE", semantic_type="other",
                       body_lines=["Profile text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Delivered work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        order = _lb_order(updated)
        assert "spacer_header_auto_1" not in order, (
            "spacer_header_auto_1 must NOT be inserted when existing spacing "
            "between header and first section already survives"
        )

    def test_spacer_count_not_multiplied(self, monkeypatch):
        """After summary insertion, exactly one spacer_header_auto_1 appears —
        the repair runs once and does not add multiple synthetic spacers."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        orig = _build_single_anchor_doc(monkeypatch)
        llm_secs = [
            LlmSection(heading="SENIOR SOFTWARE DEVELOPER", semantic_type="other",
                       body_lines=["Profile text."]),
            LlmSection(heading="Professional Summary", semantic_type="summary",
                       body_lines=["Summary text."]),
            LlmSection(heading="WORK EXPERIENCE", semantic_type="experience",
                       roles=[LlmRole(header="Eng | Corp", bullets=["Did work."])]),
        ]
        updated = apply_tailored(orig, llm_secs)

        order = _lb_order(updated)
        count = order.count("spacer_header_auto_1")
        assert count == 1, (
            f"Expected exactly 1 'spacer_header_auto_1' in layout_blocks; got {count}"
        )
