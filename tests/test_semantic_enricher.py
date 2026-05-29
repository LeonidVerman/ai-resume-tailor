"""
tests/test_semantic_enricher.py

Unit tests for the deterministic semantic enrichment layer.

Covers:
- tech_stack and highlight_header promotion via normalizer hints
- project_header detection (keyword, length, punctuation guards)
- role_intro detection (positional context + sentence structure)
- specialization_header detection (domain keywords, length, punct guards)
- priority ordering when multiple candidates match
- guardrail: non-paragraph semantics are never modified
- diagnostic records are emitted for every enrichment
"""
from __future__ import annotations

import pytest

from tailor.compiler.classification_models import (
    ClassificationInput,
    ClassificationParaInput,
    ClassificationRoleInput,
    ClassificationSectionInput,
)
from tailor.compiler.semantic_enricher import (
    ENRICH_HIGHLIGHT_HEADER,
    ENRICH_PROJECT_HEADER,
    ENRICH_ROLE_INTRO,
    ENRICH_SPECIALIZATION_HEADER,
    ENRICH_TECH_STACK,
    enrich_semantics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _para(
    para_id: str,
    text: str,
    parser_semantic: str = "paragraph",
    semantic_hint: str = "",
) -> ClassificationParaInput:
    return ClassificationParaInput(
        para_id=para_id,
        text=text,
        parser_semantic=parser_semantic,
        semantic_hint=semantic_hint,
    )


def _section(
    paras: list[ClassificationParaInput],
    roles: list[ClassificationRoleInput] | None = None,
    section_id: str = "sec_1",
    raw_title: str = "EXPERIENCE",
) -> ClassificationSectionInput:
    return ClassificationSectionInput(
        section_id=section_id,
        raw_title=raw_title,
        paragraphs=paras,
        roles=roles or [],
    )


def _doc(sections: list[ClassificationSectionInput]) -> ClassificationInput:
    return ClassificationInput(
        document_id="test_doc",
        source_kind="docx",
        sections=sections,
    )


def _enrich(paras, roles=None):
    """Convenience: enrich a single section, return (paras_out, diagnostics)."""
    result_ci, diags = enrich_semantics(_doc([_section(paras, roles)]))
    return result_ci.sections[0].paragraphs, diags


def _sem(paras, idx=0):
    return paras[idx].parser_semantic


# ---------------------------------------------------------------------------
# tech_stack
# ---------------------------------------------------------------------------

class TestTechStack:
    def test_promoted_from_hint(self):
        paras, _ = _enrich([
            _para("p1", "Tech Stack: Java, Python", semantic_hint="tech_stack_candidate"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_diagnostic_emitted(self):
        _, diags = _enrich([
            _para("p1", "Technologies: Go, Rust", semantic_hint="tech_stack_candidate"),
        ])
        assert any(d["event"] == ENRICH_TECH_STACK for d in diags)

    def test_confidence_in_diagnostic(self):
        _, diags = _enrich([
            _para("p1", "Key Technologies: AWS", semantic_hint="tech_stack_candidate"),
        ])
        ts = next(d for d in diags if d["event"] == ENRICH_TECH_STACK)
        assert ts["confidence"] >= 0.90

    def test_bullet_not_touched(self):
        paras, _ = _enrich([
            _para("p1", "Tech Stack: Java", parser_semantic="bullet",
                  semantic_hint="tech_stack_candidate"),
        ])
        assert _sem(paras) == "bullet"


# ---------------------------------------------------------------------------
# highlight_header
# ---------------------------------------------------------------------------

class TestHighlightHeader:
    def test_promoted_from_hint(self):
        paras, _ = _enrich([
            _para("p1", "Highlights: Led migration of core platform",
                  semantic_hint="highlight_candidate"),
        ])
        assert _sem(paras) == "highlight_header"

    def test_diagnostic_emitted(self):
        _, diags = _enrich([
            _para("p1", "Highlights: Built new auth service",
                  semantic_hint="highlight_candidate"),
        ])
        assert any(d["event"] == ENRICH_HIGHLIGHT_HEADER for d in diags)

    def test_only_hint_triggers(self):
        # Same text but no hint → stays paragraph
        paras, _ = _enrich([
            _para("p1", "Highlights: Some stuff"),
        ])
        # Without the hint the normalizer would add it; enricher alone sees no hint
        assert _sem(paras) == "paragraph"


# ---------------------------------------------------------------------------
# project_header
# ---------------------------------------------------------------------------

class TestProjectHeader:
    def test_detected_short_no_punct(self):
        paras, _ = _enrich([
            _para("p1", "OTA project"),
            _para("p2", "Application level: Java 11", parser_semantic="bullet"),
        ])
        assert _sem(paras, 0) == "project_header"

    def test_vehicle_simulation(self):
        paras, _ = _enrich([
            _para("p1", "Vehicle simulation project"),
            _para("p2", "Application level: Java 11", parser_semantic="bullet"),
        ])
        assert _sem(paras, 0) == "project_header"

    def test_confidence_higher_when_followed_by_bullet(self):
        _, diags_with = _enrich([
            _para("p1", "Migration project"),
            _para("p2", "Led team", parser_semantic="bullet"),
        ])
        _, diags_without = _enrich([
            _para("p1", "Migration project"),
        ])
        c_with = next(d["confidence"] for d in diags_with if d["event"] == ENRICH_PROJECT_HEADER)
        c_without = next(d["confidence"] for d in diags_without if d["event"] == ENRICH_PROJECT_HEADER)
        assert c_with > c_without

    def test_too_long_stays_paragraph(self):
        long_text = "This is a very long project description that exceeds sixty characters total"
        assert len(long_text) > 60
        paras, _ = _enrich([_para("p1", long_text)])
        assert _sem(paras) == "paragraph"

    def test_ends_with_period_stays_paragraph(self):
        paras, _ = _enrich([_para("p1", "OTA project.")])
        assert _sem(paras) == "paragraph"

    def test_no_project_keyword_stays_paragraph(self):
        paras, _ = _enrich([_para("p1", "OTA module")])
        assert _sem(paras) == "paragraph"

    def test_with_colon_not_project_header(self):
        paras, _ = _enrich([_para("p1", "OTA project: description")])
        assert _sem(paras) == "paragraph"

    def test_diagnostic_para_id_correct(self):
        _, diags = _enrich([_para("proj_42", "OTA project")])
        proj = next((d for d in diags if d["event"] == ENRICH_PROJECT_HEADER), None)
        assert proj is not None
        assert proj["para_id"] == "proj_42"


# ---------------------------------------------------------------------------
# role_intro
# ---------------------------------------------------------------------------

class TestRoleIntro:
    def test_from_intro_candidate_hint(self):
        paras, _ = _enrich([
            _para("p1", "*Tech lead on crypto exchange backend*",
                  semantic_hint="intro_candidate"),
        ])
        assert _sem(paras) == "role_intro"

    def test_sentence_after_role_header(self):
        paras, _ = _enrich([
            _para("p1", "Senior Software Engineer", parser_semantic="role_header"),
            _para("p2", "Led the development of a centralized crypto exchange backend."),
        ])
        assert _sem(paras, 1) == "role_intro"

    def test_sentence_after_role_meta(self):
        paras, _ = _enrich([
            _para("p1", "January 2022 – Present", parser_semantic="role_meta"),
            _para("p2", "Built microservice platform for financial data processing."),
        ])
        assert _sem(paras, 1) == "role_intro"

    def test_chained_role_intro(self):
        # Second sentence follows an already-enriched role_intro
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Led migration of critical partner services."),
            _para("p3", "Managed team of five engineers across two time zones."),
        ])
        assert _sem(paras, 1) == "role_intro"
        assert _sem(paras, 2) == "role_intro"

    def test_no_context_stays_paragraph(self):
        # A sentence with no role structural predecessor and not in meta_ids
        paras, _ = _enrich([
            _para("p1", "This is a complete sentence with enough words."),
        ])
        assert _sem(paras) == "paragraph"

    def test_in_meta_para_ids_provides_context(self):
        role = ClassificationRoleInput(
            role_id="role_1",
            header_para_ids=["p0"],
            meta_para_ids=["p1"],
            bullet_para_ids=[],
        )
        paras, _ = _enrich(
            [
                _para("p0", "Full Stack Software Engineer", parser_semantic="role_header"),
                _para("p1", "Developed Java applications for event-driven microservices."),
            ],
            roles=[role],
        )
        assert _sem(paras, 1) == "role_intro"

    def test_too_short_stays_paragraph(self):
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Full-time."),  # < 25 chars
        ])
        assert _sem(paras, 1) == "paragraph"

    def test_no_terminal_punctuation_stays_paragraph(self):
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Led migration of critical partner services"),  # no period
        ])
        assert _sem(paras, 1) == "paragraph"


