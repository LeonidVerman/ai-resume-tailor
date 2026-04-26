"""Tests for structural correctness invariants in apply_tailored.

Covers the 7 core invariants:
1. Role count preservation (len(updated.roles) == len(original.roles))
2. Role split repair (Experience + fake-section → 2 roles in Experience)
3. Role-in-bullets extraction (bullet "Web Dev Intern" → new role)
4. No unbound paragraphs (para_id != "" for all non-empty paras in layout-bound mode)
5. Layout ↔ semantic consistency (layout_blocks para_ids present in semantic model)
6. validate_structural_integrity reports violations correctly
7. normalize_llm_sections applies both repair passes
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SIMPLE_TEMPLATE = str(_DOCX_DIR / "1-Leonid_Verman_Resume_Template.docx")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_layout_doc(
    n_roles: int,
    n_bullets_per_role: int = 2,
) -> "ResumeDocument":
    """Build a minimal experience ResumeDocument with layout_blocks and N roles."""
    from tailor.compiler.models import (
        LayoutParagraphBlock,
        LayoutProfile,
        ParaModel,
        ParaStyle,
        ResumeDocument,
        ResumeSection,
        RoleEntry,
        assign_stable_ids,
    )
    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )

    def _para(text, semantic):
        return ParaModel(text=text, style=ParaStyle(), semantic=semantic)

    exp_heading = _para("Work Experience", "section_heading")
    roles = []
    for ri in range(n_roles):
        header = _para(f"Role {ri+1} | Company {ri+1}", "role_header")
        meta = _para(f"20{20+ri} – 20{21+ri}", "role_meta")
        bullets = [_para(f"Did work {ri+1}.{bi+1}", "bullet") for bi in range(n_bullets_per_role)]
        role = RoleEntry(
            header=header,
            meta_lines=[meta],
            bullets=bullets,
            role_id=f"Role {ri+1} | Company {ri+1}",
        )
        roles.append(role)

    exp_sec = ResumeSection(
        title="Work Experience",
        heading=exp_heading,
        semantic_type="experience",
        roles=roles,
    )
    exp_sec.section_id = "sec_exp"
    doc = ResumeDocument(
        header_paras=[], sections=[exp_sec], layout=layout, all_paras=[],
    )
    assign_stable_ids(doc)
    doc.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in doc.all_paras if pm.para_id
    ]
    return doc


def _make_llm_experience(
    n_roles: int,
    n_bullets_per_role: int = 2,
    section_title: str = "Work Experience",
) -> "LlmSection":
    from tailor.compiler.text_parser import LlmRole, LlmSection
    roles = [
        LlmRole(
            header=f"Role {ri+1} | Company {ri+1}",
            bullets=[f"Updated work {ri+1}.{bi+1}" for bi in range(n_bullets_per_role)],
        )
        for ri in range(n_roles)
    ]
    return LlmSection(heading=section_title, semantic_type="experience", roles=roles)


# ---------------------------------------------------------------------------
# 1. Role count preservation (Invariant 3)
# ---------------------------------------------------------------------------

class TestRoleCountPreservation:
    def test_three_roles_in_three_roles_out(self, monkeypatch):
        """3 original roles + 3 LLM roles → 3 updated roles."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(3)
        llm_secs = [_make_llm_experience(3)]
        updated = apply_tailored(doc, llm_secs)

        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert len(exp.roles) == 3

    def test_fewer_llm_roles_preserves_original_count(self, monkeypatch):
        """3 original roles + 1 LLM role → 3 updated roles (Invariant 3)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(3)
        llm_secs = [_make_llm_experience(1)]  # only 1 LLM role
        updated = apply_tailored(doc, llm_secs)

        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert len(exp.roles) == 3, (
            f"expected 3 roles (original count), got {len(exp.roles)}"
        )
        # First role should be updated
        assert "Updated work 1.1" in exp.roles[0].bullets[0].text
        # Remaining original roles preserved verbatim
        assert exp.roles[1].header.text == "Role 2 | Company 2"
        assert exp.roles[2].header.text == "Role 3 | Company 3"

    def test_role_ids_stable_after_preservation(self, monkeypatch):
        """Preserved original roles must retain their role_id_stable."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(3)
        orig_stable_ids = [r.role_id_stable for r in doc.sections[0].roles]
        llm_secs = [_make_llm_experience(1)]
        updated = apply_tailored(doc, llm_secs)

        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        updated_stable_ids = [r.role_id_stable for r in exp.roles]
        # All IDs should be preserved (updated role also preserves its ID in layout_bound mode)
        assert updated_stable_ids[0] == orig_stable_ids[0]
        # Preserved original roles also keep their IDs
        assert updated_stable_ids[1] == orig_stable_ids[1]
        assert updated_stable_ids[2] == orig_stable_ids[2]

    def test_flag_off_drops_unmatched_roles(self, monkeypatch):
        """Without layout_bound, unmatched original roles ARE dropped (existing behavior)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)

        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(3)
        llm_secs = [_make_llm_experience(1)]
        updated = apply_tailored(doc, llm_secs)

        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        # Without flag, zip stops at min(1, 3) = 1
        assert len(exp.roles) == 1


# ---------------------------------------------------------------------------
# 2. Role split repair (Experience + fake section → 2 roles)
# ---------------------------------------------------------------------------

class TestRoleSplitRepair:
    def test_role_continuation_section_absorbed(self):
        """Experience followed by 'Web Designer' section → 2 roles in Experience."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import normalize_llm_sections

        exp_sec = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(header="Web Developer | Co. | 2021-Present", bullets=["Built things"])],
        )
        web_sec = LlmSection(
            heading="Web Designer", semantic_type="other",
            roles=[LlmRole(header="Agency | 2019-2021", bullets=["Designed things"])],
        )
        result = normalize_llm_sections([exp_sec, web_sec])

        assert len(result) == 1
        assert result[0].semantic_type == "experience"
        assert len(result[0].roles) == 2
        assert result[0].roles[1].header == "Web Designer"

    def test_repair_applies_before_section_matching(self, monkeypatch):
        """normalize_llm_sections runs before match in apply_tailored (layout_blocks present)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(2)
        # LLM outputs Experience with 1 role + fake "Web Designer" section
        exp_llm = LlmSection(
            heading="Work Experience", semantic_type="experience",
            roles=[LlmRole(header="Role 1 | Company 1", bullets=["Updated bullet"])],
        )
        fake_sec = LlmSection(
            heading="Web Designer", semantic_type="other",
            roles=[LlmRole(header="Agency | 2019", bullets=["Made websites"])],
        )
        updated = apply_tailored(doc, [exp_llm, fake_sec])

        # "Web Designer" must NOT appear as top-level section
        section_titles = [s.title for s in updated.sections]
        assert "Web Designer" not in section_titles
        # Experience must have 2 roles (original count preserved by Invariant 3)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert len(exp.roles) == 2


# ---------------------------------------------------------------------------
# 3. Role-in-bullets extraction
# ---------------------------------------------------------------------------

class TestRoleInBulletsExtraction:
    def test_pipe_separated_bullet_extracted_as_role(self):
        """Bullet containing 'Job | Company' is extracted as a new LlmRole."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import normalize_llm_sections

        exp_sec = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(
                header="Senior Dev | Corp",
                bullets=[
                    "Built backend services",
                    "Web Development Intern | OldCo",  # role header in bullets
                    "Maintained legacy systems",
                ],
            )],
        )
        result = normalize_llm_sections([exp_sec])

        exp = result[0]
        assert len(exp.roles) == 2
        # Original role retains its non-role bullets
        assert exp.roles[0].header == "Senior Dev | Corp"
        assert "Built backend services" in exp.roles[0].bullets
        assert "Web Development Intern | OldCo" not in exp.roles[0].bullets
        # New role extracted from the embedded role header
        assert exp.roles[1].header == "Web Development Intern | OldCo"
        assert "Maintained legacy systems" in exp.roles[1].bullets

    def test_non_role_bullet_not_extracted(self):
        """Normal achievement bullet must NOT be extracted as a role."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import normalize_llm_sections

        exp_sec = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(
                header="Dev | Corp",
                bullets=[
                    "Developed scalable microservices architecture",
                    "Led a team of 5 engineers",
                    "Reduced latency by 30%",
                ],
            )],
        )
        result = normalize_llm_sections([exp_sec])
        assert len(result[0].roles) == 1
        assert len(result[0].roles[0].bullets) == 3

    def test_job_title_bullet_detected(self):
        """Bullet 'Web Development Intern' (job-title words, short) → new role."""
        from tailor.compiler.updater import _bullet_looks_like_role_title
        assert _bullet_looks_like_role_title("Web Development Intern | Agency")
        assert _bullet_looks_like_role_title("Senior Software Engineer | Acme Corp")

    def test_achievement_bullet_not_flagged(self):
        """Achievement bullets must NOT trigger role extraction."""
        from tailor.compiler.updater import _bullet_looks_like_role_title
        assert not _bullet_looks_like_role_title("Developed backend APIs for 1M+ users")
        assert not _bullet_looks_like_role_title("Led migration to microservices architecture")
        assert not _bullet_looks_like_role_title("Reduced database query latency by 40%")
        assert not _bullet_looks_like_role_title("Collaborated with product team to define requirements")


# ---------------------------------------------------------------------------
# 4. No unbound paragraphs
# ---------------------------------------------------------------------------

class TestNoUnboundParagraphs:
    def test_no_unbound_paras_after_layout_bound_update(self, monkeypatch):
        """With USE_LAYOUT_BOUND_UPDATER=True, no non-empty paras should have para_id=''."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = _make_layout_doc(2, n_bullets_per_role=3)
        llm_secs = [_make_llm_experience(2, n_bullets_per_role=3)]
        updated = apply_tailored(doc, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0, (
            f"unexpected unbound non-empty paras: {metrics['unbound_non_empty_paras']}"
        )

    def test_no_unbound_after_bullet_drop(self, monkeypatch):
        """Extra LLM bullets are dropped, not cloned — zero unbound paras."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = _make_layout_doc(1, n_bullets_per_role=2)
        llm_secs = [_make_llm_experience(1, n_bullets_per_role=5)]  # 3 extra bullets
        updated = apply_tailored(doc, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0

    def test_unbound_count_with_real_template(self, monkeypatch):
        """With real template and layout_bound=True, unbound count is minimal."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience section")

        # Build LLM output that matches exactly
        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else '2020 - Present'}\n"
            + "\n".join(f"- Updated bullet {i}" for i in range(len(role.bullets)))
        )
        llm_secs = parse_llm_output(llm_text)
        if not llm_secs:
            pytest.skip("could not parse LLM output")

        updated = apply_tailored(doc, llm_secs)
        metrics = validate_layout_binding(updated)
        # With matching content, there should be zero unbound non-empty paras
        assert metrics["unbound_non_empty_paras"] == 0


