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


def _make_minimal_section(
    title: str,
    semantic_type: str,
    body_texts: list[str],
    section_id: str = "sec_1",
) -> "ResumeSection":
    from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
    heading = ParaModel(text=title, style=ParaStyle(), semantic="section_heading")
    heading.para_id = f"para_h_{section_id}"
    body = []
    for i, t in enumerate(body_texts):
        pm = ParaModel(text=t, style=ParaStyle(), semantic="paragraph")
        pm.para_id = f"para_b_{section_id}_{i}"
        body.append(pm)
    sec = ResumeSection(
        title=title, heading=heading, semantic_type=semantic_type, body_paras=body,
    )
    sec.section_id = section_id
    return sec


def _make_llm_section(heading: str, body_lines: list[str], semantic_type: str = "summary"):
    from tailor.compiler.text_parser import LlmSection
    return LlmSection(heading=heading, semantic_type=semantic_type, body_lines=body_lines)


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


# ---------------------------------------------------------------------------
# 8. Bullet overflow — drop-not-pack (Priority 2)
# ---------------------------------------------------------------------------

class TestBulletOverflow:
    def test_single_orig_bullet_four_llm_bullets_all_packed(self, monkeypatch):
        """1 original bullet + 4 LLM bullets → all 4 bullets packed into the 1 slot."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = _make_layout_doc(1, n_bullets_per_role=1)
        llm_secs = [_make_llm_experience(1, n_bullets_per_role=4)]
        updated = apply_tailored(doc, llm_secs)

        role = updated.sections[0].roles[0]
        assert len(role.bullets) == 1, f"expected 1 bullet slot, got {len(role.bullets)}"
        assert role.bullets[0].para_id != "", "bullet must keep original para_id"
        # First LLM bullet is always present
        assert "Updated work 1.1" in role.bullets[0].text
        # All 4 LLM bullets packed — no content dropped
        for i in range(1, 5):
            assert f"Updated work 1.{i}" in role.bullets[0].text, (
                f"LLM bullet 1.{i} missing from packed slot"
            )
        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0

    def test_two_orig_bullets_five_llm_bullets_packed(self, monkeypatch):
        """2 original bullets + 5 LLM bullets → slots 1-2 filled, extras packed into slot 2."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = _make_layout_doc(1, n_bullets_per_role=2)
        llm_secs = [_make_llm_experience(1, n_bullets_per_role=5)]
        updated = apply_tailored(doc, llm_secs)

        role = updated.sections[0].roles[0]
        assert len(role.bullets) == 2, f"expected 2 bullet slots, got {len(role.bullets)}"
        for b in role.bullets:
            assert b.para_id != ""
        assert "Updated work 1.1" in role.bullets[0].text
        # Slot 2 contains bullets 2-5 packed — all present
        combined = role.bullets[0].text + " " + role.bullets[1].text
        for i in range(1, 6):
            assert f"Updated work 1.{i}" in combined, f"LLM bullet 1.{i} missing"

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0

    def test_extra_bullet_packed_into_last_slot(self, monkeypatch):
        """Extra LLM bullets are packed into the last slot instead of being dropped."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import _update_role
        from tailor.compiler.models import ParaModel, ParaStyle, RoleEntry
        from tailor.compiler.text_parser import LlmRole

        orig_bullet = ParaModel(text="Short.", style=ParaStyle(), semantic="bullet")
        orig_bullet.para_id = "para_b1"
        orig_role = RoleEntry(
            header=ParaModel(text="Dev | Corp", style=ParaStyle(), semantic="role_header"),
            bullets=[orig_bullet],
            role_id="Dev | Corp",
        )
        orig_role.role_id_stable = "role_1"
        orig_role.header.para_id = "para_h1"

        llm_role = LlmRole(header="Dev | Corp", bullets=["Short.", "extra"])
        updated_role = _update_role(orig_role, llm_role, layout_bound=True)

        # Still 1 slot
        assert len(updated_role.bullets) == 1
        # Extra is packed — nothing dropped
        assert "Short." in updated_role.bullets[0].text
        assert "extra" in updated_role.bullets[0].text

    def test_fewer_llm_bullets_than_orig(self, monkeypatch):
        """Fewer LLM bullets than original → only matched bullets, no extras."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _make_layout_doc(1, n_bullets_per_role=3)
        llm_secs = [_make_llm_experience(1, n_bullets_per_role=2)]
        updated = apply_tailored(doc, llm_secs)

        role = updated.sections[0].roles[0]
        assert len(role.bullets) == 2
        assert "Updated work 1.1" in role.bullets[0].text
        assert "Updated work 1.2" in role.bullets[1].text


# ---------------------------------------------------------------------------
# 9. Summary skipped when no anchor / mapped when anchor exists
# ---------------------------------------------------------------------------

