"""
tests/test_layout.py

Unit and integration tests for the layout-aware post-processing layer
(src/tailor/compiler/layout.py).

Coverage:
  - Template classification (linear vs table_sidebar)
  - Container extraction (capacity, region, subkind, is_narrow)
  - Additional redistribution (Languages / Certifications from Technical Skills)
  - Summary compaction (sentence-level)
  - Skills compaction (line-level tail-drop)
  - Fit estimation thresholds
  - Validation / guardrails
  - End-to-end: apply_layout_fitting with DOCX fixtures
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document

from tailor.compiler.docx_parser import parse_docx
from tailor.compiler.layout import (
    FitScore,
    TemplateContainer,
    apply_layout_fitting,
    classify_template,
    compact_experience_bullets,
    compact_skills,
    compact_summary,
    estimate_fit,
    extract_additional_subgroups,
    extract_containers,
    redistribute_additional,
    validate_llm_sections,
)
from tailor.compiler.models import ResumeDocument, TableBlock
from tailor.compiler.pipeline import compile_resume
from tailor.compiler.text_parser import LlmRole, LlmSection, parse_llm_output
from tailor.docx.template_fill import read_docx


# ---------------------------------------------------------------------------
# DOCX fixture helpers
# ---------------------------------------------------------------------------

_SIDEBAR_SECTION_NAMES = {
    "technical skills", "employment history", "education",
    "professional summary", "experience", "languages",
    "certifications and training",
}


def _make_linear_docx(tmp_path: Path) -> str:
    """Flat/paragraph-flow DOCX (no tables)."""
    doc = Document()
    doc.add_paragraph("Professional Summary", style="Heading 1")
    doc.add_paragraph("Experienced backend engineer.")
    doc.add_paragraph("Technical Skills", style="Heading 1")
    doc.add_paragraph("Python, Java, SQL")
    doc.add_paragraph("Experience", style="Heading 1")
    doc.add_paragraph("Engineer | Acme Corp")
    doc.add_paragraph("2020 – 2022")
    doc.add_paragraph("- Built APIs.")
    doc.add_paragraph("Education", style="Heading 1")
    doc.add_paragraph("BSc CS | UBC")
    doc.add_paragraph("2019")
    path = tmp_path / "linear.docx"
    doc.save(str(path))
    return str(path)


def _make_sidebar_docx(tmp_path: Path) -> str:
    """Table-based DOCX with sidebar-like short sections."""
    doc = Document()
    for para in doc.paragraphs:
        para._element.getparent().remove(para._element)

    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    for para in cell.paragraphs:
        para._element.getparent().remove(para._element)

    def add(text: str, heading: bool = False):
        p = cell.add_paragraph(text)
        if heading:
            try:
                p.style = doc.styles["Heading 1"]
            except KeyError:
                pass

    # Narrow sidebar-like sections
    add("Professional Summary", heading=True)
    add("Senior engineer.")
    add("Technical Skills", heading=True)
    add("Python, Java")
    add("AWS, Docker")
    add("Employment History", heading=True)
    add("Engineer | OldCorp")
    add("2020 – 2022")
    add("- Built legacy systems.")
    add("Education", heading=True)
    add("BSc CS | UBC")
    add("2019")
    add("Languages", heading=True)
    add("English, French")
    add("Certifications and Training", heading=True)
    add("AWS Certified 2021")

    path = tmp_path / "sidebar.docx"
    doc.save(str(path))
    return str(path)


def _make_narrow_summary_docx(tmp_path: Path) -> str:
    """Table-based DOCX with a very short (1-line) summary container."""
    doc = Document()
    for para in doc.paragraphs:
        para._element.getparent().remove(para._element)

    table = doc.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    for para in cell.paragraphs:
        para._element.getparent().remove(para._element)

    def add(text: str, heading: bool = False):
        p = cell.add_paragraph(text)
        if heading:
            try:
                p.style = doc.styles["Heading 1"]
            except KeyError:
                pass

    add("Professional Summary", heading=True)
    add("Brief summary here.")          # very short: 1 line, <40 chars
    add("Technical Skills", heading=True)
    add("Python, Java")
    add("Employment History", heading=True)
    add("Engineer | OldCorp")
    add("2020 – 2022")
    add("- Built systems.")

    path = tmp_path / "narrow_summary.docx"
    doc.save(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# LLM fixtures
# ---------------------------------------------------------------------------

_LLM_CLEAN = """\
Professional Summary
Senior backend engineer specialising in distributed systems.

