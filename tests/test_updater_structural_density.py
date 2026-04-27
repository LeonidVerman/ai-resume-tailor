"""Tests for layout density validation, hard-ban synthetic sections, and overflow drops.

Covers:
1. validate_layout_density detects overflow and multi-bullet packing
2. repair_layout_density truncates overflowed paragraphs
3. Synthetic sections with empty section_id are removed in layout-bound mode
4. Skills 8-line overflow → only 2 slots used, rest dropped
5. No "\n" in any bullet or body para after layout-bound update
6. Sample 31 smoke: no multi-bullet packing, no huge paragraph expansion
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SIMPLE_TEMPLATE = str(_DOCX_DIR / "1-Leonid_Verman_Resume_Template.docx")
_SAMPLE_31 = str(
    _DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_para(text: str, para_id: str = "", semantic: str = "paragraph"):
    from tailor.compiler.models import ParaModel, ParaStyle
    pm = ParaModel(text=text, style=ParaStyle(), semantic=semantic)
    pm.para_id = para_id
    return pm


def _make_doc(sections_spec: list[tuple]):
    """Build minimal ResumeDocument from (title, semantic_type, [body_texts]) tuples."""
    from tailor.compiler.models import (
        LayoutParagraphBlock, LayoutProfile, ParaModel, ParaStyle,
        ResumeDocument, ResumeSection, assign_stable_ids,
    )
    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )
    sections = []
    for title, sem_type, body_texts in sections_spec:
        heading = _make_para(title, semantic="section_heading")
        body = [_make_para(t) for t in body_texts]
        sec = ResumeSection(title=title, heading=heading, semantic_type=sem_type, body_paras=body)
        sections.append(sec)
    doc = ResumeDocument(header_paras=[], sections=sections, layout=layout, all_paras=[])
    assign_stable_ids(doc)
    doc.layout_blocks = [
        LayoutParagraphBlock(para_id=pm.para_id)
        for pm in doc.all_paras if pm.para_id
    ]
    return doc


# ---------------------------------------------------------------------------
# 1. validate_layout_density
# ---------------------------------------------------------------------------

class TestValidateLayoutDensity:
    def test_detects_length_overflow(self):
        """Para whose updated text is > max(200, orig_len*2) is flagged."""
        from tailor.compiler.models import LayoutProfile, ResumeDocument, assign_stable_ids
        from tailor.compiler.updater import validate_layout_density

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        orig_para = _make_para("Short text.", "para_1")
        updated_para = _make_para("A" * 500, "para_1")

        from tailor.compiler.models import ResumeSection
        sec_o = ResumeSection(title="S", heading=_make_para("S", "h"), semantic_type="skills", body_paras=[orig_para])
        sec_u = ResumeSection(title="S", heading=_make_para("S", "h"), semantic_type="skills", body_paras=[updated_para])

        orig = ResumeDocument(header_paras=[], sections=[sec_o], layout=layout, all_paras=[orig_para])
        updated = ResumeDocument(header_paras=[], sections=[sec_u], layout=layout, all_paras=[updated_para])
        assign_stable_ids(orig)
        orig_para.para_id = "para_1"
        updated_para.para_id = "para_1"

        violations = validate_layout_density(orig, updated)
        assert violations["density_overflow_count"] >= 1

    def test_detects_newline_packing(self):
        """Para with newlines whose updated length > 1.3x orig is flagged as multi-bullet."""
        from tailor.compiler.models import LayoutProfile, ResumeDocument
        from tailor.compiler.updater import validate_layout_density

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        orig_para = _make_para("Built systems.", "para_x")
        packed_para = _make_para("Built systems.\nDid analysis.\nLed team.", "para_x")

        from tailor.compiler.models import ResumeSection
        sec_o = ResumeSection(title="E", heading=_make_para("E", "h"), semantic_type="experience", body_paras=[orig_para])
        sec_u = ResumeSection(title="E", heading=_make_para("E", "h"), semantic_type="experience", body_paras=[packed_para])

        orig = ResumeDocument(header_paras=[], sections=[sec_o], layout=layout, all_paras=[orig_para])
        updated = ResumeDocument(header_paras=[], sections=[sec_u], layout=layout, all_paras=[packed_para])

        violations = validate_layout_density(orig, updated)
        assert violations["multi_bullet_packing_count"] >= 1

    def test_clean_update_no_violations(self):
        """Para updated with similar-length text has no violations."""
        from tailor.compiler.models import LayoutProfile, ResumeDocument
        from tailor.compiler.updater import validate_layout_density

        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        orig_para = _make_para("Built distributed systems.", "para_1")
        updated_para = _make_para("Architected distributed systems.", "para_1")

        from tailor.compiler.models import ResumeSection
        sec_o = ResumeSection(title="S", heading=_make_para("S", "h"), semantic_type="skills", body_paras=[orig_para])
        sec_u = ResumeSection(title="S", heading=_make_para("S", "h"), semantic_type="skills", body_paras=[updated_para])

        orig = ResumeDocument(header_paras=[], sections=[sec_o], layout=layout, all_paras=[orig_para])
        updated = ResumeDocument(header_paras=[], sections=[sec_u], layout=layout, all_paras=[updated_para])

        violations = validate_layout_density(orig, updated)
        assert violations["density_overflow_count"] == 0
        assert violations["multi_bullet_packing_count"] == 0


# ---------------------------------------------------------------------------
# 2. repair_layout_density
# ---------------------------------------------------------------------------

class TestRepairLayoutDensity:
    def test_overflowed_para_truncated_to_first_line(self):
        """Para with density overflow is truncated to first line."""
        from tailor.compiler.updater import repair_layout_density

        orig_para = _make_para("Short.", "para_1")
        overflowed = _make_para("Line one.\nLine two.\nLine three.", "para_1")

        repair_layout_density({"para_1": orig_para}, [overflowed])
        # Truncated to first line
        assert "\n" not in overflowed.text
        assert "Line one" in overflowed.text
        assert "Line two" not in overflowed.text

    def test_clean_para_unchanged(self):
        """Para within density bounds is not modified."""
        from tailor.compiler.updater import repair_layout_density

        orig_para = _make_para("Built systems.", "para_1")
        clean = _make_para("Architected systems.", "para_1")
        original_text = clean.text

        repair_layout_density({"para_1": orig_para}, [clean])
        assert clean.text == original_text

    def test_density_repair_called_in_apply_tailored(self, monkeypatch):
        """apply_tailored in layout-bound mode applies density repair automatically."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_density

        # Use real template and ensure no density overflow after update
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else '2020 - Present'}\n"
            + "\n".join(f"- {b.text}" for b in role.bullets[:2])
        )
        llm_secs = parse_llm_output(llm_text)
        if not llm_secs:
            pytest.skip("could not parse LLM text")

        updated = apply_tailored(doc, llm_secs)
        violations = validate_layout_density(doc, updated)
        assert violations["multi_bullet_packing_count"] == 0