class TestSummaryHandling:
    def test_summary_skipped_when_no_original_anchor(self, monkeypatch):
        """LLM summary with no matching original section → not inserted in layout-bound mode."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        # Template has only skills, no summary
        doc = _make_layout_doc(1)
        llm_secs = [
            _make_llm_section("Professional Summary", ["I am a developer."], "summary"),
            _make_llm_experience(1),
        ]
        from tailor.compiler.text_parser import LlmSection
        updated = apply_tailored(doc, llm_secs)

        section_types = [s.semantic_type for s in updated.sections]
        assert "summary" not in section_types, "Unmatched summary must not be inserted"

    def test_summary_mapped_to_existing_about_section(self, monkeypatch):
        """LLM 'Professional Summary' maps to original 'About Me' section (same semantic type)."""
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
        about_sec = _make_minimal_section("About Me", "summary", ["Original about text."])
        exp_sec = _make_layout_doc(1).sections[0]
        orig = ResumeDocument(
            header_paras=[], sections=[about_sec, exp_sec], layout=layout, all_paras=[],
        )
        assign_stable_ids(orig)  # assigns about_sec.section_id = "sec_1", etc.
        orig_about_section_id = about_sec.section_id
        orig_about_heading_para_id = about_sec.heading.para_id
        orig.layout_blocks = [
            LayoutParagraphBlock(para_id=pm.para_id)
            for pm in orig.all_paras if pm.para_id
        ]

        # LLM uses "Professional Summary" but the original has "About Me" (same semantic_type=summary)
        llm_secs = [
            _make_llm_section("Professional Summary", ["Updated summary content."], "summary"),
            _make_llm_experience(1),
        ]
        updated = apply_tailored(orig, llm_secs)

        summary_secs = [s for s in updated.sections if s.semantic_type == "summary"]
        assert len(summary_secs) == 1, "Should have exactly one summary section"
        # Original section_id preserved
        assert summary_secs[0].section_id == orig_about_section_id, "section_id must be preserved"
        # Heading para_id preserved
        assert summary_secs[0].heading.para_id == orig_about_heading_para_id
        # Body text updated
        assert "Updated summary content" in summary_secs[0].body_paras[0].text


# ---------------------------------------------------------------------------
# 10. enforce_no_unbound_paragraphs safety net
# ---------------------------------------------------------------------------

class TestEnforceNoUnbound:
    def test_unbound_body_para_packed_into_last_anchored(self):
        """enforce_no_unbound packs an unbound body para into the last anchored slot."""
        from tailor.compiler.models import ParaModel, ParaStyle, ResumeSection
        from tailor.compiler.updater import enforce_no_unbound_paragraphs

        def _p(text, pid):
            pm = ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
            pm.para_id = pid
            return pm

        anchored1 = _p("Anchored 1", "para_a")
        anchored2 = _p("Anchored 2", "para_b")
        unbound = _p("Unbound extra", "")  # no para_id

        sec = ResumeSection(
            title="Skills",
            heading=_p("Skills", "para_h"),
            semantic_type="skills",
            body_paras=[anchored1, anchored2, unbound],
        )
        sec.section_id = "sec_1"

        enforce_no_unbound_paragraphs([sec], [])

        content = [p for p in sec.body_paras if p.text.strip()]
        # Only 2 content paras (no unbound)
        assert len(content) == 2
        # Unbound text packed into last anchored
        assert "Unbound extra" in content[-1].text
        assert "Anchored 2" in content[-1].text
        # para_id preserved
        assert content[-1].para_id == "para_b"

    def test_enforce_called_during_apply_tailored(self, monkeypatch):
        """After apply_tailored in layout-bound mode, all_paras has no unbound non-empty."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = _make_layout_doc(2, n_bullets_per_role=2)
        llm_secs = [_make_llm_experience(2, n_bullets_per_role=5)]
        updated = apply_tailored(doc, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0


# ---------------------------------------------------------------------------
# 11. Sample 31 structural smoke test
# ---------------------------------------------------------------------------

class TestSample31StructuralSmoke:
    _SAMPLE_31 = str(
        Path(__file__).parent / "samples" / "resume" / "docx"
        / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
    )

    def test_sample31_no_unbound_after_layout_bound_update(self, monkeypatch):
        """Sample 31: no non-empty para_id='' after layout-bound apply_tailored."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored, validate_layout_binding

        doc = parse_docx(self._SAMPLE_31)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)
        assert deser.layout_blocks is not None

        # Build minimal LLM text from the parsed structure
        llm_lines = []
        for sec in deser.sections:
            if sec.semantic_type in ("education", "certifications", "languages", "websites"):
                continue
            llm_lines.append(sec.title)
            if sec.semantic_type == "experience":
                for role in sec.roles[:3]:
                    llm_lines.append(role.header.text)
                    if role.meta_lines:
                        llm_lines.append(role.meta_lines[0].text)
                    for b in role.bullets[:3]:
                        llm_lines.append(f"- {b.text}")
            else:
                for p in sec.body_paras[:2]:
                    if p.text.strip():
                        llm_lines.append(p.text)
            llm_lines.append("")

        llm_secs = parse_llm_output("\n".join(llm_lines))
        if not llm_secs:
            pytest.skip("could not parse LLM text from sample 31")

        updated = apply_tailored(deser, llm_secs)
        metrics = validate_layout_binding(updated)

        assert metrics["unbound_non_empty_paras"] == 0, (
            f"sample 31: {metrics['unbound_non_empty_paras']} unbound non-empty paras"
        )

    def test_sample31_experience_has_roles(self, monkeypatch):
        """Sample 31: experience section retains roles (not collapsed)."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(self._SAMPLE_31)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

        exp_orig = next((s for s in deser.sections if s.semantic_type == "experience"), None)
        if exp_orig is None:
            pytest.skip("no experience section in sample 31")
        n_orig_roles = len(exp_orig.roles)

        llm_lines = [exp_orig.title]
        for role in exp_orig.roles:
            llm_lines.append(role.header.text)
            if role.meta_lines:
                llm_lines.append(role.meta_lines[0].text)
            for b in role.bullets[:2]:
                llm_lines.append(f"- {b.text}")
        llm_lines.append("")

        llm_secs = parse_llm_output("\n".join(llm_lines))
        if not llm_secs:
            pytest.skip("could not parse experience LLM text")

        updated = apply_tailored(deser, llm_secs)
        exp_updated = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp_updated is not None
        assert len(exp_updated.roles) == n_orig_roles, (
            f"expected {n_orig_roles} roles, got {len(exp_updated.roles)}"
        )