Technical Skills
Python, Kafka, Kubernetes

Experience
Engineer | Acme Corp
2020 – 2022
- Designed APIs.
- Led deployment.

Education
BSc CS | UBC
2019
"""

_LLM_WITH_LABELED_ADDITIONAL = """\
Professional Summary
Senior backend engineer.

Technical Skills
Python, Kafka, Kubernetes
Languages: English, French
Certifications: AWS Certified

Experience
Engineer | Acme Corp
2020 – 2022
- Built distributed services.

Education
BSc CS | UBC
2019
"""

_LLM_LONG_SUMMARY = """\
Professional Summary
First sentence about experience. Second sentence about skills. Third sentence about achievements. Fourth sentence about leadership. Fifth sentence closing the pitch.

Technical Skills
Python, Java, Kafka

Experience
Engineer | Acme Corp
2020 – 2022
- Built services.

Education
BSc CS | UBC
2019
"""

_LLM_MANY_SKILLS = """\
Professional Summary
Senior engineer.

Technical Skills
Line 1: Python, Java
Line 2: AWS, Docker, Kubernetes
Line 3: MySQL, PostgreSQL, Redis
Line 4: Kafka, RabbitMQ
Line 5: REST, GraphQL
Line 6: CI/CD, Jenkins
Line 7: Linux, Bash
Line 8: Git, GitHub
Line 9: TypeScript, React
Line 10: Terraform, Ansible

Experience
Engineer | Acme Corp
2020 – 2022
- Built systems.

