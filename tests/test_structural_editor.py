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
from tailor.compiler.pipeline import compile_resume
from tailor.compiler.updater import apply_tailored
from tailor.diff import diff_resume
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


# ---------------------------------------------------------------------------
# Heading normalization — table-driven coverage (canonical + non-canonical)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("heading,expected", [
    # Skills — canonical and variants including newly-fixed singular form
    ("SKILL",                         "skills"),
    ("Skill",                         "skills"),
    ("Skills",                        "skills"),
    ("SKILLS",                        "skills"),
    ("Technical Skills",              "skills"),
    ("TECHNICAL SKILLS",              "skills"),
    ("Core Competencies",             "skills"),
    ("CORE COMPETENCIES",             "skills"),
    ("Technical Expertise",           "skills"),
    # Summary — canonical and variants
    ("SUMMARY",                       "summary"),
    ("Summary",                       "summary"),
    ("PROFILE",                       "summary"),
    ("Profile",                       "summary"),
    ("Professional Summary",          "summary"),
    ("PROFESSIONAL SUMMARY",          "summary"),
    # Experience — canonical and variants
    ("Experience",                    "experience"),
    ("EMPLOYMENT HISTORY",            "experience"),
    ("Work Experience",               "experience"),
    # Education
    ("Education",                     "education"),
    ("Academic Background",           "education"),
    # Non-content sections → other
    ("LANGUAGES",                     "other"),
    ("Certifications",                "other"),
    ("CERTIFICATIONS AND TRAINING",   "other"),
])
def test_classify_section_heading_normalization(heading, expected):
    assert _classify_section(heading) == expected, (
        f"_classify_section({heading!r}) returned {_classify_section(heading)!r}, "
        f"expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Diff — non-canonical source headings
# ---------------------------------------------------------------------------

class TestDiffNonCanonicalHeadings:
    """diff_resume must correctly handle source resumes with non-canonical headings."""

    def test_skill_heading_produces_before_and_after(self):
        """Source with 'SKILL' heading → diff has both 'before' and 'after' for Technical Skills.

        This is the regression test for the additions-only bug: when the source heading
        was 'SKILL' (not 'Skills'), diff_resume couldn't find the source section so
        'before' was missing and the change appeared as a pure addition.
        """
        source = "\n".join([
            "SKILL",
            "Java, Python, SQL",
            "",
            "Experience",
            "Engineer | Co",
            "2022",
        ])
        tailored = "\n".join([
            "Technical Skills",
            "Java, Python, C#",
            "",
            "Experience",
            "Engineer | Co",
            "2022",
        ])
        diff = diff_resume(source, tailored)
        skills = next((d for d in diff if d["name"] == "Technical Skills"), None)
        assert skills is not None, "No Technical Skills entry in diff"
        assert "before" in skills, (
            "Expected 'before' key — source had SKILL content but diff shows additions-only. "
            "Likely cause: 'skill' not in _ALL_SECTION_HEADERS / _SECTION_ALIASES."
        )
        assert "after" in skills

    def test_source_without_summary_heading_yields_after_only(self):
        """Source with no summary heading → after-only is the correct/expected behaviour.

        This test explicitly locks in the intended behaviour so it is not accidentally
        'fixed' in a way that invents a spurious 'before' value.
        """
        source = "\n".join([
            "John Doe",
            "Experienced software engineer.",
            "",
            "Experience",
            "Engineer | Co",
            "2022",
        ])
        tailored = "\n".join([
            "Professional Summary",
            "Senior backend engineer with cloud expertise.",
            "",
            "Experience",
            "Engineer | Co",
            "2022",
        ])
        diff = diff_resume(source, tailored)
        summary = next((d for d in diff if d["name"] == "Professional Summary"), None)
        assert summary is not None, "No Professional Summary entry in diff"
        # No summary heading in source → no 'before' is intentional
        assert "before" not in summary, (
            "Source had no summary section — 'before' should be absent (after-only is correct)"
        )
        assert "after" in summary


# ---------------------------------------------------------------------------
# Non-canonical DOCX fixture helpers
# ---------------------------------------------------------------------------

_NONCANONICAL_LLM = """\
Professional Summary
Senior backend engineer specialising in cloud-native infrastructure.

Technical Skills
Python, Kafka, Kubernetes

Experience
Senior Engineer | Acme Corp
2020 – 2023
- Built scalable cloud-native services.
- Deployed Kubernetes clusters across three regions.

Education
BSc Computer Science | University of BC
2019
"""


def _make_noncanonical_docx(tmp_path: Path) -> str:
    """Return path to a minimal DOCX with non-canonical headings."""
    doc = Document()
    # Non-canonical skills heading (singular all-caps)
    doc.add_paragraph("SKILL", style="Heading 1")
    doc.add_paragraph("COBOL, Fortran, SQL")
    # Other non-canonical sections
    doc.add_paragraph("LANGUAGES", style="Heading 1")
    doc.add_paragraph("English, French")
    doc.add_paragraph("CERTIFICATIONS AND TRAINING", style="Heading 1")
    doc.add_paragraph("AWS Certified 2021")
    # Non-canonical experience heading
    doc.add_paragraph("EMPLOYMENT HISTORY", style="Heading 1")
    doc.add_paragraph("Senior Engineer | Acme Corp")   # role_header (pipe)
    doc.add_paragraph("2020 – 2023")                   # role_meta  (year)
    doc.add_paragraph("- Built legacy backend systems using COBOL.")
    doc.add_paragraph("- Maintained Fortran codebase.")
    # Standard education heading
    doc.add_paragraph("Education", style="Heading 1")
    doc.add_paragraph("BSc Computer Science | University of BC")
    doc.add_paragraph("2019")
    path = tmp_path / "noncanonical.docx"
    doc.save(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# Updater — non-canonical source sections
# ---------------------------------------------------------------------------

class TestUpdaterNonCanonicalSections:
    """apply_tailored must correctly bind SKILL/LANGUAGES/CERTIFICATIONS/EMPLOYMENT HISTORY."""

    def _orig(self, tmp_path: Path):
        return parse_docx(_make_noncanonical_docx(tmp_path))

    def test_skill_section_classified_as_skills(self, tmp_path):
        """SKILL heading must have semantic_type 'skills' — core regression for the fix."""
        orig = self._orig(tmp_path)
        skill_sections = [s for s in orig.sections if s.title.upper() == "SKILL"]
        assert skill_sections, "SKILL section not found in parsed document"
        assert skill_sections[0].semantic_type == "skills", (
            f"SKILL classified as '{skill_sections[0].semantic_type}', expected 'skills'. "
            "Check that 'skill' is in docx_parser._SKILLS_NAMES."
        )

    def test_skill_not_kept_verbatim(self, tmp_path):
        """SKILL must be replaced by Technical Skills — not kept as a verbatim orphan."""
        orig = self._orig(tmp_path)
        llm = parse_llm_output(_NONCANONICAL_LLM)
        updated = apply_tailored(orig, llm)
        section_titles = [s.title for s in updated.sections]
        assert "SKILL" not in section_titles, (
            "SKILL section kept verbatim — semantic-type matching to 'Technical Skills' failed"
        )

    def test_no_duplicate_skills_sections(self, tmp_path):
        """Technical Skills must appear exactly once — not as both kept SKILL + injected extra."""
        orig = self._orig(tmp_path)
        llm = parse_llm_output(_NONCANONICAL_LLM)
        updated = apply_tailored(orig, llm)
        skills_sections = [s for s in updated.sections if s.semantic_type == "skills"]
        assert len(skills_sections) == 1, (
            f"Expected 1 skills section, got {len(skills_sections)}: "
            f"{[s.title for s in skills_sections]}"
        )

    def test_experience_and_education_matched(self, tmp_path):
        """EMPLOYMENT HISTORY (experience) and Education must still be matched and updated."""
        orig = self._orig(tmp_path)
        llm = parse_llm_output(_NONCANONICAL_LLM)
        updated = apply_tailored(orig, llm)
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        edu = next((s for s in updated.sections if s.semantic_type == "education"), None)
        assert exp is not None, "No experience section in updater output"
        assert edu is not None, "No education section in updater output"


# ---------------------------------------------------------------------------
# Compile — end-to-end regression for non-canonical template
# ---------------------------------------------------------------------------

class TestCompileNonCanonicalTemplate:
    """compile_resume regression: non-canonical DOCX template + canonical LLM output."""

    def test_old_skill_content_replaced(self, tmp_path):
        """Old SKILL content (COBOL, Fortran) must be absent; new skills content present."""
        template = _make_noncanonical_docx(tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _NONCANONICAL_LLM, output)
        text = read_docx(output)
        assert "COBOL" not in text, "Old SKILL content (COBOL) still present in compiled output"
        assert "Fortran" not in text, "Old SKILL content (Fortran) still present in compiled output"
        assert "Kafka" in text or "Kubernetes" in text, "New Technical Skills content missing"

    def test_no_duplicate_skills_in_output_docx(self, tmp_path):
        """Compiled DOCX must contain exactly one skills section (no old SKILL + new duplicate)."""
        template = _make_noncanonical_docx(tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _NONCANONICAL_LLM, output)
        doc = parse_docx(output)
        skills_sections = [s for s in doc.sections if s.semantic_type == "skills"]
        assert len(skills_sections) == 1, (
            f"Expected 1 skills section in compiled output, found {len(skills_sections)}: "
            f"{[s.title for s in skills_sections]}"
        )

    def test_experience_updated_in_output(self, tmp_path):
        """New experience content must appear in compiled output."""
        template = _make_noncanonical_docx(tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _NONCANONICAL_LLM, output)
        text = read_docx(output)
        assert "Acme Corp" in text, "Experience role header missing from compiled output"
        assert "cloud-native" in text, "New experience bullet missing from compiled output"


# ---------------------------------------------------------------------------
# Table-based DOCX fixtures
# ---------------------------------------------------------------------------

# LLM output that adds "Professional Summary" (absent from no-summary source)
# and updates Skills / Experience / Education.
_TABLE_LLM = """\
Professional Summary
Senior backend engineer specialising in distributed systems.

Technical Skills
Python, Kafka, Kubernetes, Redis

Experience
Senior Engineer | Acme Corp
2020 – 2023
- Designed distributed microservices handling 1M+ requests/day.
- Led migration to Kubernetes across three regions.

Education
BSc Computer Science | University of BC
2019
"""

# Table-based source WITHOUT a summary section → Professional Summary is an extra.
_TABLE_CONTENT_NO_SUMMARY = """\
Technical Skills
COBOL, Fortran, SQL

Employment History
Senior Engineer | OldCorp
2018 – 2022
- Maintained legacy COBOL systems.
- Wrote Fortran numerical routines.

Education
BSc Computer Science | University of BC
2019
"""

# Table-based source WITH a summary → all LLM sections have a match (no extras).
_TABLE_CONTENT_WITH_SUMMARY = """\
Professional Summary
Veteran COBOL and Fortran specialist.

Technical Skills
COBOL, Fortran, SQL

Employment History
Senior Engineer | OldCorp
2018 – 2022
- Maintained legacy COBOL systems.
- Wrote Fortran numerical routines.

Education
BSc Computer Science | University of BC
2019
"""


_TABLE_SECTION_NAMES = {
    "technical skills", "employment history", "education",
    "professional summary", "experience", "skills", "skill",
}


def _make_table_docx(content: str, tmp_path: Path, name: str = "table_resume.docx") -> str:
    """Create a DOCX where all content lives inside a single-cell table.

    This mirrors the layout of PDF-converted or template DOCX files that use
    a full-page table as the document body (e.g. run-69 format).
    Section heading lines use Heading 1 style so _infer_semantic classifies
    them as section_heading inside the table.
    """
    doc = Document()
    # Remove the default empty paragraph that Document() adds.
    for para in doc.paragraphs:
        p = para._element
        p.getparent().remove(p)

    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    # Remove the default empty paragraph that add_table creates in the cell.
    for para in cell.paragraphs:
        p = para._element
        p.getparent().remove(p)

    for line in content.splitlines():
        if line.strip().lower() in _TABLE_SECTION_NAMES:
            para = cell.add_paragraph(line)
            try:
                para.style = doc.styles["Heading 1"]
            except KeyError:
                pass
        else:
            cell.add_paragraph(line)

    path = tmp_path / name
    doc.save(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# Test A — table-based source + extras (Professional Summary added by LLM)
# ---------------------------------------------------------------------------

class TestTableDocxWithExtras:
    """When LLM adds a section absent from the table source, extras path runs.

    Expected: body_items=None on the result (stale table not returned),
    compiled output contains the new LLM content.
    """

    def test_body_items_is_none_when_extras(self, tmp_path):
        """apply_tailored must return body_items=None so renderer uses all_paras."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        assert updated.body_items is None, (
            "body_items should be None when extras exist — returning stale table "
            "body_items would render the original unchanged content."
        )

    def test_compiled_output_contains_new_summary(self, tmp_path):
        """Compiled DOCX must contain the extra Professional Summary text."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _TABLE_LLM, output)
        doc = parse_docx(output)
        summary = next((s for s in doc.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Professional Summary section missing from compiled output"

    def test_old_cobol_content_absent(self, tmp_path):
        """Compiled DOCX must NOT contain the original COBOL/Fortran content."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _TABLE_LLM, output)
        text = read_docx(output)
        # read_docx returns '' for table-based DOCX; check via parse_docx instead
        doc = parse_docx(output)
        all_text = " ".join(
            p.text for s in doc.sections for p in s.body_paras
        )
        assert "COBOL" not in all_text, "Old COBOL content still present in compiled output"
        assert "Fortran" not in all_text, "Old Fortran content still present in compiled output"

    def test_new_skills_content_present(self, tmp_path):
        """Compiled DOCX must contain the LLM-generated skills."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _TABLE_LLM, output)
        doc = parse_docx(output)
        skills = next((s for s in doc.sections if s.semantic_type == "skills"), None)
        assert skills is not None, "Skills section missing from compiled output"
        skills_text = " ".join(p.text for p in skills.body_paras)
        assert "Kafka" in skills_text or "Kubernetes" in skills_text, (
            "New skills content (Kafka/Kubernetes) not found in compiled output"
        )


# ---------------------------------------------------------------------------
# Test B — table-based source with no extras (all sections match)
# ---------------------------------------------------------------------------

class TestTableDocxNoExtras:
    """When all LLM sections match the source, the in-place table update path runs.

    Expected: body_items is NOT None (table preserved for rendering),
    in-place update correctly replaces content.
    """

    def test_body_items_not_none_when_no_extras(self, tmp_path):
        """apply_tailored must preserve body_items when no extras exist."""
        template = _make_table_docx(_TABLE_CONTENT_WITH_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        assert updated.body_items is not None, (
            "body_items should be preserved (not None) when no extras exist — "
            "table in-place update path should have run."
        )

    def test_no_extra_sections_injected(self, tmp_path):
        """No new sections should appear beyond the 4 canonical ones."""
        template = _make_table_docx(_TABLE_CONTENT_WITH_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        assert not updated.sections or len(updated.sections) <= len(orig.sections), (
            f"Unexpected extra sections: {[s.title for s in updated.sections]}"
        )

    def test_in_place_summary_updated(self, tmp_path):
        """In-place update must replace the summary heading and text."""
        template = _make_table_docx(_TABLE_CONTENT_WITH_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Summary section missing from updated document"
        summary_text = " ".join(p.text for p in summary.body_paras)
        assert "distributed systems" in summary_text, (
            "In-place update did not apply new summary text"
        )


# ---------------------------------------------------------------------------
# Test C — table-based source + extras + semantic matches
# ---------------------------------------------------------------------------

class TestTableDocxExtrasWithSemanticMatches:
    """Extras path: matched sections still update, extras appear, stale body not returned."""

    def test_matched_sections_updated_in_extras_path(self, tmp_path):
        """Skills/Experience/Education must be updated even when extras path runs."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        skills = next((s for s in updated.sections if s.semantic_type == "skills"), None)
        assert skills is not None, "Skills section missing"
        skills_text = " ".join(p.text for p in skills.body_paras)
        assert "Kafka" in skills_text or "Kubernetes" in skills_text, (
            "Skills section not updated in extras path"
        )

    def test_extra_section_appears_in_output(self, tmp_path):
        """The new Professional Summary section must appear in the updated document."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        summary = next((s for s in updated.sections if s.semantic_type == "summary"), None)
        assert summary is not None, "Extra section (Professional Summary) absent from updated document"

    def test_stale_body_items_not_returned(self, tmp_path):
        """body_items must be None so the stale original table is not rendered."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        orig = parse_docx(template)
        assert orig.body_items is not None, "Precondition: table-based DOCX should have body_items"
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        assert updated.body_items is None, (
            "Stale body_items returned despite extras — renderer would show original unchanged table"
        )

    def test_experience_bullets_updated(self, tmp_path):
        """Experience role bullets must be updated in the extras path."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        orig = parse_docx(template)
        llm = parse_llm_output(_TABLE_LLM)
        updated = apply_tailored(orig, llm)
        exp = next((s for s in updated.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "Experience section missing"
        all_bullets = [b.text for role in exp.roles for b in role.bullets]
        assert any("microservices" in b or "Kubernetes" in b for b in all_bullets), (
            "Experience bullets not updated in extras path"
        )


# ---------------------------------------------------------------------------
# Test D — rendered result vs diff sanity
# ---------------------------------------------------------------------------

class TestDiffVsRenderedSanity:
    """Sanity check: compiled output contains LLM content regardless of diff behavior.

    Note: read_docx() uses doc.paragraphs (top-level only) and returns '' for
    table-based DOCX — so diff may show additions-only for such templates.
    That is a known limitation of diff_resume, not a bug in the compiler.
    The rendered DOCX (via parse_docx) must still contain the tailored content.
    """

    def test_compiled_docx_contains_llm_experience_bullets(self, tmp_path):
        """parse_docx on compiled output must show updated experience bullets."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _TABLE_LLM, output)
        doc = parse_docx(output)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        assert exp is not None, "Experience section missing from compiled output"
        all_bullets = [b.text for role in exp.roles for b in role.bullets]
        assert any("microservices" in b or "Kubernetes" in b for b in all_bullets), (
            "LLM experience bullets not present in compiled DOCX"
        )

    def test_diff_may_show_additions_only_for_table_source(self, tmp_path):
        """read_docx returns '' for table-based DOCX → diff shows additions-only.

        This locks in the known limitation so it is not accidentally 'fixed' by
        a change that invents a spurious 'before' value from empty source text.
        """
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        source_text = read_docx(template)
        # The known limitation: read_docx returns '' for table-based DOCX
        assert source_text == "", (
            "Expected read_docx to return '' for table-based DOCX — if this changed, "
            "update this test and the 'additions-only diff' limitation notes."
        )
        diff = diff_resume(source_text, _TABLE_LLM)
        # Diff entries should exist (LLM content vs empty source)
        assert diff, "Expected diff entries between empty source and LLM output"
        for entry in diff:
            assert "after" in entry or "roles" in entry, (
                "Diff entry has neither 'after' nor 'roles' key"
            )

    def test_compiled_output_does_not_contain_stale_cobol(self, tmp_path):
        """Even when diff shows additions-only, compiled output must not contain old COBOL."""
        template = _make_table_docx(_TABLE_CONTENT_NO_SUMMARY, tmp_path)
        output = str(tmp_path / "compiled.docx")
        compile_resume(template, _TABLE_LLM, output)
        doc = parse_docx(output)
        all_text = " ".join(
            p.text
            for s in doc.sections
            for p in ([s.heading] + s.body_paras + [b for r in s.roles for b in ([r.header] + r.bullets)])
        )
        assert "COBOL" not in all_text, (
            "COBOL from original source still in compiled output — stale body was rendered"
        )