# ---------------------------------------------------------------------------
# 5. Layout ↔ semantic consistency
# ---------------------------------------------------------------------------

class TestLayoutSemanticConsistency:
    def test_all_layout_para_ids_in_semantic_model(self, monkeypatch):
        """Every para_id referenced by layout_blocks must exist in updated all_paras."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.models import LayoutParagraphBlock
        from tailor.compiler.updater import apply_tailored, validate_structural_integrity

        doc = _make_layout_doc(2)
        llm_secs = [_make_llm_experience(2)]
        updated = apply_tailored(doc, llm_secs)

        # Collect all para_ids referenced in layout_blocks
        lb_ids = {
            block.para_id
            for block in (updated.layout_blocks or [])
            if isinstance(block, LayoutParagraphBlock) and block.para_id
        }
        semantic_ids = {pm.para_id for pm in (updated.all_paras or []) if pm.para_id}

        missing = lb_ids - semantic_ids
        assert not missing, (
            f"layout_blocks para_ids not in semantic model: {missing}"
        )

    def test_validate_structural_integrity_clean(self, monkeypatch):
        """validate_structural_integrity returns zero violations for a clean update."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_structural_integrity

        doc = _make_layout_doc(2)
        llm_secs = [_make_llm_experience(2)]
        updated = apply_tailored(doc, llm_secs)

        violations = validate_structural_integrity(doc, updated)
        assert violations["role_count_violations"] == 0
        assert violations["unbound_non_empty_paras"] == 0
        assert violations["layout_semantic_mismatches"] == 0

    def test_validate_structural_integrity_detects_role_collapse(self, monkeypatch):
        """validate_structural_integrity detects when role count drops."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)  # allow collapse

        from tailor.compiler.updater import apply_tailored, validate_structural_integrity

        doc = _make_layout_doc(3)
        # Only 1 LLM role with flag off → zip drops 2 original roles
        llm_secs = [_make_llm_experience(1)]
        updated = apply_tailored(doc, llm_secs)

        violations = validate_structural_integrity(doc, updated)
        assert violations["role_count_violations"] >= 1, (
            "expected role count violation when LLM has fewer roles and flag is off"
        )


# ---------------------------------------------------------------------------
# 6. validate_structural_integrity
# ---------------------------------------------------------------------------

class TestValidateStructuralIntegrity:
    def test_role_boundary_violation_detected(self):
        """Roles with bullet containing a pipe-format role header are flagged."""
        from tailor.compiler.models import (
            LayoutProfile, ParaModel, ParaStyle,
            ResumeDocument, ResumeSection, RoleEntry, assign_stable_ids,
        )
        from tailor.compiler.updater import validate_structural_integrity

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )

        def _p(t, s):
            pm = ParaModel(text=t, style=ParaStyle(), semantic=s)
            return pm

        bad_bullet = _p("Web Dev Intern | OldCo", "bullet")  # looks like role header
        good_bullet = _p("Built microservices", "bullet")

        role = RoleEntry(
            header=_p("Dev | Corp", "role_header"),
            bullets=[good_bullet, bad_bullet],
            role_id="Dev | Corp",
        )
        sec = ResumeSection(
            title="Experience",
            heading=_p("Experience", "section_heading"),
            semantic_type="experience",
            roles=[role],
        )
        sec.section_id = "sec_exp"
        doc = ResumeDocument(
            header_paras=[], sections=[sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(doc)

        violations = validate_structural_integrity(doc, doc)  # self-check
        assert violations["role_boundary_violations"] >= 1

    def test_unbound_para_counted(self):
        """Paragraphs with non-empty text and para_id='' are counted."""
        from tailor.compiler.models import (
            LayoutProfile, ParaModel, ParaStyle,
            ResumeDocument, ResumeSection, assign_stable_ids,
        )
        from tailor.compiler.updater import validate_structural_integrity

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        unbound_para = ParaModel(
            text="Some content", style=ParaStyle(), semantic="paragraph"
        )
        # leave para_id="" (default)
        sec = ResumeSection(
            title="Skills", heading=ParaModel(text="Skills", style=ParaStyle(), semantic="section_heading"),
            semantic_type="skills", body_paras=[unbound_para],
        )
        doc = ResumeDocument(
            header_paras=[], sections=[sec], layout=layout,
            # Include the para in all_paras so the check can find it
            all_paras=[sec.heading, unbound_para],
        )
        assign_stable_ids(doc)
        # Forcibly reset to unbound AFTER ID assignment to simulate a clone_as para
        unbound_para.para_id = ""

        violations = validate_structural_integrity(doc, doc)
        assert violations["unbound_non_empty_paras"] >= 1


# ---------------------------------------------------------------------------
# 7. normalize_llm_sections
# ---------------------------------------------------------------------------

class TestNormalizeLlmSections:
    def test_both_repair_passes_applied(self):
        """normalize_llm_sections applies both continuation and bullet repairs."""
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import normalize_llm_sections

        # Experience + continuation section + role embedded in bullets
        exp = LlmSection(
            heading="Experience", semantic_type="experience",
            roles=[LlmRole(
                header="Dev | Corp",
                bullets=["Built things", "Senior Analyst | Co2", "Did analysis"],
            )],
        )
        continuation = LlmSection(
            heading="Web Designer", semantic_type="other",
            roles=[LlmRole(header="Agency | 2019", bullets=["Made sites"])],
        )

        result = normalize_llm_sections([exp, continuation])

        # Continuation absorbed
        assert len(result) == 1
        assert result[0].semantic_type == "experience"
        # Bullet repair extracted "Senior Analyst | Co2" as a role
        assert len(result[0].roles) >= 2  # original + extracted + absorbed
        role_headers = [r.header for r in result[0].roles]
        assert "Web Designer" in role_headers
        assert "Senior Analyst | Co2" in role_headers

    def test_empty_sections_unchanged(self):
        """Empty section list passes through unchanged."""
        from tailor.compiler.updater import normalize_llm_sections
        assert normalize_llm_sections([]) == []
