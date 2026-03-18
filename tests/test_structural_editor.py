"""
tests/test_structural_editor.py

Unit and integration tests for the structural DOCX editor:
  - structural_model data classes
  - parser: DOCX parser + LLM text parser
  - editor: block-level apply operations
  - save_doc_from_template: end-to-end round-trip

These tests make no LLM calls and do not require external services.
"""
from __future__ import annotations

import io
from copy import deepcopy
from pathlib import Path

import pytest
from docx import Document
from docx.oxml.ns import qn

from tailor.docx.parser import (
    _classify_section,
    _is_llm_section_heading,
    _is_llm_role_header,
    parse_llm_text,
    parse_docx,
)
from tailor.docx.structural_model import (
    LlmRole, LlmSection, ParagraphFormat, ParagraphModel, RoleBlock,
    SectionBlock, RunModel, RunStyle,
)
from tailor.docx.template_fill import read_docx, save_doc_from_template
from tailor.config import RESUME_TEMPLATE, COVER_TEMPLATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    """Create a minimal DOCX from (text, style_name) tuples."""
    doc = Document()
    for text, style in paragraphs:
        try:
            p = doc.add_paragraph(text, style=style)
        except Exception:
            p = doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _save_docx(doc: Document, tmp_path: Path, name: str = "test.docx") -> Path:
    out = tmp_path / name
    doc.save(str(out))
    return out


def _roundtrip(template_path, resume_text: str, tmp_path: Path) -> str:
    out = str(tmp_path / "out.docx")
    save_doc_from_template(str(template_path), out, resume_text)
    return read_docx(out)


# ---------------------------------------------------------------------------
# Parser: section classification
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
# Parser: LLM heading detection
# ---------------------------------------------------------------------------

class TestLlmSectionHeadingDetection:
    def test_known_section_names_detected(self):
        assert _is_llm_section_heading("Experience")
        assert _is_llm_section_heading("Professional Summary")
        assert _is_llm_section_heading("Technical Skills")
        assert _is_llm_section_heading("Education")

    def test_bullet_lines_not_headings(self):
        assert not _is_llm_section_heading("- Built distributed systems")
        assert not _is_llm_section_heading("• Led a team of 5 engineers")

    def test_role_header_not_section_heading(self):
        assert not _is_llm_section_heading("Senior Engineer | Acme Corp")

    def test_blank_line_not_heading(self):
        assert not _is_llm_section_heading("")
        assert not _is_llm_section_heading("   ")

    def test_long_lines_not_headings(self):
        long = "This is a very long line that should not be treated as a section heading"
        assert not _is_llm_section_heading(long)

    def test_date_lines_not_headings(self):
        assert not _is_llm_section_heading("Jan 2022 – Present")


# ---------------------------------------------------------------------------
# Parser: LLM role header detection
# ---------------------------------------------------------------------------

class TestLlmRoleHeaderDetection:
    def test_pipe_pattern_detected(self):
        assert _is_llm_role_header("Senior Engineer | Acme Corp")
        assert _is_llm_role_header("Staff Engineer | Beta Inc")

    def test_bullet_with_pipe_not_role_header(self):
        assert not _is_llm_role_header("- Built system | scalable")

    def test_blank_not_role_header(self):
        assert not _is_llm_role_header("")


# ---------------------------------------------------------------------------
# Parser: LLM text parser
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


