"""Regression tests for the sample 31 deterministic artifact generator.

Tests the full pipeline (IR -> DOCX -> PDF -> layout report) for sample 31
without any LLM calls, using generation JSON 209.

Each test is self-contained and does not depend on pre-generated artifacts.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = str(_ROOT / "tools" / "debug")
_SCRIPTS = str(_ROOT / "scripts")

_GEN_JSON_209 = str(
    _ROOT / "tests/samples/generation"
    / "Gabriel_Mitchell-American_Tire_Distributors-Lead_Software_Engineer-209-20260428-003350.json"
)
_DOCX_PATH = str(
    _ROOT / "tests/samples/resume/docx"
    / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
)

sys.path.insert(0, _TOOLS)
sys.path.insert(0, _SCRIPTS)


# ---------------------------------------------------------------------------
# Anchor budget unit tests (using pipeline functions from updater.py)
# ---------------------------------------------------------------------------

class TestAnchorBudgets:
    def test_summary_budget_constants(self):
        """SUMMARY_BODY_BUDGET and SUMMARY_HEADING_BUDGET are defined."""
        from tailor.compiler.updater import SUMMARY_BODY_BUDGET, SUMMARY_HEADING_BUDGET
        assert SUMMARY_BODY_BUDGET >= 100
        assert SUMMARY_HEADING_BUDGET >= 20
        assert SUMMARY_HEADING_BUDGET < SUMMARY_BODY_BUDGET

    def test_compute_budgets_for_sample31(self):
        """_compute_anchor_budgets skips meaningful content para_ids (no truncation)."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import (
            SUMMARY_BODY_BUDGET, SUMMARY_HEADING_BUDGET, _compute_anchor_budgets,
        )

        doc = parse_docx(_DOCX_PATH)
        # Pass doc as both original and updated (no inserted summary yet)
        budgets = _compute_anchor_budgets(doc, doc)

        # Experience bullets and skills body_paras: NOT in budgets — no truncation allowed.
        # A budget of 0 (absent key) means _truncate_to_budget is never called for these.
        assert "para_39" not in budgets, "experience bullet must not have a budget"
        assert "para_43" not in budgets, "experience bullet must not have a budget"
        assert "para_47" not in budgets, "experience bullet must not have a budget"
        assert "para_49" not in budgets, "skills body para must not have a budget"
        assert "para_51" not in budgets, "skills body para must not have a budget"
        # para_6 (heading anchor) gets SUMMARY_HEADING_BUDGET — still in budgets
        assert budgets.get("para_6", 0) >= 1  # heading anchor has a budget
        # para_7 (body anchor) is in _no_truncate — NOT in budgets (full text preserved)
        assert "para_7" not in budgets, "summary body anchor must not have a budget"

    def test_apply_anchor_budgets_truncates_overlong(self, monkeypatch):
        """apply_anchor_budgets truncates a para that exceeds budget."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import (
            SUMMARY_BODY_BUDGET, _truncate_to_budget, apply_anchor_budgets,
        )
        from tailor.compiler.models import (
            LayoutProfile, ParaModel, ParaStyle, ResumeDocument, ResumeSection,
        )

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        # Build a minimal original doc with one empty header para (para_8)
        empty_pm = ParaModel(text="", style=ParaStyle(), semantic="empty")
        empty_pm.para_id = "para_8"
        orig = ResumeDocument(header_paras=[empty_pm], sections=[], layout=layout, all_paras=[])

        # Build updated doc: para_8 is now summary body with overlong text
        body_pm = ParaModel(text="x" * 500, style=ParaStyle(), semantic="paragraph")
        body_pm.para_id = "para_8"
        summ = ResumeSection(
            title="Professional Summary",
            heading=ParaModel(text="PROFESSIONAL SUMMARY", style=ParaStyle(), semantic="section_heading"),
            semantic_type="summary",
            body_paras=[body_pm],
        )
        summ.heading.para_id = "para_7"
        summ.section_id = "sec_summary_inserted"
        updated = ResumeDocument(
            header_paras=[], sections=[summ], layout=layout, all_paras=[],
        )
        result = apply_anchor_budgets(orig, updated)

        s = next(s for s in result.sections if s.semantic_type == "summary")
        assert len(s.body_paras[0].text) <= SUMMARY_BODY_BUDGET + 3  # allow "..."
        assert s.body_paras[0].para_id == "para_8"

    def test_truncate_to_budget_sentence_boundary(self):
        """_truncate_to_budget cuts at sentence boundary when possible."""
        from tailor.compiler.updater import _truncate_to_budget
        from tailor.compiler.models import ParaModel, ParaStyle

        text = "First sentence. Second sentence. Third sentence goes on and on."
        pm = ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
        pm.para_id = "para_test"
        result = _truncate_to_budget(pm, 35)
        assert result.text.endswith(".")
        assert len(result.text) <= 35

    def test_truncate_to_budget_hard_cut(self):
        """_truncate_to_budget hard-cuts when no sentence break is available."""
        from tailor.compiler.updater import _truncate_to_budget
        from tailor.compiler.models import ParaModel, ParaStyle

        text = "abcdefghijklmnopqrstuvwxyz" * 10
        pm = ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
        pm.para_id = "para_test"
        result = _truncate_to_budget(pm, 20)
        assert len(result.text) <= 20
        assert result.text.endswith("...")

    def test_enforce_anchor_budgets_no_truncation_within_budget(self, monkeypatch):
        """Paragraphs within budget are not modified."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_anchor_budgets
        from tailor.compiler.models import (
            ParaModel, ParaStyle, ResumeDocument, ResumeSection, LayoutProfile,
        )

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        pm = ParaModel(text="Short.", style=ParaStyle(), semantic="paragraph")
        pm.para_id = "para_8"

        sec = ResumeSection(
            title="Summary", heading=ParaModel(text="", style=ParaStyle(), semantic="section_heading"),
            semantic_type="summary", body_paras=[pm],
        )
        sec.section_id = "sec_summ"
        sec.heading.para_id = "hd_summ"

        # orig and updated are the same minimal doc (para_8 is short, within budget)
        orig_doc = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        result = apply_anchor_budgets(orig_doc, orig_doc)

        summ = next(s for s in result.sections if s.semantic_type == "summary")
        assert summ.body_paras[0].text == "Short."


