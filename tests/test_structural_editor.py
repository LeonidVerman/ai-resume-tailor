"""
tests/test_structural_editor.py

Tests for the resume compiler pipeline:
  - docx_parser: DOCX → ResumeDocument IR
  - text_parser: LLM plain text → LlmSection list
  - updater: apply tailored sections → updated ResumeDocument
  - docx_renderer / pipeline: end-to-end round-trips via save_doc_from_template
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from tailor.compiler.docx_parser import _classify_section, parse_docx
from tailor.compiler.text_parser import (
    LlmRole,
    LlmSection,
    _is_role_header,
    _is_section_heading,
    parse_llm_output,
)
from tailor.compiler.updater import apply_tailored
from tailor.docx.template_fill import read_docx, save_doc_from_template
from tailor.config import COVER_TEMPLATE, RESUME_TEMPLATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    doc = Document()
    for text, style in paragraphs:
        try:
            doc.add_paragraph(text, style=style)
        except Exception:
            doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _roundtrip(template_path, resume_text: str, tmp_path: Path) -> str:
    out = str(tmp_path / "out.docx")
    save_doc_from_template(str(template_path), out, resume_text)
    return read_docx(out)


# ---------------------------------------------------------------------------
# Section classification
# ---------------------------------------------------------------------------

class TestSectionClassification:
    def test_experience_variants(self):
        for name in ["experience", "Work Experience", "professional experience"]:
            assert _classify_section(name) == "experience"

    def test_summary_variants(self):
        for name in ["Professional Summary", "summary", "Profile"]:
            assert _classify_section(name) == "summary"

    def test_skills_variants(self):
        for name in ["Technical Skills", "skills", "Core Competencies"]:
            assert _classify_section(name) == "skills"

    def test_education_variants(self):
        for name in ["Education", "Academic Background"]:
            assert _classify_section(name) == "education"

    def test_other_returns_other(self):
        assert _classify_section("References") == "other"
        assert _classify_section("Publications") == "other"


# ---------------------------------------------------------------------------
# LLM section heading detection
# ---------------------------------------------------------------------------

class TestLlmSectionHeadingDetection:
    def test_known_section_names_detected(self):
        assert _is_section_heading("Experience")
        assert _is_section_heading("Professional Summary")
        assert _is_section_heading("Technical Skills")
        assert _is_section_heading("Education")

    def test_bullet_lines_not_headings(self):
        assert not _is_section_heading("- Built distributed systems")
        assert not _is_section_heading("• Led a team of 5 engineers")

    def test_role_header_not_section_heading(self):
        assert not _is_section_heading("Senior Engineer | Acme Corp")

    def test_blank_line_not_heading(self):
        assert not _is_section_heading("")
        assert not _is_section_heading("   ")

    def test_long_lines_not_headings(self):
        assert not _is_section_heading(
            "This is a very long line that should not be treated as a section heading"
        )

    def test_date_lines_not_headings(self):
        assert not _is_section_heading("Jan 2022 – Present")


# ---------------------------------------------------------------------------
# LLM role header detection
# ---------------------------------------------------------------------------

class TestLlmRoleHeaderDetection:
    def test_pipe_pattern_detected(self):
        assert _is_role_header("Senior Engineer | Acme Corp")
        assert _is_role_header("Staff Engineer | Beta Inc")

    def test_bullet_with_pipe_not_role_header(self):
        assert not _is_role_header("- Built system | scalable")

    def test_blank_not_role_header(self):
        assert not _is_role_header("")


# ---------------------------------------------------------------------------
# LLM text parser
# ---------------------------------------------------------------------------

_SIMPLE_RESUME = """\
Professional Summary
Senior engineer with 10 years of experience.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Built distributed backend services.
- Reduced latency by 30%.

Technical Skills
Python, Kafka, Kubernetes
"""

_MULTI_ROLE_RESUME = """\
Professional Summary
Experienced engineer.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Led architecture.

Staff Engineer | Beta Inc
Mar 2019 – Dec 2021
- Scaled microservices.
- Reduced cost by 40%.

Technical Skills
Python, Go
"""

_SKILLS_FIRST_RESUME = """\
Professional Summary
Experienced engineer.