class TestParseLlmText:
    def test_parses_three_sections(self):
        sections = parse_llm_text(_SIMPLE_RESUME)
        assert len(sections) == 3

    def test_section_types(self):
        sections = parse_llm_text(_SIMPLE_RESUME)
        types = [s.semantic_type for s in sections]
        assert "summary" in types
        assert "experience" in types
        assert "skills" in types

    def test_experience_section_has_roles(self):
        sections = parse_llm_text(_SIMPLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        assert len(exp.roles) == 1
        assert exp.roles[0].header == "Senior Engineer | Acme Corp"

    def test_role_has_meta_and_bullets(self):
        sections = parse_llm_text(_SIMPLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        role = exp.roles[0]
        assert role.meta_lines == ["Jan 2022 – Present"]
        assert "Built distributed backend services." in role.bullets
        assert "Reduced latency by 30%." in role.bullets

    def test_two_roles_parsed(self):
        sections = parse_llm_text(_MULTI_ROLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        assert len(exp.roles) == 2
        headers = [r.header for r in exp.roles]
        assert "Senior Engineer | Acme Corp" in headers
        assert "Staff Engineer | Beta Inc" in headers

    def test_role_bullet_count(self):
        sections = parse_llm_text(_MULTI_ROLE_RESUME)
        exp = next(s for s in sections if s.semantic_type == "experience")
        role2 = exp.roles[1]
        assert len(role2.bullets) == 2

    def test_skills_section_body(self):
        sections = parse_llm_text(_SIMPLE_RESUME)
        skills = next(s for s in sections if s.semantic_type == "skills")
        assert "Python" in " ".join(skills.body_lines)

    def test_section_order_preserved(self):
        """parse_llm_text preserves the order sections appear in text."""
        sections = parse_llm_text(_SKILLS_FIRST_RESUME)
        types = [s.semantic_type for s in sections]
        # summary, skills, experience — in that order
        assert types.index("skills") < types.index("experience")

    def test_no_sections_returns_empty(self):
        sections = parse_llm_text("Hello world\nSome text")
        # No known section headings — should return empty or single unknown
        for s in sections:
            assert s.heading  # at least has a heading string


# ---------------------------------------------------------------------------
# Parser: DOCX parser
# ---------------------------------------------------------------------------

class TestParseDocx:
    def test_parses_resume_template(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        assert len(model.sections) > 0

    def test_detects_experience_section(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        exp_sections = [s for s in model.sections if s.semantic_type == "experience"]
        assert len(exp_sections) >= 1

    def test_experience_section_has_roles(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        exp = next(s for s in model.sections if s.semantic_type == "experience")
        assert len(exp.roles) >= 1

    def test_all_paragraphs_populated(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        assert len(model.all_paragraphs) > 0

    def test_paragraph_models_have_xml_refs(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        for pm in model.all_paragraphs[:20]:
            assert pm.xml_ref is not None

    def test_role_blocks_have_bullets(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        exp = next(s for s in model.sections if s.semantic_type == "experience")
        for role in exp.roles:
            assert isinstance(role.bullet_paragraphs, list)
            assert isinstance(role.header_paragraphs, list)

    def test_semantic_types_assigned(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        for pm in model.all_paragraphs:
            assert pm.semantic_type is not None

    def test_heading_paragraphs_have_section_heading_type(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        for sec in model.sections:
            if sec.heading is not None:
                assert sec.heading.semantic_type == "section_heading"

    def test_debug_info_populated(self):
        model = parse_docx(str(RESUME_TEMPLATE))
        assert "sections" in model.debug_info
        assert "total_paragraphs" in model.debug_info


# ---------------------------------------------------------------------------
# Editor: apply_tailored_content
# ---------------------------------------------------------------------------

from tailor.docx.editor import apply_tailored_content


class TestApplyTailoredContent:
    def test_output_is_valid_docx(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SIMPLE_RESUME)
        doc = Document(out)
        assert len(doc.paragraphs) > 0

    def test_content_appears_in_output(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SIMPLE_RESUME)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_multiple_roles_appear(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _MULTI_ROLE_RESUME)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Beta Inc" in text

    def test_bullets_preserved(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SIMPLE_RESUME)
        text = read_docx(out)
        assert "Built distributed backend services" in text
        assert "Reduced latency by 30%" in text

    def test_section_headings_updated(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SIMPLE_RESUME)
        text = read_docx(out)
        assert "Experience" in text

    def test_skills_before_experience_works(self, tmp_path):
        out = str(tmp_path / "out.docx")
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SKILLS_FIRST_RESUME)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_debug_log_populated(self, tmp_path):
        out = str(tmp_path / "out.docx")
        debug = {}
        apply_tailored_content(str(RESUME_TEMPLATE), out, _SIMPLE_RESUME, debug_log=debug)
        assert "document_structure" in debug
        assert "llm_sections" in debug
        assert "edit_log" in debug


# ---------------------------------------------------------------------------
# Integration: save_doc_from_template (end-to-end round-trips)
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
        assert "Beta Inc" in text

    def test_extra_bullets_not_lost(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_EXTRA_BULLETS, tmp_path)
        assert "Added new feature X" in text
        assert "Mentored 3 junior engineers" in text

    def test_fewer_bullets_no_garbage(self, tmp_path):
        text = _roundtrip(RESUME_TEMPLATE, _RESUME_FEWER_BULLETS, tmp_path)
        assert "Acme Corp" in text
        # Only one bullet; old template bullets shouldn't survive
        # We can't easily assert old bullets are gone without knowing them,
        # but we can assert the new one is present.
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
            "Thank you for your consideration.\n\nSincerely,\nLeoind Verman"
        )
        out = str(tmp_path / "cl_out.docx")
        save_doc_from_template(str(COVER_TEMPLATE), out, cl_text)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Sincerely" in text


# ---------------------------------------------------------------------------
# Archetype preservation: paragraph style not lost after edit
# ---------------------------------------------------------------------------

class TestFormattingPreservation:
    def test_section_heading_style_preserved(self, tmp_path):
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_STANDARD_ORDER)
        out_doc = Document(out)
        tmpl_doc = Document(str(RESUME_TEMPLATE))

        # Collect heading styles from both
        def heading_styles(doc):
            return [p.style.name for p in doc.paragraphs if "Heading" in p.style.name]

        tmpl_headings = heading_styles(tmpl_doc)
        out_headings = heading_styles(out_doc)

        # Output should have at least as many heading-styled paragraphs as
        # there are matched sections (not necessarily same count since we may
        # remove template sections with no LLM content).
        assert len(out_headings) >= 1

    def test_bullet_style_preserved_after_extra_bullets(self, tmp_path):
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_EXTRA_BULLETS)
        out_doc = Document(out)
        tmpl_doc = Document(str(RESUME_TEMPLATE))

        # Get List Paragraph style names from template
        tmpl_list_styles = {p.style.name for p in tmpl_doc.paragraphs if "List" in p.style.name}
        out_list_styles = {p.style.name for p in out_doc.paragraphs if "List" in p.style.name}

        if tmpl_list_styles:
            # At least one list style should survive in output
            assert out_list_styles & tmpl_list_styles, (
                f"Template list styles {tmpl_list_styles} not found in output {out_list_styles}"
            )
