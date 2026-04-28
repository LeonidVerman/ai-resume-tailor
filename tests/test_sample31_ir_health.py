"""Sample 31 layout-bound IR health regression tests.

These tests run the full apply_tailored path on sample 31 with
USE_LAYOUT_BOUND_UPDATER=True and verify all hard layout-bound invariants.

Each test maps to a specific Definition of Done from the spec:
  DoD #1 — no synthetic/unanchored sections (section_id="")
  DoD #2 — no non-empty semantic para_id=""
  DoD #3 — no layout_blocks with empty para_id
  DoD #4 — experience not split-brain (roles canonical, body_paras clean)

Also includes:
  - Role meta cross-contamination checks
  - Skills section purity
  - Lydia/sample 22 non-regression
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")
_SAMPLE_22 = str(_DOCX_DIR / "22-Software-Engineer-Editable-Resume-Template-Download-in-docx-4.docx")

# Import health checker
_SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")
sys.path.insert(0, _SCRIPTS)
from check_layout_bound_ir_health import check_layout_bound_ir_health, assert_layout_bound_clean  # noqa: E402


def _apply_realistic_llm(sample_path: str) -> "ResumeDocument":
    """Parse a sample, apply a realistic LLM output, return updated IR."""
    import tailor.config as cfg
    import tailor.compiler.docx_parser as _dp
    import tailor.compiler.updater as _up
    from tailor.compiler.text_parser import LlmSection, LlmRole

    cfg.USE_LAYOUT_BOUND_UPDATER = True

    doc = _dp.parse_docx(sample_path)
    if sample_path.endswith("31-Software-Engineer"):
        # Realistic LLM output for sample 31 — includes Professional Summary
        # (not in original) and extra bullets beyond template slots
        llm = [
            LlmSection("Professional Summary", "summary",
                       body_lines=["Experienced web developer with 5+ years."]),
            LlmSection("WORK EXPERIENCE", "experience", roles=[
                LlmRole("Web Developer | Liceria & Co.",
                        meta_lines=["2019 - Present"],
                        bullets=["Built responsive websites using HTML/CSS/JS.",
                                 "Extra bullet 1", "Extra bullet 2"]),
                LlmRole("Web Designer | Borcelle Company",
                        meta_lines=["2016-2018"],
                        bullets=["Designed UI/UX for 50+ client projects."]),
                LlmRole("Web Development Intern | Fauget",
                        meta_lines=["2014-2015"],
                        bullets=["Maintained company websites and internal tools."]),
            ]),
            LlmSection("SKILLS", "skills",
                       body_lines=["HTML, CSS, JavaScript", "React, Vue.js",
                                   "Adobe Photoshop", "Figma", "Git, GitHub"]),
        ]
    else:
        # Identity transform for other samples
        from scripts.ir_health_check import _build_synthetic_llm_sections  # noqa: E402
        llm = _build_synthetic_llm_sections(doc)

    return _up.apply_tailored(doc, llm)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def updated_31(monkeypatch_module):
    return _apply_realistic_llm(_SAMPLE_31)


@pytest.fixture(scope="module")
def updated_22(monkeypatch_module):
    import tailor.config as cfg
    cfg.USE_LAYOUT_BOUND_UPDATER = True
    from scripts.ir_health_check import _build_synthetic_llm_sections  # noqa: E402
    import tailor.compiler.docx_parser as _dp
    import tailor.compiler.updater as _up
    doc = _dp.parse_docx(_SAMPLE_22)
    llm = _build_synthetic_llm_sections(doc)
    return _up.apply_tailored(doc, llm)


@pytest.fixture(scope="module")
def monkeypatch_module():
    """Module-scoped monkeypatch for flag settings."""
    import tailor.config as cfg
    orig_lb = cfg.USE_LAYOUT_BOUND_UPDATER
    cfg.USE_LAYOUT_BOUND_UPDATER = True
    yield
    cfg.USE_LAYOUT_BOUND_UPDATER = orig_lb


# ---------------------------------------------------------------------------
# Sample 31: core DoD assertions
# ---------------------------------------------------------------------------

class TestSample31DefinitionOfDone:
    """Verifies all 4 Definitions of Done for sample 31."""

    def test_dod1_no_synthetic_sections(self, monkeypatch):
        """DoD #1: No section_id='' with non-empty content."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)

        synth = [
            s for s in updated.sections
            if not s.section_id and (
                any(p.text.strip() for p in s.body_paras)
                or any(r.header.text.strip() for r in s.roles)
            )
        ]
        assert synth == [], (
            f"Synthetic sections found: {[s.title for s in synth]}\n"
            "Professional Summary should be dropped — no original anchor in sample 31."
        )

    def test_dod2_no_unbound_semantic_paras(self, monkeypatch):
        """DoD #2: No non-empty ParaModel with para_id=''."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)

        def _collect(d):
            p = list(d.header_paras)
            for s in d.sections:
                p.append(s.heading)
                for r in s.roles:
                    p.append(r.header); p.extend(r.header_extra)
                    p.extend(r.meta_lines); p.extend(r.bullets)
                p.extend(s.body_paras)
            return p

        unbound = [pm for pm in _collect(updated) if not pm.para_id and pm.text.strip()]
        assert unbound == [], (
            f"{len(unbound)} non-empty para_id='' paragraphs:\n"
            + "\n".join(f"  [{pm.semantic}] {pm.text[:60]!r}" for pm in unbound[:5])
        )

    def test_dod3_no_unbound_layout_blocks(self, monkeypatch):
        """DoD #3: No LayoutParagraphBlock with para_id=''."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)

        from tailor.compiler.models import LayoutParagraphBlock
        lb_empty = [
            b for b in (updated.layout_blocks or [])
            if isinstance(b, LayoutParagraphBlock) and not b.para_id
        ]
        assert lb_empty == [], (
            f"{len(lb_empty)} LayoutParagraphBlock(s) with empty para_id"
        )

    def test_dod4_experience_not_split_brain(self, monkeypatch):
        """DoD #4: Experience section uses roles as canonical; body_paras has no role content."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)

        _ROLE_SEMS = frozenset({"role_header", "role_meta", "bullet"})
        for sec in updated.sections:
            if sec.semantic_type == "experience" and sec.roles:
                role_like = [
                    p for p in sec.body_paras
                    if p.text.strip() and p.semantic in _ROLE_SEMS
                ]
                assert not role_like, (
                    f"Split-brain: section {sec.title!r} has {len(role_like)} role-like "
                    f"paragraphs in body_paras while roles={len(sec.roles)}"
                )

    def test_dod_complete_assertion(self, monkeypatch):
        """All DoD checks pass via assert_layout_bound_clean."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        assert_layout_bound_clean(updated, label="Sample 31", expected_experience_roles=3)


