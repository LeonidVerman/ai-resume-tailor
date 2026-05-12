"""Regression tests for tools/debug/inject_llm_into_ir.py.

Verifies that deterministic LLM injection into sample 31 produces a clean
layout-bound IR with all hard invariants satisfied and exactly 3 experience roles.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TOOLS = str(_ROOT / "tools" / "debug")
_SCRIPTS = str(_ROOT / "scripts")
_GEN_JSON = str(
    _ROOT / "tests" / "samples" / "generation"
    / "Gabriel_Mitchell-American_Tire_Distributors-Lead_Software_Engineer-201-20260427-170826.json"
)
_OUTPUT = _ROOT / "tmp" / "artefacts" / "ir" / "sample31_final_ir.json"

sys.path.insert(0, _TOOLS)
sys.path.insert(0, _SCRIPTS)


@pytest.mark.skipif(
    not Path(_GEN_JSON).exists(),
    reason="generation JSON not present",
)
class TestInjectLlmIntoIr:
    def test_all_hard_invariants_pass(self, monkeypatch):
        """inject_llm_into_ir.run() passes all hard layout-bound invariants."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run

        violations = run(_GEN_JSON, verbose=False)
        # A (non_empty_unbound_semantic_paras) and E (role_bullets_unbound) are
        # intentionally info-only: unbound paras are overflow-reflow content.
        _INFO = frozenset({
            "role_count", "layout_blocks_count", "layout_semantic_mismatches",
            "non_empty_unbound_semantic_paras", "role_bullets_unbound",
        })
        hard = {k: v for k, v in violations.items() if k not in _INFO}
        assert all(v == 0 for v in hard.values()), (
            f"Hard invariant violations: {hard}"
        )

    def test_exactly_three_experience_roles(self, monkeypatch):
        """Injection produces exactly 3 experience roles (cardinality invariant)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run
        violations = run(_GEN_JSON, verbose=False)
        assert violations["role_count"] == 3, (
            f"Expected 3 experience roles, got {violations['role_count']}"
        )

    def test_output_file_written(self, monkeypatch):
        """Injection writes output IR JSON that deserializes cleanly."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run
        run(_GEN_JSON, verbose=False)

        assert _OUTPUT.exists(), f"Output not written: {_OUTPUT}"
        with open(_OUTPUT, encoding="utf-8") as f:
            data = json.load(f)
        assert "sections" in data
        assert "layout_blocks" in data

    def test_output_survives_deserialization(self, monkeypatch):
        """Output IR round-trips through from_dict without violations."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run
        from tailor.compiler.models import ResumeDocument
        from check_layout_bound_ir_health import check_layout_bound_ir_health

        run(_GEN_JSON, verbose=False)
        with open(_OUTPUT, encoding="utf-8") as f:
            data = json.load(f)
        doc = ResumeDocument.from_dict(data)
        violations = check_layout_bound_ir_health(doc)
        # A and E are intentionally info-only (overflow reflow content)
        _INFO = frozenset({
            "role_count", "layout_blocks_count", "layout_semantic_mismatches",
            "non_empty_unbound_semantic_paras", "role_bullets_unbound",
        })
        hard = {k: v for k, v in violations.items() if k not in _INFO}
        assert all(v == 0 for v in hard.values()), (
            f"Post-deserialization violations: {hard}"
        )

    def test_no_current_date_in_output(self, monkeypatch):
        """'Current Date:' is stripped from LLM text before injection."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run
        run(_GEN_JSON, verbose=False)

        with open(_OUTPUT, encoding="utf-8") as f:
            raw = f.read()
        assert "current date" not in raw.lower(), (
            "'Current Date' text leaked into output IR"
        )

    def test_role_headers_bound_to_original_para_ids(self, monkeypatch):
        """Experience role headers use the original para_ids (para_37/41/45)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from inject_llm_into_ir import run
        from tailor.compiler.models import ResumeDocument
        run(_GEN_JSON, verbose=False)

        with open(_OUTPUT, encoding="utf-8") as f:
            data = json.load(f)
        doc = ResumeDocument.from_dict(data)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "No experience section"
        assert len(exp.roles) == 3
        header_pids = [r.header.para_id for r in exp.roles]
        assert "para_37" in header_pids, f"para_37 missing from role headers: {header_pids}"
        assert "para_41" in header_pids, f"para_41 missing from role headers: {header_pids}"
        assert "para_45" in header_pids, f"para_45 missing from role headers: {header_pids}"

    def test_preprocess_merges_title_and_company(self):
        """_preprocess_llm_text merges 'title\\ncompany | date' into pipe-format header."""
        from inject_llm_into_ir import _preprocess_llm_text

        text = "EXPERIENCE\nWeb Developer\nLiceria & Co. | 2019 - Present\n- bullet\n"
        result = _preprocess_llm_text(text)
        assert "Web Developer | Liceria & Co. | 2019 - Present" in result
        assert "\nWeb Developer\n" not in result

    def test_preprocess_strips_current_date(self):
        """_preprocess_llm_text removes 'Current Date: ...' lines."""
        from inject_llm_into_ir import _preprocess_llm_text

        text = "SKILLS\nPython\nCurrent Date: April 27, 2026\n"
        result = _preprocess_llm_text(text)
        assert "Current Date" not in result
        assert "Python" in result