Education
BSc CS | UBC
2019
"""


# ---------------------------------------------------------------------------
# TestClassifyTemplate
# ---------------------------------------------------------------------------

class TestClassifyTemplate:
    def test_linear_docx_is_linear(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        assert classify_template(original) == "linear"

    def test_table_docx_is_table_sidebar(self, tmp_path):
        original = parse_docx(_make_sidebar_docx(tmp_path))
        assert classify_template(original) == "table_sidebar"

    def test_no_body_items_is_linear(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        # Simulate a deserialized IR (body_items=None)
        from dataclasses import replace
        no_items = ResumeDocument(
            header_paras=original.header_paras,
            sections=original.sections,
            layout=original.layout,
            all_paras=original.all_paras,
            body_items=None,
        )
        assert classify_template(no_items) == "linear"


# ---------------------------------------------------------------------------
# TestExtractContainers
# ---------------------------------------------------------------------------

class TestExtractContainers:
    def test_linear_containers_have_correct_semantic_types(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        containers = extract_containers(original)
        types = {c.semantic_type for c in containers}
        assert "experience" in types
        assert "skills" in types

    def test_sidebar_has_language_container(self, tmp_path):
        original = parse_docx(_make_sidebar_docx(tmp_path))
        containers = extract_containers(original)
        lang = [c for c in containers if c.subkind == "languages"]
        assert lang, "Expected a 'languages' subkind container"
        assert lang[0].region == "sidebar"

    def test_sidebar_has_cert_container(self, tmp_path):
        original = parse_docx(_make_sidebar_docx(tmp_path))
        containers = extract_containers(original)
        cert = [c for c in containers if c.subkind == "certifications"]
        assert cert, "Expected a 'certifications' subkind container"

    def test_experience_is_main_region(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        containers = extract_containers(original)
        exp = next(c for c in containers if c.semantic_type == "experience")
        assert exp.region == "main"

    def test_summary_is_main_region(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        containers = extract_containers(original)
        summ = next(c for c in containers if c.semantic_type == "summary")
        assert summ.region == "main"

    def test_narrow_flag_on_short_section(self, tmp_path):
        original = parse_docx(_make_narrow_summary_docx(tmp_path))
        containers = extract_containers(original)
        summ = next((c for c in containers if c.semantic_type == "summary"), None)
        assert summ is not None
        assert summ.is_narrow, "1-line summary container should be flagged narrow"

    def test_orig_para_count_matches_body_lines(self, tmp_path):
        original = parse_docx(_make_linear_docx(tmp_path))
        containers = extract_containers(original)
        skills = next(c for c in containers if c.semantic_type == "skills")
        # Linear fixture has 1 non-empty skills line: "Python, Java, SQL"
        assert skills.orig_para_count >= 1


# ---------------------------------------------------------------------------
# TestExtractAdditionalSubgroups
# ---------------------------------------------------------------------------

class TestExtractAdditionalSubgroups:
    def test_languages_extracted(self):
        lines = ["Python, Java", "Languages: English, French"]
        clean, subs = extract_additional_subgroups(lines)
        assert clean == ["Python, Java"]
        assert subs == {"languages": ["English, French"]}

    def test_certifications_extracted(self):
        lines = ["AWS, Docker", "Certifications: AWS Certified"]
        clean, subs = extract_additional_subgroups(lines)
        assert "certifications" in subs
        assert subs["certifications"] == ["AWS Certified"]
        assert "AWS, Docker" in clean

    def test_multiple_subgroups_in_same_section(self):
        lines = [
            "Python",
            "Languages: English",
            "Certifications: Oracle",
            "Awards: Dean's List",
        ]
        clean, subs = extract_additional_subgroups(lines)
        assert clean == ["Python"]
        assert set(subs.keys()) == {"languages", "certifications", "awards"}

    def test_no_labels_unchanged(self):
        lines = ["Python, Java", "AWS, Docker"]
        clean, subs = extract_additional_subgroups(lines)
        assert clean == lines
        assert subs == {}

    def test_empty_value_after_label_is_skipped(self):
        lines = ["Languages:  "]
        clean, subs = extract_additional_subgroups(lines)
        # Empty value → not added to subgroups
        assert "languages" not in subs or subs.get("languages") == []

    def test_case_insensitive_label(self):
        lines = ["LANGUAGES: English"]
        _, subs = extract_additional_subgroups(lines)
        assert "languages" in subs

    def test_colon_variants(self):
        lines = ["Certifications：AWS"]  # full-width colon
        _, subs = extract_additional_subgroups(lines)
        assert "certifications" in subs


# ---------------------------------------------------------------------------
# TestRedistributeAdditional
# ---------------------------------------------------------------------------

class TestRedistributeAdditional:
    def _containers(self, tmp_path):
        return extract_containers(parse_docx(_make_sidebar_docx(tmp_path)))

    def test_languages_removed_from_skills(self, tmp_path):
        containers = self._containers(tmp_path)
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = redistribute_additional(llm, containers)
        skills = next(s for s in result if s.semantic_type == "skills")
        for line in skills.body_lines:
            assert not line.strip().lower().startswith("languages"), (
                "Languages line still in Technical Skills after redistribution"
            )

    def test_certifications_removed_from_skills(self, tmp_path):
        containers = self._containers(tmp_path)
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = redistribute_additional(llm, containers)
        skills = next(s for s in result if s.semantic_type == "skills")
        for line in skills.body_lines:
            assert not line.strip().lower().startswith("certifications"), (
                "Certifications line still in Technical Skills after redistribution"
            )

    def test_dedicated_languages_section_locked_stays_in_skills(self, tmp_path):
        """Languages/Certs containers are locked (verbatim-only).
        Labeled content from Technical Skills must stay there (label stripped),
        not be routed to the locked container where it would be discarded.
        """
        containers = self._containers(tmp_path)
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = redistribute_additional(llm, containers)
        # No new Languages LLM section should be injected
        injected = [s for s in result if s.semantic_type == "languages"]
        assert not injected, (
            "No Languages LLM section should be injected when the container is locked"
        )
        # Extracted value must survive in Technical Skills (label stripped)
        skills = next(s for s in result if s.semantic_type == "skills")
        all_text = " ".join(skills.body_lines)
        assert "English" in all_text or "French" in all_text, (
            "Extracted languages value must remain in Technical Skills when container is locked"
        )

    def test_dedicated_cert_section_locked_stays_in_skills(self, tmp_path):
        """Certifications container is locked; extracted value must stay in Technical Skills."""
        containers = self._containers(tmp_path)
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = redistribute_additional(llm, containers)
        # No new Certifications LLM section should be injected
        injected = [
            s for s in result
            if s.semantic_type == "certifications"
        ]
        assert not injected, (
            "No Certifications LLM section should be injected when the container is locked"
        )
        # Extracted cert value must survive in Technical Skills (label stripped)
        skills = next(s for s in result if s.semantic_type == "skills")
        all_text = " ".join(skills.body_lines)
        assert "AWS Certified" in all_text, (
            "Extracted certifications value must remain in Technical Skills when container is locked"
        )

    def test_no_container_for_subkind_falls_back_to_skills(self):
        """When no dedicated container exists, extracted value stays in skills."""
        # Build a minimal linear document with no Languages/Cert containers
        from tailor.compiler.models import LayoutProfile, ResumeSection, ParaModel, ParaStyle
        from lxml import etree
        from copy import deepcopy
        dummy_style = ParaStyle(style_name=None, alignment=None, indent_left=None,
                                indent_right=None, hanging=None, spacing_before=None,
                                spacing_after=None, line_spacing=None, keep_with_next=None,
                                numbering=None, bold=None, italic=None, font_name=None,
                                font_size_pt=None, color=None,
                                xml_proto=deepcopy(etree.fromstring("<w:p xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'/>")))
        dummy_para = ParaModel(text="Python", style=dummy_style, semantic="paragraph")
        skills_sec = ResumeSection(
            title="Technical Skills",
            heading=ParaModel(text="Technical Skills", style=dummy_style, semantic="section_heading"),
            semantic_type="skills",
            body_paras=[dummy_para],
        )
        layout = LayoutProfile(page_width_pt=612, page_height_pt=792,
                               margin_top_pt=72, margin_bottom_pt=72,
                               margin_left_pt=72, margin_right_pt=72,
                               default_font_name="Calibri", default_font_size_pt=11.0)
        doc = ResumeDocument(
            header_paras=[], sections=[skills_sec], layout=layout, all_paras=[dummy_para]
        )
        containers = extract_containers(doc)
        # No Languages or Certifications container exists

        llm = [LlmSection(
            heading="Technical Skills",
            semantic_type="skills",
            body_lines=["Python", "Languages: English"],
        )]
        result = redistribute_additional(llm, containers)
        skills_result = next(s for s in result if s.semantic_type == "skills")
        all_text = " ".join(skills_result.body_lines)
        # Value should be returned to skills (without the label prefix)
        assert "English" in all_text

    def test_no_skills_section_returns_unchanged(self, tmp_path):
        containers = self._containers(tmp_path)
        llm = [LlmSection(heading="Experience", semantic_type="experience", body_lines=[])]
        result = redistribute_additional(llm, containers)
        assert result == llm

    def test_no_labeled_lines_returns_unchanged(self, tmp_path):
        containers = self._containers(tmp_path)
        llm = parse_llm_output(_LLM_CLEAN)
        original_skills_lines = next(s for s in llm if s.semantic_type == "skills").body_lines
        result = redistribute_additional(llm, containers)
        result_skills_lines = next(s for s in result if s.semantic_type == "skills").body_lines
        assert result_skills_lines == original_skills_lines

    def test_languages_label_in_skills_stays_in_skills_when_locked(self, tmp_path):
        """When the dedicated container is locked, 'Languages: ...' stays in Technical Skills.

        The LLM Languages section (if present in the LLM output) is kept as-is;
        the labeled value from Technical Skills is NOT merged into it.
        """
        containers = self._containers(tmp_path)
        llm = [
            LlmSection(
                heading="Technical Skills",
                semantic_type="skills",
                body_lines=["Python", "Languages: Spanish"],
            ),
            LlmSection(
                heading="Languages",
                semantic_type="other",
                body_lines=["English"],
            ),
        ]
        result = redistribute_additional(llm, containers)
        # Spanish must stay in Technical Skills (label stripped)
        skills = next(s for s in result if s.semantic_type == "skills")
        assert "Spanish" in " ".join(skills.body_lines), (
            "Spanish must remain in Technical Skills when the Languages container is locked"
        )
        # The LLM's Languages section must be unchanged
        lang = next(s for s in result if "languages" in s.heading.lower())
        assert "English" in " ".join(lang.body_lines)


# ---------------------------------------------------------------------------
# TestCompactSummary
# ---------------------------------------------------------------------------

class TestCompactSummary:
    def test_within_limit_unchanged(self):
        lines = ["First sentence. Second sentence. Third sentence."]
        result = compact_summary(lines, max_sentences=3)
        assert result == lines

    def test_exceeds_limit_is_reduced(self):
        lines = [
            "First sentence. Second sentence. Third sentence. Fourth sentence. Fifth sentence."
        ]
        result = compact_summary(lines, max_sentences=3)
        assert len(result) == 1
        # Should contain the first 2 + last sentence
        text = result[0]
        assert "First sentence" in text
        assert "Fifth sentence" in text

    def test_preserves_first_and_last_sentence(self):
        lines = ["A. B. C. D. E."]
        result = compact_summary(lines, max_sentences=3)
        text = result[0]
        assert text.startswith("A")
        assert text.endswith("E.")

    def test_multi_line_input_joined_then_split(self):
        lines = ["First sentence.", "Second sentence.", "Third sentence.", "Fourth sentence."]
        result = compact_summary(lines, max_sentences=3)
        sentences_in_result = [s for s in result[0].split(". ") if s]
        assert len(sentences_in_result) <= 3

    def test_exactly_max_sentences_unchanged(self):
        lines = ["A. B. C."]
        result = compact_summary(lines, max_sentences=3)
        assert result == lines

    def test_empty_lines_ignored(self):
        lines = ["", "First. Second. Third. Fourth.", ""]
        result = compact_summary(lines, max_sentences=3)
        assert result  # should produce something


# ---------------------------------------------------------------------------
# TestCompactSkills
# ---------------------------------------------------------------------------

class TestCompactSkills:
    def test_within_target_unchanged(self):
        lines = ["Line 1", "Line 2", "Line 3"]
        assert compact_skills(lines, target_count=5) == lines

    def test_exceeds_target_truncated(self):
        lines = ["L1", "L2", "L3", "L4", "L5", "L6", "L7"]
        result = compact_skills(lines, target_count=4)
        assert result == ["L1", "L2", "L3", "L4"]

    def test_empty_lines_not_counted(self):
        lines = ["L1", "", "L2", "", "L3", "L4", "L5"]
        result = compact_skills(lines, target_count=3)
        # Only non-empty lines are counted and returned
        assert len(result) == 3
        assert "" not in result

    def test_exactly_target_unchanged(self):
        lines = ["A", "B", "C"]
        assert compact_skills(lines, target_count=3) == lines

    def test_drops_trailing_not_leading(self):
        lines = ["Important 1", "Important 2", "Less important", "Even less important"]
        result = compact_skills(lines, target_count=2)
        assert "Important 1" in result
        assert "Important 2" in result
        assert "Less important" not in result


# ---------------------------------------------------------------------------
# TestCompactExperienceBullets
# ---------------------------------------------------------------------------

class TestCompactExperienceBullets:
    def _container(self, bullets_per_role: list[int]) -> TemplateContainer:
        return TemplateContainer(
            section_idx=0, title="Experience", semantic_type="experience",
            subkind="", region="main",
            orig_para_count=sum(1 + b for b in bullets_per_role),
            orig_char_count=500, is_narrow=False,
            orig_bullets_per_role=bullets_per_role,
            orig_role_count=len(bullets_per_role),
        )

    def _llm_role(self, n_bullets: int) -> LlmRole:
        return LlmRole(
            header="Engineer | Corp",
            bullets=[f"Bullet {i}." for i in range(n_bullets)],
        )

    def test_trims_to_orig_density(self):
        result = compact_experience_bullets(
            [self._llm_role(6)], self._container([3]), compact_template=True
        )
        assert len(result[0].bullets) == 3

    def test_zero_orig_count_skips_trimming(self):
        # orig=0 means the template parser couldn't attribute bullets to the
        # role — density unknown, all LLM bullets must survive (samples 15/17).
        result = compact_experience_bullets(
            [self._llm_role(4)], self._container([0]), compact_template=True
        )
        assert len(result[0].bullets) == 4

    def test_all_zero_counts_skip_unmatched_roles_too(self):
        # avg of all-zero counts is 0 → extra LLM roles are not trimmed either
        result = compact_experience_bullets(
            [self._llm_role(4), self._llm_role(3)],
            self._container([0]), compact_template=True,
        )
        assert [len(r.bullets) for r in result] == [4, 3]


# ---------------------------------------------------------------------------
# TestFitEstimation
# ---------------------------------------------------------------------------

class TestFitEstimation:
    def _narrow_container(self) -> TemplateContainer:
        return TemplateContainer(
            section_idx=0, title="Skills", semantic_type="skills",
            subkind="", region="sidebar",
            orig_para_count=3, orig_char_count=50, is_narrow=True,
        )

    def _wide_container(self) -> TemplateContainer:
        return TemplateContainer(
            section_idx=0, title="Experience", semantic_type="experience",
            subkind="", region="main",
            orig_para_count=10, orig_char_count=500, is_narrow=False,
        )

    def _skills_section(self, lines: list[str]) -> LlmSection:
        return LlmSection(heading="Skills", semantic_type="skills", body_lines=lines)

    def test_low_risk_within_narrow_threshold(self):
        s = self._skills_section(["A", "B", "C"])   # same as orig_para_count=3
        score = estimate_fit(self._narrow_container(), s)
        assert score.risk == "low"

    def test_high_risk_far_exceeds_narrow(self):
        s = self._skills_section(["A", "B", "C", "D", "E", "F", "G"])  # 7 vs 3
        score = estimate_fit(self._narrow_container(), s)
        assert score.risk in ("medium", "high")

    def test_wide_container_tolerates_more(self):
        # 15 paras vs orig_para_count=10 → ratio=1.5, below wide threshold of 1.8
        s = self._skills_section([f"Line {i}" for i in range(15)])
        score = estimate_fit(self._wide_container(), s)
        assert score.risk == "low"

    def test_char_ratio_computed(self):
        s = self._skills_section(["x" * 200])
        score = estimate_fit(self._narrow_container(), s)
        assert score.char_ratio > 1.0


# ---------------------------------------------------------------------------
# TestValidateLlmSections
# ---------------------------------------------------------------------------

class TestValidateLlmSections:
    def _no_containers(self) -> list[TemplateContainer]:
        return []

    def test_internal_marker_flagged(self):
        llm = [LlmSection(
            heading="Summary", semantic_type="summary",
            body_lines=["Experienced engineer. CURRENT_DATE."],
        )]
        warnings = validate_llm_sections(llm, self._no_containers())
        assert any("CURRENT_DATE" in w for w in warnings)

    def test_duplicate_semantic_type_flagged(self):
        llm = [
            LlmSection(heading="Summary", semantic_type="summary", body_lines=["A"]),
            LlmSection(heading="Professional Summary", semantic_type="summary", body_lines=["B"]),
        ]
        warnings = validate_llm_sections(llm, self._no_containers())
        assert any("summary" in w.lower() for w in warnings)

    def test_clean_output_no_warnings(self):
        llm = [
            LlmSection(heading="Summary", semantic_type="summary", body_lines=["Clean."]),
            LlmSection(heading="Skills", semantic_type="skills", body_lines=["Python"]),
        ]
        assert validate_llm_sections(llm, self._no_containers()) == []

    def test_languages_in_skills_with_dedicated_container_flagged(self, tmp_path):
        original = parse_docx(_make_sidebar_docx(tmp_path))
        containers = extract_containers(original)
        llm = [LlmSection(
            heading="Technical Skills", semantic_type="skills",
            body_lines=["Python", "Languages: English"],
        )]
        warnings = validate_llm_sections(llm, containers)
        assert any("Languages" in w for w in warnings)

    def test_curly_brace_marker_flagged(self):
        llm = [LlmSection(
            heading="Skills", semantic_type="skills",
            body_lines=["{{placeholder}} Python"],
        )]
        warnings = validate_llm_sections(llm, self._no_containers())
        assert any("{{" in w for w in warnings)


# ---------------------------------------------------------------------------
# TestApplyLayoutFitting — end-to-end integration
# ---------------------------------------------------------------------------

class TestApplyLayoutFitting:
    """End-to-end tests: apply_layout_fitting normalises LLM sections correctly."""

    def test_linear_template_passthrough(self, tmp_path):
        """Linear templates: sections should pass through unchanged."""
        original = parse_docx(_make_linear_docx(tmp_path))
        llm = parse_llm_output(_LLM_CLEAN)
        result = apply_layout_fitting(original, llm)
        # Section count may change only if redistribution added something
        assert any(s.semantic_type == "experience" for s in result)
        assert any(s.semantic_type == "skills" for s in result)

    def test_sidebar_lang_redistribution(self, tmp_path):
        """Sidebar template: Languages extracted from Technical Skills."""
        original = parse_docx(_make_sidebar_docx(tmp_path))
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = apply_layout_fitting(original, llm)
        skills = next(s for s in result if s.semantic_type == "skills")
        for line in skills.body_lines:
            assert not line.strip().lower().startswith("languages"), (
                "Languages still in Technical Skills after apply_layout_fitting"
            )

    def test_sidebar_cert_redistribution(self, tmp_path):
        """Sidebar template: Certifications extracted from Technical Skills."""
        original = parse_docx(_make_sidebar_docx(tmp_path))
        llm = parse_llm_output(_LLM_WITH_LABELED_ADDITIONAL)
        result = apply_layout_fitting(original, llm)
        skills = next(s for s in result if s.semantic_type == "skills")
        for line in skills.body_lines:
            assert not line.strip().lower().startswith("certif"), (
                "Certifications still in Technical Skills after apply_layout_fitting"
            )

    def test_oversized_summary_compacted_in_narrow_container(self, tmp_path):
        """Narrow summary container: long summary is compacted."""
        original = parse_docx(_make_narrow_summary_docx(tmp_path))
        llm = parse_llm_output(_LLM_LONG_SUMMARY)
        result = apply_layout_fitting(original, llm)
        summary = next(s for s in result if s.semantic_type == "summary")
        full_text = " ".join(l for l in summary.body_lines if l.strip())
        sentences = [s for s in full_text.split(". ") if s.strip()]
        assert len(sentences) <= 3, (
            f"Summary not compacted: got {len(sentences)} sentences in narrow container"
        )

    def test_oversized_skills_compacted_in_sidebar(self, tmp_path):
        """Sidebar template: excessive skills lines are compacted."""
        original = parse_docx(_make_sidebar_docx(tmp_path))
        # Source has 2 skills lines; LLM outputs 10 → should compact
        llm = parse_llm_output(_LLM_MANY_SKILLS)
        result = apply_layout_fitting(original, llm)
        skills = next(s for s in result if s.semantic_type == "skills")
        non_empty = [l for l in skills.body_lines if l.strip()]
        # After compaction, should not massively exceed source capacity
        # Source had 2 lines → target = max(2, 3) = 3
        assert len(non_empty) <= 6, (
            f"Skills not compacted: {len(non_empty)} lines remain in sidebar container"
        )

    def test_experience_not_compacted(self, tmp_path):
        """Experience sections should be passed through unchanged."""
        original = parse_docx(_make_linear_docx(tmp_path))
        llm = parse_llm_output(_LLM_CLEAN)
        result = apply_layout_fitting(original, llm)
        exp = next(s for s in result if s.semantic_type == "experience")
        assert exp.roles  # roles preserved

    def test_returns_list_of_llm_sections(self, tmp_path):
        """Return type must be list[LlmSection]."""
        original = parse_docx(_make_linear_docx(tmp_path))
        llm = parse_llm_output(_LLM_CLEAN)
        result = apply_layout_fitting(original, llm)
        assert isinstance(result, list)
        assert all(isinstance(s, LlmSection) for s in result)


# ---------------------------------------------------------------------------
# TestCompileIntegration — pipeline-level regression tests
# ---------------------------------------------------------------------------

class TestCompileIntegration:
    """compile_resume now includes apply_layout_fitting; smoke-test the full path."""

    def test_sidebar_compile_no_lang_in_skills(self, tmp_path):
        """Compiled DOCX must not have Languages embedded in Technical Skills."""
        template = _make_sidebar_docx(tmp_path)
        output = str(tmp_path / "out.docx")
        compile_resume(template, _LLM_WITH_LABELED_ADDITIONAL, output)
        doc = parse_docx(output)
        skills = next((s for s in doc.sections if s.semantic_type == "skills"), None)
        assert skills is not None
        skills_text = " ".join(p.text for p in skills.body_paras)
        # Languages content should not be in the skills section text
        assert "Languages:" not in skills_text

    def test_sidebar_compile_dedicated_lang_section_populated(self, tmp_path):
        """Compiled DOCX Languages section must contain the redistributed text."""
        template = _make_sidebar_docx(tmp_path)
        output = str(tmp_path / "out.docx")
        compile_resume(template, _LLM_WITH_LABELED_ADDITIONAL, output)
        doc = parse_docx(output)
        lang = next(
            (s for s in doc.sections if "languages" in s.title.lower()), None
        )
        assert lang is not None, "No Languages section in compiled output"
        lang_text = " ".join(p.text for p in lang.body_paras)
        assert "English" in lang_text or "French" in lang_text

    def test_linear_compile_clean_passthrough(self, tmp_path):
        """Linear template with clean LLM output compiles without error."""
        template = _make_linear_docx(tmp_path)
        output = str(tmp_path / "out.docx")
        compile_resume(template, _LLM_CLEAN, output)
        doc = parse_docx(output)
        assert any(s.semantic_type == "experience" for s in doc.sections)