# ---------------------------------------------------------------------------
# Full pipeline tests (require generation JSON 209)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not Path(_GEN_JSON_209).exists(),
    reason="generation JSON 209 not present",
)
class TestSample31ArtifactPipeline:
    def test_ir_passes_all_hard_invariants(self, monkeypatch, tmp_path):
        """Full pipeline IR passes all hard layout-bound invariants."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored
        from check_layout_bound_ir_health import check_layout_bound_ir_health

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_text = gen["llm_response"]["resume"]
        llm_sections = parse_llm_output(llm_text)
        updated = apply_tailored(doc, llm_sections)

        violations = check_layout_bound_ir_health(updated)
        hard = {k: v for k, v in violations.items()
                if k not in ("role_count", "layout_blocks_count", "layout_semantic_mismatches")}
        assert all(v == 0 for v in hard.values()), f"IR violations: {hard}"
        assert violations["role_count"] == 3

    def test_ir_has_anchored_summary(self, monkeypatch):
        """Updated IR has summary section anchored to para_6/para_7."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Summary section missing"
        assert summary.heading.para_id == "para_6"
        assert summary.body_paras and summary.body_paras[0].para_id == "para_7"
        # Full summary text preserved — no budget truncation for summary body anchor
        assert len(summary.body_paras[0].text) > 0
        assert "..." not in summary.body_paras[0].text, "Summary must not be truncated"

    def test_ir_section_order_preserved(self, monkeypatch):
        """Sections appear in original document order (summary inserted first)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)

        types = [s.semantic_type for s in updated.sections]
        assert types[0] == "summary"
        assert "education" in types
        assert "experience" in types
        assert "skills" in types
        idx_edu = types.index("education")
        idx_exp = types.index("experience")
        idx_skills = types.index("skills")
        assert idx_edu < idx_exp, "education must precede experience"
        assert idx_skills > idx_exp, "skills must follow experience"

    def test_ir_no_duplicate_para_ids(self, monkeypatch):
        """No para_id appears more than once in all_paras after budget enforcement."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from collections import Counter
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)

        pids = [p.para_id for p in (updated.all_paras or []) if p.para_id]
        dups = {k: v for k, v in Counter(pids).items() if v > 1}
        assert not dups, f"Duplicate para_ids: {dups}"

    def test_ir_no_current_date_leakage(self, monkeypatch):
        """'Current Date:' is stripped and does not appear in IR content."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)

        ir_json = json.dumps(updated.to_dict(), ensure_ascii=False)
        assert "current date" not in ir_json.lower(), "'Current Date' leaked into IR"

    def test_docx_renders_without_error(self, monkeypatch, tmp_path):
        """DOCX renders using the layout_blocks path without raising."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)

        out = str(tmp_path / "sample31_test.docx")
        render_docx(updated, _DOCX_PATH, out)
        assert Path(out).exists()
        assert Path(out).stat().st_size > 1000


# ---------------------------------------------------------------------------
# Preprocess tests (gen 209 specific)
# ---------------------------------------------------------------------------

class TestPreprocessGen209:
    def test_preprocess_strips_current_date_gen209(self):
        """preprocess_resume_text strips the 'Current Date: April 28, 2026' line."""
        from tailor.compiler.text_parser import preprocess_resume_text

        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        raw = gen["llm_response"]["resume"]
        assert "Current Date:" in raw, "test setup: raw text should have Current Date"

        processed = preprocess_resume_text(raw)
        assert "Current Date" not in processed

    def test_preprocess_merges_roles_gen209(self):
        """Role title+company lines are merged into pipe format."""
        from tailor.compiler.text_parser import preprocess_resume_text

        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        processed = preprocess_resume_text(gen["llm_response"]["resume"])

        assert "Web Developer | Liceria & Co." in processed
        assert "Web Designer | Borcelle Company" in processed
        assert "Web Development Intern | Fauget" in processed