# ---------------------------------------------------------------------------
# specialization_header
# ---------------------------------------------------------------------------

class TestSpecializationHeader:
    def test_backend_development(self):
        paras, _ = _enrich([
            _para("p1", "Backend Development"),
            _para("p2", "Java, Spring Boot", parser_semantic="bullet"),
        ])
        assert _sem(paras) == "specialization_header"

    def test_backend_and_fullstack(self):
        paras, _ = _enrich([
            _para("p1", "Backend & Full-Stack Development"),
            _para("p2", "Java, Python", parser_semantic="bullet"),
        ])
        assert _sem(paras) == "specialization_header"

    def test_devops_infrastructure(self):
        paras, _ = _enrich([
            _para("p1", "DevOps & Infrastructure"),
            _para("p2", "Docker, Kubernetes", parser_semantic="bullet"),
        ])
        assert _sem(paras) == "specialization_header"

    def test_cloud_architecture(self):
        paras, _ = _enrich([_para("p1", "Cloud Architecture")])
        assert _sem(paras) == "specialization_header"

    def test_digit_stays_paragraph(self):
        paras, _ = _enrich([_para("p1", "Java 8 Development")])
        assert _sem(paras) == "paragraph"

    def test_ends_with_period_stays_paragraph(self):
        paras, _ = _enrich([_para("p1", "Backend Development.")])
        assert _sem(paras) == "paragraph"

    def test_too_long_stays_paragraph(self):
        long_header = "Backend, Frontend, DevOps, Infrastructure, Cloud, Architecture and Distributed Systems"
        assert len(long_header) > 80
        paras, _ = _enrich([_para("p1", long_header)])
        assert _sem(paras) == "paragraph"

    def test_no_domain_keyword_stays_paragraph(self):
        paras, _ = _enrich([_para("p1", "Soft Skills")])
        assert _sem(paras) == "paragraph"

    def test_colon_stays_paragraph(self):
        # Colon means it's a tech_stack line — handled at higher priority
        paras, _ = _enrich([
            _para("p1", "Backend: Java, Spring", semantic_hint="tech_stack_candidate"),
        ])
        assert _sem(paras) == "tech_stack"  # won by tech_stack, not specialization_header


