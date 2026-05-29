"""
tests/test_semantic_enricher.py

Unit tests for the deterministic semantic enrichment layer (Rounds 1 & 2).

Covers:
- tech_stack and highlight_header promotion via normalizer hints
- tech_stack_detail: extended tech-layer patterns (Application level:, etc.)
- project_header detection (keyword, length, punctuation guards)
- project_entry: hyperlinked entries in Projects sections
- project_intro (internal project): long project descriptions in role body
- role_intro detection (positional context + sentence structure)
- specialization_header detection (domain keywords, length, punct guards)
- priority ordering when multiple candidates match
- guardrail: non-paragraph semantics are never modified
- diagnostic records contain required fields including original/enriched semantic
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
    ENRICH_INTERNAL_PROJECT,
    ENRICH_PROJECT_ENTRY,
    ENRICH_PROJECT_HEADER,
    ENRICH_ROLE_INTRO,
    ENRICH_SPECIALIZATION_HEADER,
    ENRICH_TECH_STACK,
    ENRICH_TECH_STACK_DETAIL,
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


def _role(
    role_id: str,
    header_ids: list[str] | None = None,
    meta_ids: list[str] | None = None,
    bullet_ids: list[str] | None = None,
) -> ClassificationRoleInput:
    return ClassificationRoleInput(
        role_id=role_id,
        header_para_ids=header_ids or [],
        meta_para_ids=meta_ids or [],
        bullet_para_ids=bullet_ids or [],
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


def _enrich(paras, roles=None, raw_title="EXPERIENCE"):
    """Convenience: enrich a single section, return (paras_out, diagnostics)."""
    result_ci, diags = enrich_semantics(_doc([_section(paras, roles, raw_title=raw_title)]))
    return result_ci.sections[0].paragraphs, diags


def _sem(paras, idx=0):
    return paras[idx].parser_semantic


# ---------------------------------------------------------------------------
# tech_stack (hint-based, Round 1)
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
# tech_stack_detail (extended patterns, Round 2)
# ---------------------------------------------------------------------------

class TestTechStackDetail:
    def test_application_level_promoted(self):
        paras, _ = _enrich([
            _para("p1", "Application level: Java 8-11, Spring Boot, Kotlin"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_monitoring_and_metrics(self):
        paras, _ = _enrich([
            _para("p1", "Monitoring and metrics: Prometheus, Grafana, Spring Cloud Eureka"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_persistence_level(self):
        paras, _ = _enrich([
            _para("p1", "Persistence level: Spring Data, PostgreSQL, Hibernate"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_containers(self):
        paras, _ = _enrich([
            _para("p1", "Containers: Amazon EKS, Kubernetes, Docker"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_cache(self):
        paras, _ = _enrich([
            _para("p1", "Cache: Redis, Memcached"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_auth(self):
        paras, _ = _enrich([
            _para("p1", "Auth: OAuth2, JWT, Keycloak"),
        ])
        assert _sem(paras) == "tech_stack"

    def test_diagnostic_event_is_detail(self):
        _, diags = _enrich([
            _para("p1", "Application level: Java 8, Spring Boot"),
        ])
        assert any(d["event"] == ENRICH_TECH_STACK_DETAIL for d in diags)
        # event is detail but enriched semantic is still tech_stack
        detail_diag = next(d for d in diags if d["event"] == ENRICH_TECH_STACK_DETAIL)
        assert detail_diag["enriched_semantic"] == "tech_stack"

    def test_availability_not_tech_stack(self):
        # "Availability" is not in the tech-layer keyword set
        paras, _ = _enrich([
            _para("p1", "Availability: Open to Remote positions globally"),
        ])
        assert _sem(paras) == "paragraph"

    def test_location_not_tech_stack(self):
        paras, _ = _enrich([
            _para("p1", "Location: New York, USA"),
        ])
        assert _sem(paras) == "paragraph"

    def test_hint_wins_over_layer_pattern(self):
        # A para with tech_stack_candidate hint gets ENRICH_TECH_STACK (0.95)
        # not ENRICH_TECH_STACK_DETAIL (0.90), but both result in tech_stack
        _, diags = _enrich([
            _para("p1", "Technologies: Java, Python", semantic_hint="tech_stack_candidate"),
        ])
        events = [d["event"] for d in diags]
        assert ENRICH_TECH_STACK in events
        assert ENRICH_TECH_STACK_DETAIL not in events

    def test_colon_beyond_40_chars_not_tech_stack(self):
        # Colon position > 40 chars in key → not a tech-layer line
        paras, _ = _enrich([
            _para("p1", "This is a very long sentence that eventually has a colon: value"),
        ])
        assert _sem(paras) == "paragraph"


# ---------------------------------------------------------------------------
# highlight_header (Round 1)
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
        paras, _ = _enrich([_para("p1", "Highlights: Some stuff")])
        assert _sem(paras) == "paragraph"


# ---------------------------------------------------------------------------
# project_entry (Round 2 — Projects section, hyperlinked entries)
# ---------------------------------------------------------------------------

class TestProjectEntry:
    def test_a_link_in_projects_section(self):
        paras, _ = _enrich(
            [_para("p1", "a link Apache Incubator – NLPCraft, API to NLP, 2020")],
            raw_title="Projects",
        )
        assert _sem(paras) == "project_entry"

    def test_a_link_in_technical_projects(self):
        paras, _ = _enrich(
            [_para("p1", "a link Bachelor Thesis, Learning similarity, 2019")],
            raw_title="TECHNICAL PROJECTS",
        )
        assert _sem(paras) == "project_entry"

    def test_diagnostic_emitted(self):
        _, diags = _enrich(
            [_para("p1", "a link Cargo, Rust build tool, 2024")],
            raw_title="Projects",
        )
        assert any(d["event"] == ENRICH_PROJECT_ENTRY for d in diags)

    def test_a_link_outside_projects_stays_paragraph(self):
        # Same text but in EXPERIENCE section → no project_entry
        paras, _ = _enrich(
            [_para("p1", "a link Apache Incubator – NLPCraft, API, 2020")],
            raw_title="EXPERIENCE",
        )
        assert _sem(paras) == "paragraph"

    def test_a_link_in_role_meta_not_touched(self):
        # If the para is in a role's meta_para_ids, it may be role_meta (not touched)
        # or paragraph in meta context — project_entry requires is_unowned=True
        role = _role("r1", header_ids=["h1"], meta_ids=["p1"])
        paras, _ = _enrich(
            [_para("p1", "a link Scaffold Indexer, smart contract indexer, 2026")],
            roles=[role],
            raw_title="Projects",
        )
        # para is in meta_para_ids → not unowned → project_entry does NOT fire
        assert _sem(paras) == "paragraph"

    def test_non_a_link_stays_paragraph(self):
        paras, _ = _enrich(
            [_para("p1", "Rust build tool, 2024")],
            raw_title="Projects",
        )
        assert _sem(paras) == "paragraph"

    def test_diagnostic_includes_original_and_enriched(self):
        _, diags = _enrich(
            [_para("p1", "a link NLPCraft, 2020")],
            raw_title="Projects",
        )
        d = next(d for d in diags if d["event"] == ENRICH_PROJECT_ENTRY)
        assert d["original_semantic"] == "paragraph"
        assert d["enriched_semantic"] == "project_entry"


# ---------------------------------------------------------------------------
# project_header (Round 1)
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


# ---------------------------------------------------------------------------
# project_intro / internal project (Round 2)
# ---------------------------------------------------------------------------

class TestInternalProject:
    def test_long_internal_project_in_bullet_context(self):
        role = _role("r1", header_ids=["h1"], bullet_ids=["p1"])
        paras, _ = _enrich(
            [
                _para("h1", "Full Stack Software Engineer", parser_semantic="role_header"),
                _para("p1", "Contribution to the internal project - a self service portal "
                      "which holds the information on employee development"),
            ],
            roles=[role],
        )
        assert _sem(paras, 1) == "project_intro"

    def test_long_migration_project_in_meta_context(self):
        role = _role("r1", header_ids=["h1"], meta_ids=["p1"])
        paras, _ = _enrich(
            [
                _para("h1", "Senior Engineer", parser_semantic="role_header"),
                _para("p1", "Migration project for the payments platform, involving complex "
                      "distributed systems and team coordination across timezones"),
            ],
            roles=[role],
        )
        assert _sem(paras, 1) == "project_intro"

    def test_diagnostic_event_is_internal_project(self):
        role = _role("r1", header_ids=["h1"], bullet_ids=["p1"])
        _, diags = _enrich(
            [
                _para("h1", "Engineer", parser_semantic="role_header"),
                _para("p1", "Contribution to the internal project - a self service portal "
                      "for employee development and technical growth management"),
            ],
            roles=[role],
        )
        assert any(d["event"] == ENRICH_INTERNAL_PROJECT for d in diags)

    def test_short_project_text_stays_project_header(self):
        # Short (≤60) → project_header, not project_intro
        role = _role("r1", header_ids=["h1"], bullet_ids=["p1"])
        paras, _ = _enrich(
            [
                _para("h1", "Engineer", parser_semantic="role_header"),
                _para("p1", "OTA project"),
            ],
            roles=[role],
        )
        assert _sem(paras, 1) == "project_header"

    def test_no_project_keyword_stays_paragraph(self):
        role = _role("r1", header_ids=["h1"], bullet_ids=["p1"])
        paras, _ = _enrich(
            [
                _para("h1", "Engineer", parser_semantic="role_header"),
                _para("p1", "Contribution to the internal platform - a self service portal "
                      "which holds the information on employee development"),
            ],
            roles=[role],
        )
        # No "project" keyword → stays paragraph
        assert _sem(paras, 1) == "paragraph"

    def test_not_in_role_context_stays_paragraph(self):
        # Para with "project" keyword but NOT in any role group
        paras, _ = _enrich([
            _para("p1", "Contribution to the internal project - a self service portal "
                  "which holds the information on employee technical development"),
        ])
        assert _sem(paras) == "paragraph"

    def test_pdf_fake_bullet_not_project_intro(self):
        role = _role("r1", bullet_ids=["p1"])
        paras, _ = _enrich(
            [_para("p1", "f Contributed to the internal project implementation details here")],
            roles=[role],
        )
        # Starts with "f [A-Z]" → PDF fake bullet → excluded
        assert _sem(paras) == "paragraph"

    def test_too_long_stays_paragraph(self):
        role = _role("r1", bullet_ids=["p1"])
        long_text = (
            "Contribution to the internal project - a self service portal which holds "
            "the information on employee development, technical growth and helps manage "
            "it across multiple departments and organizational layers and hierarchies "
            "to enable better performance outcomes"
        )
        assert len(long_text) > 250
        paras, _ = _enrich([_para("p1", long_text)], roles=[role])
        assert _sem(paras) == "paragraph"


# ---------------------------------------------------------------------------
# role_intro (Round 1)
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
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Led migration of critical partner services."),
            _para("p3", "Managed team of five engineers across two time zones."),
        ])
        assert _sem(paras, 1) == "role_intro"
        assert _sem(paras, 2) == "role_intro"

    def test_no_context_stays_paragraph(self):
        paras, _ = _enrich([
            _para("p1", "This is a complete sentence with enough words."),
        ])
        assert _sem(paras) == "paragraph"

    def test_in_meta_para_ids_provides_context(self):
        role = _role("role_1", header_ids=["p0"], meta_ids=["p1"])
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
            _para("p2", "Full-time."),
        ])
        assert _sem(paras, 1) == "paragraph"

    def test_no_terminal_punctuation_stays_paragraph(self):
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Led migration of critical partner services"),
        ])
        assert _sem(paras, 1) == "paragraph"


# ---------------------------------------------------------------------------
# specialization_header (Round 1)
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


# ---------------------------------------------------------------------------
# Priority and guardrails
# ---------------------------------------------------------------------------

class TestPriorityAndGuardrails:
    def test_tech_stack_hint_wins_over_role_intro(self):
        paras, _ = _enrich([
            _para("p1", "Senior Engineer", parser_semantic="role_header"),
            _para("p2", "Technologies: Java, Spring, Docker.",
                  semantic_hint="tech_stack_candidate"),
        ])
        assert _sem(paras, 1) == "tech_stack"

    def test_tech_stack_hint_wins_over_tech_stack_detail(self):
        # Hint-based (0.95) beats pattern-based (0.90)
        _, diags = _enrich([
            _para("p1", "Technologies: Java, Python", semantic_hint="tech_stack_candidate"),
        ])
        events = [d["event"] for d in diags]
        assert ENRICH_TECH_STACK in events
        assert ENRICH_TECH_STACK_DETAIL not in events

    def test_project_entry_wins_over_project_intro(self):
        # In a Projects section, unowned "a link" para: project_entry (0.90) > project_intro (0.80)
        # Note: project_intro won't even fire since no "project" keyword in the text below
        paras, _ = _enrich(
            [_para("p1", "a link NLPCraft, API to convert natural language, 2020")],
            raw_title="Projects",
        )
        assert _sem(paras) == "project_entry"

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
        _, diags = _enrich([_para("para_x", "OTA project")])
        assert diags
        d = diags[0]
        assert "event" in d
        assert "para_id" in d
        assert "text" in d
        assert "confidence" in d
        assert "original_semantic" in d
        assert "enriched_semantic" in d
        assert d["confidence"] >= 0.80
        assert d["original_semantic"] == "paragraph"

    def test_diagnostic_original_semantic_preserved(self):
        _, diags = _enrich([
            _para("p1", "Application level: Java 8, Spring Boot"),
        ])
        d = next(d for d in diags if d["event"] == ENRICH_TECH_STACK_DETAIL)
        assert d["original_semantic"] == "paragraph"
        assert d["enriched_semantic"] == "tech_stack"
