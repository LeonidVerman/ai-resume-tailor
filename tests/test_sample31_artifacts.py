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
# Anchor budget unit tests
# ---------------------------------------------------------------------------

class TestAnchorBudgets:
    def test_budgets_cover_experience_slots(self):
        """All 3 experience bullet slots have defined budgets."""
        from generate_sample31_artifacts import _ANCHOR_BUDGETS
        assert "para_39" in _ANCHOR_BUDGETS
        assert "para_43" in _ANCHOR_BUDGETS
        assert "para_47" in _ANCHOR_BUDGETS

    def test_summary_slots_have_budgets(self):
        """Summary anchor slots para_7/para_8 have budgets."""
        from generate_sample31_artifacts import _ANCHOR_BUDGETS
        assert "para_7" in _ANCHOR_BUDGETS
        assert "para_8" in _ANCHOR_BUDGETS
        assert _ANCHOR_BUDGETS["para_8"] >= 100
        assert _ANCHOR_BUDGETS["para_7"] >= 20

    def test_enforce_anchor_budgets_truncates(self, monkeypatch):
        """_enforce_anchor_budgets truncates paragraphs that exceed budget."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from generate_sample31_artifacts import _ANCHOR_BUDGETS, _enforce_anchor_budgets
        from tailor.compiler.models import (
            ParaModel, ParaStyle, ResumeDocument, ResumeSection,
            LayoutProfile, assign_stable_ids,
        )

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        # Create a para with para_id matching a budget entry and text over budget
        pm = ParaModel(text="x" * 500, style=ParaStyle(), semantic="paragraph")
        pm.para_id = "para_8"  # budget = 200

        sec = ResumeSection(
            title="Summary", heading=ParaModel(text="", style=ParaStyle(), semantic="section_heading"),
            semantic_type="summary", body_paras=[pm],
        )
        sec.section_id = "sec_summ"
        sec.heading.para_id = "hd_summ"

        doc = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        result = _enforce_anchor_budgets(doc)

        summ = next(s for s in result.sections if s.semantic_type == "summary")
        assert len(summ.body_paras[0].text) <= _ANCHOR_BUDGETS["para_8"] + 3  # allow for "..."
        assert summ.body_paras[0].para_id == "para_8"  # para_id preserved

    def test_enforce_anchor_budgets_no_truncation_within_budget(self, monkeypatch):
        """Paragraphs within budget are not modified."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from generate_sample31_artifacts import _enforce_anchor_budgets
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

        doc = ResumeDocument(header_paras=[], sections=[sec], layout=layout, all_paras=[])
        result = _enforce_anchor_budgets(doc)

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
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_text = gen["llm_response"]["resume"]
        llm_sections = parse_llm_output(llm_text)
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

        violations = check_layout_bound_ir_health(updated)
        hard = {k: v for k, v in violations.items()
                if k not in ("role_count", "layout_blocks_count", "layout_semantic_mismatches")}
        assert all(v == 0 for v in hard.values()), f"IR violations: {hard}"
        assert violations["role_count"] == 3

    def test_ir_has_anchored_summary(self, monkeypatch):
        """Updated IR has summary section anchored to para_7/para_8."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Summary section missing"
        assert summary.heading.para_id == "para_7"
        assert summary.body_paras and summary.body_paras[0].para_id == "para_8"
        assert len(summary.body_paras[0].text) <= 200  # budget enforced

    def test_ir_section_order_preserved(self, monkeypatch):
        """Sections appear in original document order (summary inserted first)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

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
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

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
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

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
        from generate_sample31_artifacts import _enforce_anchor_budgets

        doc = parse_docx(_DOCX_PATH)
        with open(_GEN_JSON_209, encoding="utf-8") as f:
            gen = json.load(f)
        llm_sections = parse_llm_output(gen["llm_response"]["resume"])
        updated = apply_tailored(doc, llm_sections)
        updated = _enforce_anchor_budgets(updated)

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