# ---------------------------------------------------------------------------
# Priority and guardrails
# ---------------------------------------------------------------------------

class TestPriorityAndGuardrails:
    def test_tech_stack_wins_over_role_intro(self):
        # tech_stack_candidate has higher confidence (0.95) than role_intro (0.82)
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Technologies: Java, Spring, Docker.",
                  semantic_hint="tech_stack_candidate"),
        ])
        assert _sem(paras, 1) == "tech_stack"

    def test_role_header_not_touched(self):
        paras, _ = _enrich([
            _para("p1", "Senior Software Engineer", parser_semantic="role_header"),
        ])
        assert _sem(paras) == "role_header"

    def test_bullet_not_touched(self):
        paras, _ = _enrich([
            _para("p1", "Implemented distributed caching layer.", parser_semantic="bullet"),
        ])
        assert _sem(paras) == "bullet"

    def test_role_meta_not_touched(self):
        paras, _ = _enrich([
            _para("p1", "January 2022 – Present", parser_semantic="role_meta"),
        ])
        assert _sem(paras) == "role_meta"

    def test_empty_text_not_touched(self):
        paras, _ = _enrich([_para("p1", "")])
        assert _sem(paras) == "paragraph"

    def test_no_false_positives_on_generic_sentence(self):
        paras, _ = _enrich([
            _para("p1", "Processed over one million records per day using batch jobs."),
        ])
        assert _sem(paras) == "paragraph"

    def test_multiple_sections_enriched_independently(self):
        sec1 = _section(
            [_para("p1", "OTA project")],
            section_id="sec_1", raw_title="EXPERIENCE",
        )
        sec2 = _section(
            [_para("p2", "Technologies: Java", semantic_hint="tech_stack_candidate")],
            section_id="sec_2", raw_title="SKILLS",
        )
        result_ci, diags = enrich_semantics(_doc([sec1, sec2]))
        assert result_ci.sections[0].paragraphs[0].parser_semantic == "project_header"
        assert result_ci.sections[1].paragraphs[0].parser_semantic == "tech_stack"
        assert len(diags) == 2

    def test_diagnostics_contain_required_fields(self):
        _, diags = _enrich([
            _para("para_x", "OTA project"),
        ])
        assert diags
        d = diags[0]
        assert "event" in d
        assert "para_id" in d
        assert "text" in d
        assert "confidence" in d
        assert d["confidence"] >= 0.80
