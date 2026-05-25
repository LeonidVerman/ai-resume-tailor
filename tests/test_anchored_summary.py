"""Tests for anchored summary insertion in layout-bound mode.

When the LLM produces a Professional Summary but the template has none,
apply_tailored (with USE_LAYOUT_BOUND_UPDATER=True) should insert the summary
anchored to existing empty header paragraphs rather than dropping it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

_SAMPLE_31 = str(
    _ROOT / "tests/samples/resume/docx"
    / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
)
_SAMPLE_22 = str(
    _ROOT / "tests/samples/resume/docx"
    / "22-Software-Engineer-Editable-Resume-Template-Download-in-docx-4.docx"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_para(text: str, para_id: str, semantic: str = "empty"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _make_section(title: str, semantic_type: str, section_id: str):
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
    heading = ParaModel(text=title, style=ParaStyle(), semantic="section_heading")
    heading.para_id = f"hd_{section_id}"
    sec = ResumeSection(
        title=title, heading=heading, semantic_type=semantic_type,
    )
    sec.section_id = section_id
    return sec


def _make_role_entry(label: str, idx: int):
    from tailor.compiler.models import ParaModel, ParaStyle, RoleEntry
    h = ParaModel(text=f"Role {label}", style=ParaStyle(), semantic="role_header")
    h.para_id = f"rh_{idx}"
    m = ParaModel(text=f"2020 – 2021", style=ParaStyle(), semantic="role_meta")
    m.para_id = f"rm_{idx}"
    b = ParaModel(text=f"Did work {idx}.", style=ParaStyle(), semantic="bullet")
    b.para_id = f"rb_{idx}"
    return RoleEntry(header=h, meta_lines=[m], bullets=[b], role_id=f"role_{idx}")


def _build_doc_with_header_empties(n_empty_header: int = 3):
    """Build a minimal document that has n_empty_header empty header_paras at the
    end of a short header area (name + empties), plus a single experience section."""
    from tailor.compiler.models import (
        LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
    )
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection

    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )

    name_para = _make_para("JOHN DOE", "para_name", "section_heading")
    empty_paras = [_make_para("", f"para_e{i}", "empty") for i in range(n_empty_header)]

    exp_sec = _make_section("Work Experience", "experience", "sec_exp")
    exp_sec.roles = [_make_role_entry("Engineer | Acme", 1)]

    header_paras = [name_para] + empty_paras
    doc = ResumeDocument(
        header_paras=header_paras,
        sections=[exp_sec],
        layout=layout,
        all_paras=[],
    )
    assign_stable_ids(doc)
    all_lb_paras = list(header_paras)
    all_lb_paras.append(exp_sec.heading)
    for r in exp_sec.roles:
        all_lb_paras.extend([r.header] + r.meta_lines + r.bullets)
    doc.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id) for pm in all_lb_paras if pm.para_id
    ]
    return doc


def _llm_with_summary(summary_text: str = "I am a great engineer."):
    from tailor.compiler.text_parser import LlmRole, LlmSection
    return [
        LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=[summary_text],
        ),
        LlmSection(
            heading="Work Experience",
            semantic_type="experience",
            roles=[LlmRole(
                header="Engineer | Acme",
                meta_lines=["2020 – 2021"],
                bullets=["Did excellent work."],
            )],
        ),
    ]


# ---------------------------------------------------------------------------
# _find_summary_anchors unit tests
# ---------------------------------------------------------------------------

class TestFindSummaryAnchors:
    def test_finds_last_two_empty_header_paras(self):
        """Returns the last two empty header_paras (closest to first section)."""
        from tailor.compiler.updater import _find_summary_anchors

        doc = _build_doc_with_header_empties(3)
        result = _find_summary_anchors(doc)
        assert result is not None
        h_anchor, b_anchor = result
        # Both must be empty paragraphs
        assert h_anchor.text.strip() == ""
        assert b_anchor.text.strip() == ""
        # Both must have valid para_ids
        assert h_anchor.para_id
        assert b_anchor.para_id
        assert h_anchor.para_id != b_anchor.para_id

    def test_returns_single_anchor_when_only_one_empty(self):
        """Returns (None, body_anchor) when only 1 trailing empty slot exists."""
        from tailor.compiler.updater import _find_summary_anchors

        doc = _build_doc_with_header_empties(1)
        result = _find_summary_anchors(doc)
        assert result is not None
        heading_anchor, body_anchor = result
        assert heading_anchor is None  # no heading slot
        assert body_anchor.para_id != ""  # body slot found

    def test_returns_none_when_no_header_paras(self):
        """Returns None when header_paras is empty."""
        from tailor.compiler.updater import _find_summary_anchors

        doc = _build_doc_with_header_empties(3)
        doc.header_paras.clear()
        assert _find_summary_anchors(doc) is None

    def test_skips_non_empty_trailing_para(self):
        """Non-empty para between empties breaks the trailing cluster."""
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.updater import _find_summary_anchors

        doc = _build_doc_with_header_empties(3)
        # Insert non-empty para at end of header_paras → breaks trailing cluster
        doc.header_paras.append(_make_para("Contact: 555-1234", "para_contact", "paragraph"))
        result = _find_summary_anchors(doc)
        assert result is None

    def test_exact_two_empty(self):
        """Returns anchors when exactly 2 trailing empty paras exist."""
        from tailor.compiler.updater import _find_summary_anchors

        doc = _build_doc_with_header_empties(2)
        result = _find_summary_anchors(doc)
        assert result is not None


# ---------------------------------------------------------------------------
# _clean_summary_text unit tests
# ---------------------------------------------------------------------------

class TestCleanSummaryText:
    def test_strips_professional_summary_prefix(self):
        from tailor.compiler.updater import _clean_summary_text

        result = _clean_summary_text(["Professional Summary: I am a developer."])
        assert not result.lower().startswith("professional summary")
        assert "developer" in result

    def test_strips_current_date(self):
        from tailor.compiler.updater import _clean_summary_text

        result = _clean_summary_text(["Good engineer.", "Current Date: April 2026"])
        assert "Current Date" not in result
        assert "Good engineer" in result

    def test_joins_multiple_lines(self):
        from tailor.compiler.updater import _clean_summary_text

        result = _clean_summary_text(["Line one.", "Line two."])
        assert "Line one" in result
        assert "Line two" in result

    def test_empty_lines_returns_empty(self):
        from tailor.compiler.updater import _clean_summary_text

        assert _clean_summary_text([]) == ""
        assert _clean_summary_text(["  ", ""]) == ""


# ---------------------------------------------------------------------------
# _build_anchored_summary_section unit tests
# ---------------------------------------------------------------------------

class TestBuildAnchoredSummarySection:
    def test_uses_heading_anchor_para_id(self):
        from tailor.compiler.text_parser import LlmSection
        from tailor.compiler.updater import _build_anchored_summary_section

        h_anchor = _make_para("", "para_h", "empty")
        b_anchor = _make_para("", "para_b", "empty")
        llm_sec = LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=["Great developer."],
        )
        sec = _build_anchored_summary_section(llm_sec, h_anchor, b_anchor)
        assert sec.heading.para_id == "para_h"
        # Heading slot is kept empty — no synthetic "PROFESSIONAL SUMMARY" label
        # is added for templates without a dedicated summary section.
        assert sec.heading.text == ""

    def test_uses_body_anchor_para_id(self):
        from tailor.compiler.text_parser import LlmSection
        from tailor.compiler.updater import _build_anchored_summary_section

        h_anchor = _make_para("", "para_h", "empty")
        b_anchor = _make_para("", "para_b", "empty")
        llm_sec = LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=["Great developer."],
        )
        sec = _build_anchored_summary_section(llm_sec, h_anchor, b_anchor)
        assert len(sec.body_paras) == 1
        assert sec.body_paras[0].para_id == "para_b"
        assert "Great developer" in sec.body_paras[0].text

    def test_section_id_is_non_empty(self):
        from tailor.compiler.text_parser import LlmSection
        from tailor.compiler.updater import _build_anchored_summary_section

        h_anchor = _make_para("", "para_h")
        b_anchor = _make_para("", "para_b")
        llm_sec = LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=["Text."],
        )
        sec = _build_anchored_summary_section(llm_sec, h_anchor, b_anchor)
        assert sec.section_id, "section_id must be non-empty"
        assert sec.semantic_type == "summary"


# ---------------------------------------------------------------------------
# Integration: apply_tailored with anchored insertion
# ---------------------------------------------------------------------------

class TestAnchoredSummaryInsertion:
    def test_summary_inserted_when_anchors_available(self, monkeypatch):
        """Summary is anchored to empty header_paras when template has none."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        updated = apply_tailored(doc, _llm_with_summary("Senior engineer with 5 years."))

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types, "Summary section must be present"

    def test_summary_uses_valid_para_ids(self, monkeypatch):
        """Anchored summary has no para_id='' paragraphs."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        updated = apply_tailored(doc, _llm_with_summary("Experienced engineer."))

        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        assert summary.heading.para_id, "Summary heading must have para_id"
        for p in summary.body_paras:
            assert p.para_id, f"Summary body para must have para_id, text={p.text!r}"

    def test_summary_section_id_non_empty(self, monkeypatch):
        """Anchored summary section has non-empty section_id (not synthetic)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        updated = apply_tailored(doc, _llm_with_summary("Text."))

        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        assert summary.section_id, "Summary section_id must not be ''"

    def test_anchors_removed_from_header_paras(self, monkeypatch):
        """Anchor para_ids are removed from header_paras to prevent duplicates."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import _find_summary_anchors, apply_tailored

        doc = _build_doc_with_header_empties(3)
        anchors = _find_summary_anchors(doc)
        assert anchors is not None
        h_pid, b_pid = anchors[0].para_id, anchors[1].para_id

        updated = apply_tailored(doc, _llm_with_summary("Text."))

        header_ids = {p.para_id for p in updated.header_paras}
        assert h_pid not in header_ids, "Heading anchor must be removed from header_paras"
        assert b_pid not in header_ids, "Body anchor must be removed from header_paras"

    def test_no_para_id_empty_violations(self, monkeypatch):
        """No non-empty para with para_id='' after anchored insertion."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        updated = apply_tailored(doc, _llm_with_summary("Great engineer."))

        all_paras = list(updated.header_paras)
        for sec in updated.sections:
            all_paras.append(sec.heading)
            for r in sec.roles:
                all_paras.extend([r.header] + r.meta_lines + r.bullets)
            all_paras.extend(sec.body_paras)

        unbound = [p for p in all_paras if not p.para_id and p.text.strip()]
        assert not unbound, f"Unbound non-empty paras: {[p.text[:40] for p in unbound]}"

    def test_summary_skipped_when_no_anchors(self, monkeypatch):
        """Summary dropped when no empty trailing header slots exist."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(0)  # name only, no empty slots
        updated = apply_tailored(doc, _llm_with_summary("Text."))

        types = [s.semantic_type for s in updated.sections]
        assert "summary" not in types, "Summary must be dropped when no safe anchors"

    def test_summary_inserted_with_single_anchor(self, monkeypatch):
        """Summary IS inserted when only 1 empty header slot exists (body-only mode)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(1)
        updated = apply_tailored(doc, _llm_with_summary("Full summary text here."))

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types  # inserted using the single empty slot
        summary = next(s for s in updated.sections if s.semantic_type == "summary")
        # Body contains full text — no truncation
        assert "Full summary text here." in summary.body_paras[0].text

    def test_name_contact_not_overwritten(self, monkeypatch):
        """Name and contact paragraphs are not overwritten by summary insertion."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        # Find the name para (first header para, non-empty)
        name_para = doc.header_paras[0]
        name_text = name_para.text
        name_pid = name_para.para_id

        updated = apply_tailored(doc, _llm_with_summary("Summary text."))

        # Name para should still be in header_paras with unchanged text
        header_pids = {p.para_id: p for p in updated.header_paras}
        assert name_pid in header_pids, "Name para must remain in header_paras"
        assert header_pids[name_pid].text == name_text, "Name text must not be modified"

    def test_summary_before_experience(self, monkeypatch):
        """Anchored summary section appears before the experience section in IR."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _build_doc_with_header_empties(3)
        updated = apply_tailored(doc, _llm_with_summary("Experienced."))

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types
        assert "experience" in types
        assert types.index("summary") < types.index("experience"), (
            "Summary must appear before experience in sections list"
        )

    def test_existing_summary_updated_not_duplicated(self, monkeypatch):
        """When template already has a summary section, it is updated (not anchored)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        empty_1 = _make_para("", "hp_e1")
        empty_2 = _make_para("", "hp_e2")

        summary_sec = _make_section("Profile", "summary", "sec_summ")
        body_pm = _make_para("Old profile text.", "para_body_s")
        summary_sec.body_paras = [body_pm]

        exp_sec = _make_section("Work Experience", "experience", "sec_exp")
        exp_sec.roles = [_make_role_entry("Engineer | Acme", 1)]

        doc = ResumeDocument(
            header_paras=[empty_1, empty_2],
            sections=[summary_sec, exp_sec],
            layout=layout,
            all_paras=[],
        )
        assign_stable_ids(doc)
        all_lb = [empty_1, empty_2, summary_sec.heading, body_pm, exp_sec.heading]
        for r in exp_sec.roles:
            all_lb.extend([r.header] + r.meta_lines + r.bullets)
        doc.layout_blocks = [LayoutParagraphBlock(para_id=p.para_id) for p in all_lb if p.para_id]

        updated = apply_tailored(doc, _llm_with_summary("New summary text."))

        summary_secs = [s for s in updated.sections if s.semantic_type == "summary"]
        assert len(summary_secs) == 1, "Exactly one summary section"
        # The existing summary should be updated with new text
        assert "New summary text" in " ".join(p.text for p in summary_secs[0].body_paras)

    def test_matched_original_summary_not_duplicated_by_anchor_path(self, monkeypatch):
        """Fix 2: matched original summary must not appear TWICE in new_sections.

        When the template has an existing summary section that is matched by the
        LLM output, the anchored_summaries list must only include sections whose
        section_id == 'sec_summary_inserted'.  The matched original (different
        section_id) must not be re-inserted, preventing double-summary output.
        """
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import (
            LayoutParagraphBlock, LayoutProfile, ResumeDocument, assign_stable_ids,
        )
        from tailor.compiler.updater import apply_tailored

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        # Template with: name + 2 empty header slots + existing summary + experience.
        name_para = _make_para("John Doe", "para_name", "section_heading")
        empty_1 = _make_para("", "hp_f1")
        empty_2 = _make_para("", "hp_f2")

        summary_sec = _make_section("Professional Summary", "summary", "sec_original_summ")
        body_pm = _make_para("Old profile text.", "para_old_body")
        summary_sec.body_paras = [body_pm]

        exp_sec = _make_section("Work Experience", "experience", "sec_exp_f")
        exp_sec.roles = [_make_role_entry("Engineer | Corp", 2)]

        doc = ResumeDocument(
            header_paras=[name_para, empty_1, empty_2],
            sections=[summary_sec, exp_sec],
            layout=layout,
            all_paras=[],
        )
        assign_stable_ids(doc)
        all_lb = [name_para, empty_1, empty_2, summary_sec.heading, body_pm, exp_sec.heading]
        for r in exp_sec.roles:
            all_lb.extend([r.header] + r.meta_lines + r.bullets)
        doc.layout_blocks = [LayoutParagraphBlock(para_id=p.para_id) for p in all_lb if p.para_id]

        # LLM outputs both a summary and an experience section.
        updated = apply_tailored(doc, _llm_with_summary("New tailored summary text."))

        # Must have exactly ONE summary section — the matched original, updated.
        summary_secs = [s for s in updated.sections if s.semantic_type == "summary"]
        assert len(summary_secs) == 1, (
            f"Expected exactly 1 summary section, got {len(summary_secs)}: "
            f"{[s.section_id for s in summary_secs]}"
        )
        # The original section_id must be preserved (not 'sec_summary_inserted').
        assert summary_secs[0].section_id != "sec_summary_inserted", (
            "Matched original summary must NOT be replaced with a synthetic anchored one"
        )
        # Text must be updated to the LLM output.
        combined = " ".join(p.text for p in summary_secs[0].body_paras)
        assert "New tailored summary text" in combined


# ---------------------------------------------------------------------------
# Sample 31 integration tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not Path(_SAMPLE_31).exists(),
    reason="sample 31 DOCX not present",
)
class TestSample31AnchoredSummary:
    def test_summary_inserted_with_para7_para8(self, monkeypatch):
        """Sample 31 anchored summary uses the first two trailing empty header slots.

        _find_summary_anchors returns trailing[0], trailing[1] (closest to name/title),
        which for sample 31 are para_6 (heading) and para_7 (body).  para_8 is kept
        as a spacer between the summary and the first section.
        """
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        llm = [
            LlmSection("Professional Summary", "summary",
                       body_lines=["Experienced web developer with 5+ years."]),
            LlmSection("WORK EXPERIENCE", "experience", roles=[
                LlmRole("Web Developer | Liceria & Co.", meta_lines=["2019 - Present"],
                        bullets=["Built responsive websites."]),
                LlmRole("Web Designer | Borcelle Company", meta_lines=["2016-2018"],
                        bullets=["Designed UI/UX."]),
                LlmRole("Web Development Intern | Fauget", meta_lines=["2014-2015"],
                        bullets=["Maintained websites."]),
            ]),
            LlmSection("SKILLS", "skills",
                       body_lines=["HTML, CSS, JavaScript"]),
        ]
        updated = apply_tailored(doc, llm)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Summary must be inserted for sample 31"
        assert summary.heading.para_id == "para_6"
        assert summary.body_paras and summary.body_paras[0].para_id == "para_7"
        assert summary.section_id == "sec_summary_inserted"

    def test_summary_before_education(self, monkeypatch):
        """Summary section appears before EDUCATION in the IR sections list."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        llm = [
            LlmSection("Professional Summary", "summary",
                       body_lines=["Great developer."]),
            LlmSection("WORK EXPERIENCE", "experience", roles=[
                LlmRole("Web Developer | Liceria & Co.", bullets=["Built sites."]),
                LlmRole("Web Designer | Borcelle", bullets=["Designed UI."]),
                LlmRole("Web Dev Intern | Fauget", bullets=["Maintained tools."]),
            ]),
            LlmSection("SKILLS", "skills", body_lines=["HTML, CSS, JavaScript"]),
        ]
        updated = apply_tailored(doc, llm)

        types = [s.semantic_type for s in updated.sections]
        assert "summary" in types
        assert "education" in types
        assert types.index("summary") < types.index("education"), (
            "Summary must appear before education"
        )

    def test_no_para_id_empty_after_insertion(self, monkeypatch):
        """No non-empty para with para_id='' after sample 31 anchored insertion."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        llm = [
            LlmSection("Professional Summary", "summary",
                       body_lines=["Excellent engineer."]),
            LlmSection("WORK EXPERIENCE", "experience", roles=[
                LlmRole("Web Developer | Liceria & Co.", bullets=["Built sites."]),
                LlmRole("Web Designer | Borcelle", bullets=["Designed UI."]),
                LlmRole("Web Dev Intern | Fauget", bullets=["Maintained."]),
            ]),
        ]
        updated = apply_tailored(doc, llm)

        all_paras = list(updated.header_paras)
        for sec in updated.sections:
            all_paras.append(sec.heading)
            for r in sec.roles:
                all_paras.extend([r.header] + r.meta_lines + r.bullets)
            all_paras.extend(sec.body_paras)

        unbound = [p for p in all_paras if not p.para_id and p.text.strip()]
        assert not unbound, f"{len(unbound)} unbound paras: {[p.text[:40] for p in unbound[:3]]}"

    def test_para7_para8_not_in_header_paras(self, monkeypatch):
        """para_6 and para_7 (anchor slots) are removed from header_paras after insertion."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        llm = [
            LlmSection("Professional Summary", "summary", body_lines=["Great dev."]),
            LlmSection("WORK EXPERIENCE", "experience", roles=[
                LlmRole("Web Developer | Co.", bullets=["Did work."]),
                LlmRole("Web Designer | Co.", bullets=["Did design."]),
                LlmRole("Intern | Co.", bullets=["Did intern."]),
            ]),
            LlmSection("SKILLS", "skills", body_lines=["HTML, CSS"]),
        ]
        updated = apply_tailored(doc, llm)

        header_ids = {p.para_id for p in updated.header_paras}
        assert "para_6" not in header_ids, "para_6 must be removed from header_paras"
        assert "para_7" not in header_ids, "para_7 must be removed from header_paras"


# ---------------------------------------------------------------------------
# Sample 22 (Lydia) non-regression
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not Path(_SAMPLE_22).exists(),
    reason="sample 22 DOCX not present",
)
class TestSample22SummaryNonRegression:
    def test_lydia_summary_if_existing(self, monkeypatch):
        """Sample 22 summary section (if present) is updated normally."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        sys.path.insert(0, str(_ROOT / "scripts"))
        from ir_health_check import _build_synthetic_llm_sections  # noqa: E402

        doc = parse_docx(_SAMPLE_22)
        llm = _build_synthetic_llm_sections(doc)
        updated = apply_tailored(doc, llm)

        all_paras = list(updated.header_paras)
        for sec in updated.sections:
            all_paras.append(sec.heading)
            for r in sec.roles:
                all_paras.extend([r.header] + r.meta_lines + r.bullets)
            all_paras.extend(sec.body_paras)

        unbound = [p for p in all_paras if not p.para_id and p.text.strip()]
        assert not unbound, f"Sample 22 regression: {len(unbound)} unbound paras"
