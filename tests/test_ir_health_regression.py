"""Regression tests for IR health reports on real DOCX samples.

These tests use the IR health check to verify that both known problem samples
(Sample 31 / Gabriel Mitchell and Sample 22 / Lydia Mary) satisfy all hard
structural invariants after apply_tailored.

Run with both USE_LAYOUT_BOUND_UPDATER=True (layout-bound mode) and False
(production default) to ensure the section_id preservation and finalize_layout_bound_ir
fixes hold in both paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")
_SAMPLE_22 = str(_DOCX_DIR / "22-Software-Engineer-Editable-Resume-Template-Download-in-docx-4.docx")

# Import the health check script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ir_health_check import ir_health_report  # noqa: E402


def _assert_passes(violations: dict, sample_name: str, mode: str) -> None:
    """Assert all hard invariants are zero."""
    failures = {k: v for k, v in violations.items() if v}
    assert not failures, (
        f"{sample_name} ({mode}) has {len(failures)} hard invariant violations:\n"
        + "\n".join(f"  {k}: {v}" for k, v in failures.items())
    )


class TestSample31Regression:
    """Gabriel Mitchell / Sample 31 — newspaper-column multi-column template."""

    def test_layout_bound_mode(self):
        """All hard invariants pass in layout-bound mode (flag=True)."""
        v = ir_health_report(_SAMPLE_31, layout_bound=True, verbose=False)
        _assert_passes(v, "Sample 31", "layout_bound=ON")

    def test_production_default_mode(self):
        """All hard invariants pass in production default mode (flag=False)."""
        v = ir_health_report(_SAMPLE_31, layout_bound=False, verbose=False)
        _assert_passes(v, "Sample 31", "layout_bound=OFF")

    def test_role_count_is_three(self, monkeypatch):
        """WORK EXPERIENCE must have exactly 3 roles."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        from ir_health_check import _build_synthetic_llm_sections

        doc = parse_docx(_SAMPLE_31)
        llm = _build_synthetic_llm_sections(doc)
        updated = apply_tailored(doc, llm)

        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "no experience section"
        assert len(exp.roles) == 3, f"expected 3 roles, got {len(exp.roles)}"

    def test_no_newline_in_bullets(self, monkeypatch):
        """No bullet paragraph contains a newline character."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        from ir_health_check import _build_synthetic_llm_sections

        doc = parse_docx(_SAMPLE_31)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))

        for sec in updated.sections:
            for role in sec.roles:
                for b in role.bullets:
                    assert "\n" not in b.text, f"newline in bullet: {b.text[:60]!r}"

    def test_all_sections_have_section_id(self, monkeypatch):
        """Every updated section must have a non-empty section_id."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        from ir_health_check import _build_synthetic_llm_sections

        doc = parse_docx(_SAMPLE_31)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))

        for sec in updated.sections:
            has_content = (
                any(p.text.strip() for p in sec.body_paras)
                or any(r.header.text.strip() for r in sec.roles)
            )
            if has_content:
                assert sec.section_id, (
                    f"section {sec.title!r} has content but no section_id"
                )


class TestSample22Regression:
    """Lydia Mary / Sample 22 — date-first experience layout."""

    def test_layout_bound_mode(self):
        """All hard invariants pass in layout-bound mode (flag=True)."""
        v = ir_health_report(_SAMPLE_22, layout_bound=True, verbose=False)
        _assert_passes(v, "Sample 22", "layout_bound=ON")

    def test_production_default_mode(self):
        """All hard invariants pass in production default mode (flag=False)."""
        v = ir_health_report(_SAMPLE_22, layout_bound=False, verbose=False)
        _assert_passes(v, "Sample 22", "layout_bound=OFF")

    def test_experience_content_present_and_anchored(self, monkeypatch):
        """Experience body paragraphs are all anchored (para_id non-empty)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        from ir_health_check import _build_synthetic_llm_sections

        doc = parse_docx(_SAMPLE_22)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))

        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "no experience section"

        # All non-empty body paragraphs must have para_id
        unbound = [p for p in exp.body_paras if p.text.strip() and not p.para_id]
        assert len(unbound) == 0, (
            f"experience has {len(unbound)} unbound non-empty body paras"
        )

    def test_no_density_overflow(self, monkeypatch):
        """No paragraph has density overflow after identity transform."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored, validate_layout_density
        from ir_health_check import _build_synthetic_llm_sections

        doc = parse_docx(_SAMPLE_22)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))

        density = validate_layout_density(doc, updated)
        assert density["multi_bullet_packing_count"] == 0