# ---------------------------------------------------------------------------
# Sample 31: additional invariant checks
# ---------------------------------------------------------------------------

class TestSample31AdditionalInvariants:
    def test_layout_blocks_present(self, monkeypatch):
        """layout_blocks must be non-empty after update."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        assert updated.layout_blocks is not None
        assert len(updated.layout_blocks) > 0

    def test_experience_has_exactly_three_roles(self, monkeypatch):
        """Sample 31 WORK EXPERIENCE must have exactly 3 roles."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "no experience section"
        assert len(exp.roles) == 3, f"expected 3 roles, got {len(exp.roles)}"

    def test_role_bullets_all_bound(self, monkeypatch):
        """All role bullets must have non-empty para_id."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        for sec in updated.sections:
            for role in sec.roles:
                for b in role.bullets:
                    assert b.para_id, (
                        f"Role {role.header.text[:30]!r} has bullet with para_id='': {b.text[:50]!r}"
                    )

    def test_no_cross_role_meta_contamination(self, monkeypatch):
        """Web Development Intern meta must not appear in Web Designer role."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        if exp is None or len(exp.roles) < 2:
            pytest.skip("no experience or < 2 roles")
        designer_role = next(
            (r for r in exp.roles if "Designer" in r.header.text or "designer" in r.header.text.lower()),
            None
        )
        if designer_role:
            meta_texts = " ".join(m.text for m in designer_role.meta_lines)
            assert "Fauget" not in meta_texts, (
                f"Fauget meta leaked into Web Designer role: {meta_texts!r}"
            )

    def test_no_role_titles_inside_bullets(self, monkeypatch):
        """No bullet should contain a role title (role boundary violation)."""
        import tailor.config as cfg
        from tailor.compiler.updater import _bullet_looks_like_role_title
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        for sec in updated.sections:
            for role in sec.roles:
                for b in role.bullets:
                    assert not _bullet_looks_like_role_title(b.text), (
                        f"Role boundary violation: bullet {b.text[:60]!r} looks like role title"
                    )

    def test_all_sections_have_section_id(self, monkeypatch):
        """Every section with content must have a non-empty section_id."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        for sec in updated.sections:
            has_content = (
                any(p.text.strip() for p in sec.body_paras)
                or any(r.header.text.strip() for r in sec.roles)
            )
            if has_content:
                assert sec.section_id, f"Section {sec.title!r} has content but no section_id"

    def test_orphan_layout_blocks_have_stable_id(self, monkeypatch):
        """All layout_blocks must have a non-empty para_id (orphans get lb_orphan_ prefix)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import LayoutParagraphBlock
        updated = _apply_realistic_llm(_SAMPLE_31)
        for block in (updated.layout_blocks or []):
            if isinstance(block, LayoutParagraphBlock):
                assert block.para_id, (
                    f"LayoutParagraphBlock with empty para_id found (xml={block.xml_proto_xml[:80]!r})"
                )

    def test_no_date_in_resume_sections(self, monkeypatch):
        """No 'Current Date:' text should appear in resume sections."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        updated = _apply_realistic_llm(_SAMPLE_31)
        for sec in updated.sections:
            for p in sec.body_paras:
                assert "current date" not in p.text.lower(), (
                    f"'Current Date' found in section {sec.title!r}: {p.text[:60]!r}"
                )


# ---------------------------------------------------------------------------
# Sample 22 (Lydia) non-regression
# ---------------------------------------------------------------------------

class TestSample22NonRegression:
    def test_no_unbound_semantic_paras(self, monkeypatch):
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        sys.path.insert(0, _SCRIPTS)
        from ir_health_check import _build_synthetic_llm_sections  # noqa: E402

        doc = parse_docx(_SAMPLE_22)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))

        def _collect(d):
            p = list(d.header_paras)
            for s in d.sections:
                p.append(s.heading)
                for r in s.roles:
                    p.append(r.header); p.extend(r.header_extra)
                    p.extend(r.meta_lines); p.extend(r.bullets)
                p.extend(s.body_paras)
            return p

        unbound = [pm for pm in _collect(updated) if not pm.para_id and pm.text.strip()]
        assert unbound == []

    def test_layout_blocks_present(self, monkeypatch):
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        sys.path.insert(0, _SCRIPTS)
        from ir_health_check import _build_synthetic_llm_sections  # noqa: E402

        doc = parse_docx(_SAMPLE_22)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))
        assert updated.layout_blocks is not None
        assert len(updated.layout_blocks) > 0

    def test_experience_content_anchored(self, monkeypatch):
        """Experience body paragraphs must be anchored."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.updater import apply_tailored
        sys.path.insert(0, _SCRIPTS)
        from ir_health_check import _build_synthetic_llm_sections  # noqa: E402

        doc = parse_docx(_SAMPLE_22)
        updated = apply_tailored(doc, _build_synthetic_llm_sections(doc))
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        if exp is None:
            pytest.skip("no experience section")
        unbound = [p for p in exp.body_paras if p.text.strip() and not p.para_id]
        assert not unbound, f"{len(unbound)} unbound experience body paras"


# ---------------------------------------------------------------------------
# Health check on serialized IR (simulating debug JSON path)
# ---------------------------------------------------------------------------

class TestSerializedIRHealthCheck:
    def test_sample31_survives_serialization(self, monkeypatch):
        """Updated IR must still pass health checks after to_dict/from_dict."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)
        from tailor.compiler.models import ResumeDocument
        updated = _apply_realistic_llm(_SAMPLE_31)
        d = updated.to_dict()
        deserialized = ResumeDocument.from_dict(d)

        violations = check_layout_bound_ir_health(deserialized)
        hard = {k: v for k, v in violations.items()
                if k not in ("role_count", "layout_blocks_count", "layout_semantic_mismatches")}
        assert all(v == 0 for v in hard.values()), (
            f"Post-serialization violations: {hard}"
        )