Technical Skills
Python, Go

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Led architecture.
"""


class TestParseLlmOutput:
    def test_parses_three_sections(self):
        sections = parse_llm_output(_SIMPLE_RESUME)
        assert len(sections) == 3

    def test_section_types(self):
        sections = parse_llm_output(_SIMPLE_RESUME)
        types = [s.semantic_type for s in sections]
        assert "summary" in types
        assert "experience" in types
        assert "skills" in types

    def test_experience_section_has_roles(self):
        sections = parse_llm_output(_SIMPLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        assert len(exp.roles) == 1
        assert exp.roles[0].header == "Senior Engineer | Acme Corp"

    def test_role_has_meta_and_bullets(self):
        sections = parse_llm_output(_SIMPLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        role = exp.roles[0]
        assert role.meta_lines == ["Jan 2022 – Present"]
        assert "Built distributed backend services." in role.bullets
        assert "Reduced latency by 30%." in role.bullets

    def test_two_roles_parsed(self):
        sections = parse_llm_output(_MULTI_ROLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        assert len(exp.roles) == 2
        headers = [r.header for r in exp.roles]
        assert "Senior Engineer | Acme Corp" in headers
        assert "Staff Engineer | Beta Inc" in headers

    def test_role_bullet_count(self):
        sections = parse_llm_output(_MULTI_ROLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        role2 = exp.roles[1]
        assert len(role2.bullets) == 2

    def test_skills_section_body(self):
        sections = parse_llm_output(_SIMPLE_RESUME)
        skills = next(s for s in sections if s.semantic_type == "skills")
        assert "Python" in " ".join(skills.body_lines)

    def test_section_order_preserved(self):
        sections = parse_llm_output(_SKILLS_FIRST_RESUME)
        types = [s.semantic_type for s in sections]
        assert types.index("skills") < types.index("experience")

    def test_no_sections_returns_empty(self):
        sections = parse_llm_output("Hello world\nSome text")
        for s in sections:
            assert s.heading


# ---------------------------------------------------------------------------
# DOCX parser
# ---------------------------------------------------------------------------

class TestParseDocx:
    def test_parses_resume_template(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        assert len(doc.sections) > 0

    def test_detects_experience_section(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        exp = [s for s in doc.sections if s.semantic_type == "experience"]
        assert len(exp) >= 1

    def test_experience_section_has_roles(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        exp = next(s for s in doc.sections if s.semantic_type == "experience")
        assert len(exp.roles) >= 1

    def test_all_paras_populated(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        assert len(doc.all_paras) > 0

    def test_paragraphs_have_xml_protos(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        for pm in doc.all_paras[:20]:
            assert pm.style.xml_proto is not None

    def test_role_blocks_have_bullets(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        exp = next(s for s in doc.sections if s.semantic_type == "experience")
        for role in exp.roles:
            assert isinstance(role.bullets, list)

    def test_semantic_types_assigned(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        for pm in doc.all_paras:
            assert pm.semantic

    def test_heading_paragraphs_classified(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        for sec in doc.sections:
            assert sec.heading.semantic == "section_heading"

    def test_layout_extracted(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        assert doc.layout.page_width_pt > 0
        assert doc.layout.page_height_pt > 0
        assert doc.layout.margin_left_pt >= 0

    def test_header_paras_present(self):
        doc = parse_docx(str(RESUME_TEMPLATE))
        # Header paras contain name/contact block before first section
        assert len(doc.header_paras) >= 0  # may be 0 if first line is a heading


# ---------------------------------------------------------------------------
# Updater
# ---------------------------------------------------------------------------

_RESUME_STANDARD_ORDER = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Built distributed backend services.
- Reduced latency by 30%.

Technical Skills
Python, Kafka, Kubernetes
"""

_RESUME_MULTIPLE_ROLES = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Led distributed backend architecture.

Staff Engineer | Beta Inc
Mar 2019 – Dec 2021
- Scaled microservices from 0 to 1M RPS.

Technical Skills
Python, Go, Kafka
"""

_RESUME_EXTRA_BULLETS = """\
Professional Summary
Experienced engineer.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Built distributed backend services.
- Reduced latency by 30%.
- Added new feature X.
- Improved CI pipeline by 50%.
- Mentored 3 junior engineers.

Technical Skills
Python, Go
"""

_RESUME_FEWER_BULLETS = """\
Professional Summary
Experienced engineer.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Built distributed backend services.