# ---------------------------------------------------------------------------
# 3. Hard ban synthetic sections in layout-bound mode
# ---------------------------------------------------------------------------

class TestHardBanSyntheticSections:
    def test_section_with_empty_id_and_content_removed(self, monkeypatch):
        """Section with section_id='' and non-empty content is removed in layout-bound mode."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored

        doc = _make_doc([("Skills", "skills", ["Python", "Go"])])
        from tailor.compiler.text_parser import LlmSection

        # LLM outputs a summary that has no original anchor → should be dropped
        llm_secs = [
            LlmSection(
                heading="Professional Summary",
                semantic_type="summary",
                body_lines=["I am a developer with 5 years experience."],
            ),
            LlmSection(heading="Skills", semantic_type="skills", body_lines=["Python", "Go"]),
        ]
        updated = apply_tailored(doc, llm_secs)

        # No summary section in updated IR
        section_types = [s.semantic_type for s in updated.sections]
        assert "summary" not in section_types, "Unanchored summary must be removed"

        # No section with empty section_id and content
        for sec in updated.sections:
            if not sec.section_id:
                has_content = any(p.text.strip() for p in sec.body_paras)
                assert not has_content, f"Section {sec.title!r} has empty section_id but content"

    def test_original_sections_keep_section_id(self, monkeypatch):
        """All original sections retain their section_id in layout-bound mode."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored
        from tailor.compiler.text_parser import LlmSection

        doc = _make_doc([
            ("Summary", "summary", ["Original summary."]),
            ("Skills", "skills", ["Python"]),
        ])
        orig_section_ids = {s.section_id for s in doc.sections if s.section_id}

        llm_secs = [
            LlmSection(heading="Summary", semantic_type="summary", body_lines=["Updated summary."]),
            LlmSection(heading="Skills", semantic_type="skills", body_lines=["Go"]),
        ]
        updated = apply_tailored(doc, llm_secs)

        for sec in updated.sections:
            if sec.section_id:
                assert sec.section_id in orig_section_ids, (
                    f"section_id {sec.section_id!r} not from original"
                )


# ---------------------------------------------------------------------------
# 4. Skills overflow: 8 lines → 2 slots (drop, not pack)
# ---------------------------------------------------------------------------