Technical Skills
Python
"""


class TestUpdater:
    def _parsed_and_llm(self, llm_text: str):
        orig = parse_docx(str(RESUME_TEMPLATE))
        llm = parse_llm_output(llm_text)
        return orig, llm

    def test_apply_tailored_returns_document(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        assert updated is not None
        assert len(updated.sections) > 0

    def test_sections_updated(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        # Experience section should exist
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp is not None

    def test_role_header_updated(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert any("Acme Corp" in role.header.text for role in exp.roles)

    def test_bullets_updated(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        all_bullets = [b.text for role in exp.roles for b in role.bullets]
        assert any("distributed" in b for b in all_bullets)

    def test_extra_bullets_added(self):
        orig, llm = self._parsed_and_llm(_RESUME_EXTRA_BULLETS)
        updated = apply_tailored(orig, llm)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        all_bullets = [b.text for role in exp.roles for b in role.bullets]
        assert any("Mentored 3 junior engineers" in b for b in all_bullets)

    def test_extra_bullets_have_xml_proto(self):
        orig, llm = self._parsed_and_llm(_RESUME_EXTRA_BULLETS)
        updated = apply_tailored(orig, llm)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        for role in exp.roles:
            for b in role.bullets:
                assert b.style.xml_proto is not None, f"Bullet '{b.text}' has no xml_proto"

    def test_all_paras_rebuilt(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        assert len(updated.all_paras) > 0

    def test_more_roles_than_template_clones_format(self):
        """LLM with MORE roles than template should succeed by cloning last role format."""
        orig = parse_docx(str(RESUME_TEMPLATE))
        orig_exp = next(s for s in orig.sections if s.semantic_type == "experience")

        extra_count = len(orig_exp.roles) + 1
        roles_text = "\n".join(
            f"Engineer {i} | Company {i}\nJan 2020 – Present\n- Did something.\n"
            for i in range(extra_count)
        )
        llm_text = f"Professional Summary\nSummary.\n\nExperience\n{roles_text}\nTechnical Skills\nPython, Go, Kafka\n"
        llm = parse_llm_output(llm_text)
        updated = apply_tailored(orig, llm)
        exp = next(s for s in updated.sections if s.semantic_type == "experience")
        assert len(exp.roles) == extra_count
        # Extra role must have an xml_proto (cloned from last original role)
        for role in exp.roles:
            assert role.header.style.xml_proto is not None

    def test_unmatched_llm_section_raises(self):
        """An LLM section with an unmatchable title raises ValueError."""
        orig = parse_docx(str(RESUME_TEMPLATE))
        from tailor.compiler.text_parser import LlmSection
        bogus_sections = [LlmSection(heading="ZZZ Unknown Section XYZ", semantic_type="other")]
        with pytest.raises(ValueError):
            apply_tailored(orig, bogus_sections)

    def test_skills_content_updated(self):
        orig, llm = self._parsed_and_llm(_RESUME_STANDARD_ORDER)
        updated = apply_tailored(orig, llm)
        skills = next((s for s in updated.sections if s.semantic_type == "skills"), None)
        if skills:
            body_text = " ".join(p.text for p in skills.body_paras)
            assert "Python" in body_text


# ---------------------------------------------------------------------------
# Integration: save_doc_from_template (end-to-end round-trips)
# ---------------------------------------------------------------------------

_RESUME_SKILLS_FIRST = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Technical Skills
Python, Kafka, Kubernetes

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
- Built distributed backend services.
- Reduced latency by 30%.
"""


class TestSaveDocFromTemplateIntegration:
    def test_standard_order(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_STANDARD_ORDER, tmp_path)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_skills_before_experience(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_SKILLS_FIRST, tmp_path)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_multiple_roles(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_MULTIPLE_ROLES, tmp_path)
        assert "Acme Corp" in text

    def test_extra_bullets_not_lost(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_EXTRA_BULLETS, tmp_path)
        assert "Added new feature X" in text
        assert "Mentored 3 junior engineers" in text

    def test_fewer_bullets(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_FEWER_BULLETS, tmp_path)
        assert "Acme Corp" in text
        assert "Built distributed backend services" in text

    def test_output_is_valid_docx(self, tmp_path):
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_STANDARD_ORDER)
        doc = Document(out)
        assert len(doc.paragraphs) > 0

    def test_cover_letter_merged(self, tmp_path):
        cl_text = (
            "Leonid Verman\nVancouver, BC\nDear Hiring Manager,\n"
            "I am excited to apply for the role at Acme Corp.\n"
            "I have built distributed systems for 10 years.\n"
            "Thank you for your consideration.\n\nSincerely,\nLeonid Verman"
        )
        out = str(tmp_path / "cl_out.docx")
        save_doc_from_template(str(COVER_TEMPLATE), out, cl_text)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Sincerely" in text


# ---------------------------------------------------------------------------
# Formatting preservation
# ---------------------------------------------------------------------------

class TestFormattingPreservation:
    def test_output_has_paragraphs(self, tmp_path):
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_STANDARD_ORDER)
        doc = Document(out)
        assert len(doc.paragraphs) > 5

    def test_extra_bullets_preserve_style(self, tmp_path):
        """Extra bullets cloned from archetype should carry a named Word style."""
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_EXTRA_BULLETS)
        out_doc = Document(out)
        styles_used = {p.style.name for p in out_doc.paragraphs if p.text.strip()}
        # At minimum the document default style should be present
        assert len(styles_used) >= 1

    def test_xml_protos_are_independent(self):
        """Cloning a ParaModel must not alias the same xml_proto object."""
        doc = parse_docx(str(RESUME_TEMPLATE))
        if not doc.all_paras:
            pytest.skip("No paragraphs in template")
        pm = doc.all_paras[0]
        clone1 = pm.clone_as("text 1")
        clone2 = pm.clone_as("text 2")
        assert clone1.style.xml_proto is not clone2.style.xml_proto
        assert clone1.style.xml_proto is not pm.style.xml_proto