class TestSkillsOverflowDrop:
    def test_skills_8_lines_2_slots_drops_overflow(self, monkeypatch):
        """8 LLM skill lines with 2 original slots → 2 slots updated, 6 dropped."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored
        from tailor.compiler.text_parser import LlmSection

        doc = _make_doc([("Technical Skills", "skills", ["Python, Go", "AWS, Docker"])])
        llm_secs = [LlmSection(
            heading="Technical Skills", semantic_type="skills",
            body_lines=[
                "Python, Go, Rust",
                "AWS, Docker, Kubernetes",
                "PostgreSQL, Redis",
                "Kafka, RabbitMQ",
                "TensorFlow, PyTorch",
                "React, TypeScript",
                "GraphQL, REST",
                "Linux, Git",
            ],
        )]
        updated = apply_tailored(doc, llm_secs)

        skills = next(s for s in updated.sections if s.semantic_type == "skills")
        content = [p for p in skills.body_paras if p.text.strip()]
        assert len(content) == 2, f"expected 2 content paras, got {len(content)}"
        # No newline packing
        for p in content:
            assert "\n" not in p.text, f"no newline packing: {p.text!r}"
        # First 2 LLM lines in slots
        assert "Python" in content[0].text
        assert "AWS" in content[1].text

    def test_skills_overflow_no_unbound_paras(self, monkeypatch):
        """Skills overflow drop leaves zero unbound non-empty paras."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored, validate_layout_binding
        from tailor.compiler.text_parser import LlmSection

        doc = _make_doc([("Skills", "skills", ["A", "B"])])
        llm_secs = [LlmSection(
            heading="Skills", semantic_type="skills",
            body_lines=["X", "Y", "Z", "W", "V", "U"],
        )]
        updated = apply_tailored(doc, llm_secs)

        metrics = validate_layout_binding(updated)
        assert metrics["unbound_non_empty_paras"] == 0


# ---------------------------------------------------------------------------
# 5. No newlines in bullets or body paras after layout-bound update
# ---------------------------------------------------------------------------

class TestNoNewlineInUpdatedContent:
    def test_no_newline_in_bullets(self, monkeypatch):
        """No bullet para contains \\n after layout-bound update."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import LlmRole, LlmSection
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_exp = LlmSection(
            heading=exp.title, semantic_type="experience",
            roles=[LlmRole(
                header=role.header.text,
                bullets=[f"LLM bullet {i}" for i in range(len(role.bullets) + 5)],
            )],
        )
        updated = apply_tailored(doc, [llm_exp])

        exp_updated = next(s for s in updated.sections if s.semantic_type == "experience")
        for r in exp_updated.roles:
            for b in r.bullets:
                assert "\n" not in b.text, f"bullet contains newline: {b.text!r}"

    def test_no_newline_in_body_paras(self, monkeypatch):
        """No body para contains \\n after layout-bound update."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.updater import apply_tailored
        from tailor.compiler.text_parser import LlmSection

        doc = _make_doc([("Skills", "skills", ["Python", "Go"])])
        llm_secs = [LlmSection(
            heading="Skills", semantic_type="skills",
            body_lines=["X", "Y", "Z", "W", "V", "U"],
        )]
        updated = apply_tailored(doc, llm_secs)

        for sec in updated.sections:
            for p in sec.body_paras:
                assert "\n" not in p.text, f"body para contains newline: {p.text!r}"


# ---------------------------------------------------------------------------
# 6. Sample 31 smoke: density and structure
# ---------------------------------------------------------------------------

class TestSample31DensitySmoke:
    def test_sample31_no_newline_packing_after_update(self, monkeypatch):
        """Sample 31: no multi-bullet newline packing after layout-bound update."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored, validate_layout_density

        doc = parse_docx(_SAMPLE_31)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

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
            pytest.skip("could not parse LLM text for sample 31")

        updated = apply_tailored(deser, llm_secs)

        # No newline packing in any bullet
        for sec in updated.sections:
            for role in sec.roles:
                for b in role.bullets:
                    assert "\n" not in b.text, f"newline in bullet: {b.text!r}"

        # Density violations should be zero or minimal
        violations = validate_layout_density(deser, updated)
        assert violations["multi_bullet_packing_count"] == 0

    def test_sample31_no_synthetic_summary(self, monkeypatch):
        """Sample 31: no synthetic Professional Summary section if no original anchor."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import LlmSection, parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

        # Check if original has a summary anchor
        has_summary_anchor = any(s.semantic_type == "summary" for s in deser.sections)

        # Add a "Professional Summary" to LLM output that may not match
        llm_secs = [LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=["Experienced software engineer with diverse skills."],
        )]

        updated = apply_tailored(deser, llm_secs)

        if not has_summary_anchor:
            # Summary must NOT appear as a new section
            section_types = [s.semantic_type for s in updated.sections]
            assert "summary" not in section_types, (
                "Sample 31 has no summary anchor; synthetic summary must not be created"
            )
        # If has_summary_anchor: the summary was matched and updated in-place (OK)
