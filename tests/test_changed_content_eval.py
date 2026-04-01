"""Tests for the changed-content layout evaluation framework.

Coverage
--------
1. Taxonomy classification — deterministic, no I/O
2. Scorer helpers — duplication detection, leakage, heading extraction, bullet count
3. Scorer end-to-end — score_layout() with synthetic ExtractedDoc fixtures
4. Report generation — build_case_report() and build_suite_report()
5. Benchmark case loading — BenchmarkCase.from_dict() + load_suite_from_file()
6. Same-text path safety — existing eval pipeline unaffected by the new module
"""
from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from dataclasses import dataclass, field

import pytest

# ---------------------------------------------------------------------------
# Fixtures: synthetic ExtractedDoc
# ---------------------------------------------------------------------------

from tailor.eval.models import (
    DocumentFeatures,
    ExtractedDoc,
    PageModel,
)


def _make_features(
    page_count: int = 1,
    column_count: int = 1,
    column_confidence: str = "high",
    dominant_body_left_x: float = 72.0,
) -> DocumentFeatures:
    return DocumentFeatures(
        page_count=page_count,
        column_count_estimate=column_count,
        column_confidence=column_confidence,
        dominant_left_margins=[72.0],
        dominant_line_gap=12.0,
        dominant_section_gap=18.0,
        dominant_body_left_x=dominant_body_left_x,
    )


def _make_extracted(
    full_text: str = "",
    headings: list[str] | None = None,
    bullet_count: int = 0,
    page_count: int = 1,
    column_count: int = 1,
    column_confidence: str = "high",
) -> ExtractedDoc:
    features = _make_features(
        page_count=page_count,
        column_count=column_count,
        column_confidence=column_confidence,
    )
    return ExtractedDoc(
        path="synthetic",
        pages=[PageModel(page_number=1, width=612.0, height=792.0, lines=[], blocks=[])],
        features=features,
        full_text_normalized=full_text,
        headings=headings or [],
        bullet_count=bullet_count,
    )


# ---------------------------------------------------------------------------
# 1. Taxonomy classification — deterministic
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.taxonomy import (
    FailureClass,
    FailureClassification,
    classify_failures,
)
from tailor.eval.changed_content.scorer import LayoutScore


def _make_score(**overrides) -> LayoutScore:
    defaults = dict(
        topology_preservation=1.0,
        section_placement=1.0,
        overflow_penalty=0.0,
        duplication_penalty=0.0,
        leakage_penalty=0.0,
        style_score=1.0,
        page_count_delta=0,
        src_page_count=1,
        out_page_count=1,
        src_column_count=1,
        out_column_count=1,
        column_confidence="high",
        section_headings_found=3,
        section_headings_expected=3,
        stale_token_ratio=0.0,
        src_bullet_count=5,
        out_bullet_count=5,
        gen_bullet_count=5,
        section_placement_results=[],
        composite=1.0,
    )
    defaults.update(overrides)
    return LayoutScore(**defaults)


class TestTaxonomyClassification:

    def test_clean_score_produces_no_failures(self):
        fc = classify_failures(_make_score())
        assert fc.classes == []

    def test_topology_collapse_high_confidence(self):
        score = _make_score(src_column_count=2, out_column_count=1, column_confidence="high")
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE in fc.classes

    def test_topology_collapse_low_confidence_no_failure(self):
        # Low confidence column detection should NOT trigger topology failure
        score = _make_score(src_column_count=2, out_column_count=1, column_confidence="low")
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE not in fc.classes

    def test_overflow_page_growth(self):
        score = _make_score(page_count_delta=1, src_page_count=1, out_page_count=2)
        fc = classify_failures(score)
        assert FailureClass.C_OVERFLOW_FIT in fc.classes

    def test_no_overflow_same_pages(self):
        score = _make_score(page_count_delta=0)
        fc = classify_failures(score)
        assert FailureClass.C_OVERFLOW_FIT not in fc.classes

    def test_duplication_high_ratio(self):
        score = _make_score(duplication_penalty=0.50, stale_token_ratio=0.55)
        fc = classify_failures(score)
        assert FailureClass.D_DUPLICATION_STALE in fc.classes

    def test_duplication_low_ratio_no_failure(self):
        score = _make_score(duplication_penalty=0.20, stale_token_ratio=0.15)
        fc = classify_failures(score)
        assert FailureClass.D_DUPLICATION_STALE not in fc.classes

    def test_section_boundary_failure(self):
        score = _make_score(section_headings_found=1, section_headings_expected=4)
        fc = classify_failures(score)
        assert FailureClass.A_SECTION_BOUNDARY in fc.classes

    def test_section_boundary_borderline_pass(self):
        # 3/4 = 75% coverage — above the 70% threshold, no failure
        score = _make_score(section_headings_found=3, section_headings_expected=4)
        fc = classify_failures(score)
        assert FailureClass.A_SECTION_BOUNDARY not in fc.classes

    def test_style_failure(self):
        score = _make_score(style_score=0.40)
        fc = classify_failures(score)
        assert FailureClass.E_STYLE_PROTOTYPE in fc.classes

    def test_leakage_failure(self):
        score = _make_score(leakage_penalty=0.30)
        fc = classify_failures(score)
        assert FailureClass.B_CONTAINER_ASSIGNMENT in fc.classes

    def test_multiple_failures_accumulated(self):
        score = _make_score(
            page_count_delta=2,
            src_page_count=1,
            out_page_count=3,
            duplication_penalty=0.60,
            stale_token_ratio=0.65,
            src_column_count=2,
            out_column_count=1,
            column_confidence="high",
        )
        fc = classify_failures(score)
        assert FailureClass.C_OVERFLOW_FIT in fc.classes
        assert FailureClass.D_DUPLICATION_STALE in fc.classes
        assert FailureClass.F_TOPOLOGY_COLLAPSE in fc.classes

    def test_evidence_populated_for_each_class(self):
        score = _make_score(page_count_delta=1, src_page_count=1, out_page_count=2)
        fc = classify_failures(score)
        assert FailureClass.C_OVERFLOW_FIT in fc.classes
        ev = fc.evidence.get(FailureClass.C_OVERFLOW_FIT.value, [])
        assert len(ev) >= 1
        assert "page" in ev[0].lower()

    def test_to_dict_has_labels(self):
        score = _make_score(page_count_delta=1, src_page_count=1, out_page_count=2)
        fc = classify_failures(score)
        d = fc.to_dict()
        assert "labels" in d
        assert any("Overflow" in lbl for lbl in d["labels"])


# ---------------------------------------------------------------------------
# 2. Scorer helpers
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.scorer import (
    _compute_duplication,
    _count_bullets_in_llm_text,
    _detect_leakage,
    _extract_llm_headings,
    _significant_tokens,
)


class TestScorerHelpers:

    def test_significant_tokens_length_filter(self):
        tokens = _significant_tokens("he ran to IBM quickly computing results")
        # "IBM" len=3 excluded; "quickly" len=7 included; "computing" len=9 included
        assert "ibm" not in tokens
        assert "quickly" in tokens
        assert "computing" in tokens

    def test_significant_tokens_case_insensitive(self):
        tokens = _significant_tokens("Microsoft microsoft MICROSOFT")
        assert tokens["microsoft"] == 3

    def test_extract_llm_headings_finds_known_sections(self):
        llm = "Summary\n- Led team of 5\n\nExperience\n- Built API\n\nEducation\n- MIT"
        hdgs = _extract_llm_headings(llm)
        assert "Summary" in hdgs
        assert "Experience" in hdgs
        assert "Education" in hdgs

    def test_extract_llm_headings_excludes_bullets(self):
        llm = "- Led team\n- Built API\nTechnical Skills\n- Python"
        hdgs = _extract_llm_headings(llm)
        assert all(not h.startswith("-") for h in hdgs)
        assert "Technical Skills" in hdgs

    def test_extract_llm_headings_deduplicates(self):
        llm = "Experience\n- item\nExperience\n- item2"
        hdgs = _extract_llm_headings(llm)
        assert hdgs.count("Experience") == 1

    def test_extract_llm_headings_ignores_unknown_sections(self):
        llm = "John Smith\nSenior Engineer\nSummary\n- Great engineer"
        hdgs = _extract_llm_headings(llm)
        # "John Smith" and "Senior Engineer" not known sections
        assert "John Smith" not in hdgs
        assert "Summary" in hdgs

    def test_count_bullets(self):
        llm = "Summary\n- Led team\n- Built platform\nExperience\n- Worked at IBM"
        assert _count_bullets_in_llm_text(llm) == 3

    def test_count_bullets_zero_when_no_bullets(self):
        assert _count_bullets_in_llm_text("Summary\nExperience\nEducation") == 0

    def test_detect_leakage_clean(self):
        penalty, ev = _detect_leakage("Normal resume text without markers")
        assert penalty == 0.0
        assert ev == []

    def test_detect_leakage_template_marker(self):
        penalty, ev = _detect_leakage("Name: {{CANDIDATE_NAME}} graduated from MIT")
        assert penalty > 0.0
        assert len(ev) >= 1

    def test_detect_leakage_current_date(self):
        penalty, ev = _detect_leakage("Started on CURRENT_DATE at IBM")
        assert penalty > 0.0

    def test_duplication_no_overlap(self):
        # Generated text contains all significant tokens from source
        src = "software engineer python django testing deployment"
        gen = "software engineer python django testing deployment kubernetes"
        out = "software engineer python django testing deployment kubernetes"
        ratio, ev = _compute_duplication(src, gen, out)
        assert ratio == pytest.approx(0.0, abs=0.05)

    def test_duplication_high_stale_survival(self):
        # Source has "Microsoft" and "Windows" not in generated; they appear in output
        src = "Worked at Microsoft building Windows systems architecture platform"
        gen = "Worked at Google building Android systems interface product"
        out = "Worked at Microsoft Google building Windows Android systems architecture"
        ratio, ev = _compute_duplication(src, gen, out)
        # microsoft, windows, architecture are stale; they survive in output
        assert ratio > 0.30

    def test_duplication_evidence_populated_when_high(self):
        src = "Achieved Microsoft Windows systems architecture platform buildings"
        gen = "Achieved Google Android systems interface product service"
        out = "Achieved Microsoft Windows systems architecture platform buildings"
        ratio, ev = _compute_duplication(src, gen, out)
        # High stale ratio should produce evidence
        if ratio >= 0.35:
            assert len(ev) >= 1


# ---------------------------------------------------------------------------
# 3. Scorer end-to-end (score_layout)
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.scorer import score_layout


class TestScoreLayout:

    def test_perfect_topology_match(self):
        src = _make_extracted(
            full_text="summary experienced developer",
            headings=["Summary", "Experience"],
            bullet_count=3,
            column_count=1,
        )
        out = _make_extracted(
            full_text="summary experienced developer kubernetes",
            headings=["Summary", "Experience"],
            bullet_count=3,
            column_count=1,
        )
        llm = "Summary\n- Senior developer\nExperience\n- Built APIs\n- Led team"
        score, ev = score_layout(src, out, "old summary old experience", llm)
        assert score.topology_preservation == 1.0
        assert score.overflow_penalty == 0.0
        assert score.composite >= 0.70

    def test_column_collapse_penalises_topology(self):
        src = _make_extracted(column_count=2, column_confidence="high")
        out = _make_extracted(column_count=1, column_confidence="high")
        llm = "Summary\n- text\nExperience\n- text"
        score, ev = score_layout(src, out, "old text", llm)
        assert score.topology_preservation == 0.0
        assert any("column" in e.lower() for e in ev)

    def test_page_growth_penalises_overflow(self):
        src = _make_extracted(page_count=1)
        out = _make_extracted(page_count=2)
        llm = "Summary\n- text"
        score, ev = score_layout(src, out, "old text", llm)
        assert score.page_count_delta == 1
        assert score.overflow_penalty > 0.0
        assert any("page" in e.lower() for e in ev)

    def test_section_placement_all_found(self):
        src = _make_extracted(headings=["Summary"])
        out = _make_extracted(headings=["Summary", "Experience", "Education"])
        llm = "Summary\n- text\nExperience\n- job\nEducation\n- degree"
        score, ev = score_layout(src, out, "old", llm)
        assert score.section_placement == pytest.approx(1.0)
        assert score.section_headings_found == 3
        assert score.section_headings_expected == 3

    def test_section_placement_missing_heading(self):
        src = _make_extracted()
        out = _make_extracted(headings=["Summary"])  # Experience missing
        llm = "Summary\n- text\nExperience\n- job"
        score, ev = score_layout(src, out, "old", llm)
        assert score.section_headings_found == 1
        assert score.section_headings_expected == 2
        assert score.section_placement == pytest.approx(0.5)

    def test_bullet_count_ratio_affects_style(self):
        src = _make_extracted(bullet_count=10)
        out = _make_extracted(bullet_count=2)
        llm = "Experience\n" + "\n".join(f"- bullet {i}" for i in range(10))
        score, ev = score_layout(src, out, "old", llm)
        assert score.style_score < 0.60  # 2/10 = 0.20 ratio → style_score = 0.25

    def test_all_metric_fields_present(self):
        src = _make_extracted()
        out = _make_extracted()
        llm = "Summary\n- text"
        score, _ = score_layout(src, out, "old text for source", llm)
        # All LayoutScore fields should be populated (not None)
        from dataclasses import fields as dc_fields
        for f in dc_fields(score):
            assert getattr(score, f.name) is not None, f"{f.name} is None"

    def test_composite_in_range(self):
        src = _make_extracted()
        out = _make_extracted()
        llm = "Summary\n- text\nExperience\n- job"
        score, _ = score_layout(src, out, "source text here", llm)
        assert 0.0 <= score.composite <= 1.0


# ---------------------------------------------------------------------------
# 4. Report generation
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.benchmark import CaseResult
from tailor.eval.changed_content.report import (
    build_case_report,
    build_suite_report,
    write_case_report,
    write_suite_report,
)


def _make_case_result(**overrides) -> CaseResult:
    defaults = dict(
        case_id="test_case_01",
        template_class="linear",
        severity="S2",
        layout_score=0.85,
        metric_breakdown={
            "topology_preservation": 1.0,
            "section_placement": 0.9,
            "overflow_penalty": 0.0,
            "duplication_penalty": 0.1,
            "leakage_penalty": 0.0,
            "style_score": 0.9,
            "page_count_delta": 0,
            "src_page_count": 1,
            "out_page_count": 1,
            "src_column_count": 1,
            "out_column_count": 1,
            "column_confidence": "high",
            "section_headings_found": 3,
            "section_headings_expected": 3,
            "stale_token_ratio": 0.05,
            "src_bullet_count": 5,
            "out_bullet_count": 5,
            "gen_bullet_count": 5,
            "section_placement_results": [],
            "composite": 0.85,
        },
        failure_classes=[],
        evidence=[],
        notes="unit test case",
        error="",
    )
    defaults.update(overrides)
    return CaseResult(**defaults)


class TestReportGeneration:

    def test_build_case_report_has_required_keys(self):
        result = _make_case_result()
        report = build_case_report(result)
        for key in ("case_id", "template_class", "severity", "status",
                    "layout_score", "metric_breakdown", "failure_classes",
                    "evidence", "failure_labels"):
            assert key in report, f"Missing key: {key}"

    def test_build_case_report_status_pass(self):
        result = _make_case_result(layout_score=0.85)
        report = build_case_report(result)
        assert report["status"] == "pass"

    def test_build_case_report_status_fail(self):
        result = _make_case_result(layout_score=0.50)
        report = build_case_report(result)
        assert report["status"] == "fail"

    def test_build_case_report_status_error(self):
        result = _make_case_result(error="something broke", layout_score=0.0)
        report = build_case_report(result)
        assert report["status"] == "error"

    def test_write_case_report_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _make_case_result()
            report = build_case_report(result)
            write_case_report(report, tmp)
            assert os.path.exists(os.path.join(tmp, "report.json"))
            assert os.path.exists(os.path.join(tmp, "summary.txt"))

    def test_write_case_report_json_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _make_case_result()
            report = build_case_report(result)
            write_case_report(report, tmp)
            with open(os.path.join(tmp, "report.json")) as f:
                loaded = json.load(f)
            assert loaded["case_id"] == "test_case_01"

    def test_build_suite_report_aggregates_correctly(self):
        results = [
            _make_case_result(case_id="c1", layout_score=0.90, template_class="linear"),
            _make_case_result(case_id="c2", layout_score=0.60, template_class="table_sidebar",
                              failure_classes=["C_OVERFLOW_FIT"]),
            _make_case_result(case_id="c3", layout_score=0.80, template_class="linear",
                              failure_classes=["D_DUPLICATION_STALE"]),
        ]
        report = build_suite_report(results)
        assert report["total"] == 3
        assert report["passed"] == 2
        assert report["failed"] == 1
        assert report["average_layout_score"] == pytest.approx((0.90 + 0.60 + 0.80) / 3, abs=0.001)
        assert "C_OVERFLOW_FIT" in report["failure_class_frequency"]

    def test_write_suite_report_creates_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = [_make_case_result()]
            report = build_suite_report(results)
            write_suite_report(report, tmp)
            assert os.path.exists(os.path.join(tmp, "suite_report.json"))
            assert os.path.exists(os.path.join(tmp, "suite_summary.md"))

    def test_suite_summary_md_contains_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = [_make_case_result()]
            report = build_suite_report(results)
            write_suite_report(report, tmp)
            md = open(os.path.join(tmp, "suite_summary.md")).read()
            assert "| Case |" in md
            assert "test_case_01" in md


# ---------------------------------------------------------------------------
# 5. Benchmark case loading
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.benchmark import BenchmarkCase, load_suite_from_file


class TestBenchmarkCaseLoading:

    def test_from_dict_minimal(self):
        d = {
            "case_id": "test_01",
            "source_docx": "/tmp/template.docx",
            "generated_resume_text": "Summary\n- text",
        }
        case = BenchmarkCase.from_dict(d)
        assert case.case_id == "test_01"
        assert case.template_class_hint == "linear"   # default
        assert case.severity == "S2"                  # default

    def test_from_dict_full(self):
        d = {
            "case_id": "sidebar_01",
            "source_docx": "/tmp/sidebar.docx",
            "generated_resume_text_file": "/tmp/llm_out.txt",
            "template_class_hint": "table_sidebar",
            "severity": "S3",
            "notes": "pathological case",
        }
        case = BenchmarkCase.from_dict(d)
        assert case.template_class_hint == "table_sidebar"
        assert case.severity == "S3"
        assert case.notes == "pathological case"

    def test_load_suite_from_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            suite = {
                "suite_id": "cc_v1",
                "cases": [
                    {
                        "case_id": "linear_01",
                        "source_docx": "/tmp/t.docx",
                        "generated_resume_text": "Summary\n- text",
                        "template_class_hint": "linear",
                        "severity": "S2",
                    },
                    {
                        "case_id": "sidebar_01",
                        "source_docx": "/tmp/s.docx",
                        "generated_resume_text": "Summary\n- text",
                        "template_class_hint": "table_sidebar",
                        "severity": "S3",
                    },
                ],
            }
            path = os.path.join(tmp, "suite.json")
            with open(path, "w") as f:
                json.dump(suite, f)

            suite_id, cases = load_suite_from_file(path)
            assert suite_id == "cc_v1"
            assert len(cases) == 2
            assert cases[0].case_id == "linear_01"
            assert cases[1].template_class_hint == "table_sidebar"

    def test_load_generated_text_from_inline(self):
        from tailor.eval.changed_content.benchmark import _load_generated_text
        case = BenchmarkCase(
            case_id="x",
            source_docx="/tmp/t.docx",
            generated_resume_text="Summary\n- text",
        )
        assert _load_generated_text(case) == "Summary\n- text"

    def test_load_generated_text_from_file(self):
        from tailor.eval.changed_content.benchmark import _load_generated_text
        with tempfile.TemporaryDirectory() as tmp:
            txt = os.path.join(tmp, "llm.txt")
            with open(txt, "w") as f:
                f.write("Summary\n- from file")
            case = BenchmarkCase(
                case_id="x",
                source_docx="/tmp/t.docx",
                generated_resume_text_file=txt,
            )
            assert _load_generated_text(case) == "Summary\n- from file"

    def test_load_generated_text_from_debug_json(self):
        from tailor.eval.changed_content.benchmark import _load_generated_text
        with tempfile.TemporaryDirectory() as tmp:
            debug = os.path.join(tmp, "debug.json")
            with open(debug, "w") as f:
                json.dump({"resume": "Summary\n- from debug json", "other": "data"}, f)
            case = BenchmarkCase(
                case_id="x",
                source_docx="/tmp/t.docx",
                generated_debug_json=debug,
            )
            assert _load_generated_text(case) == "Summary\n- from debug json"

    def test_load_generated_text_missing_raises(self):
        from tailor.eval.changed_content.benchmark import _load_generated_text
        case = BenchmarkCase(case_id="x", source_docx="/tmp/t.docx")
        with pytest.raises(ValueError, match="no generated text"):
            _load_generated_text(case)


# ---------------------------------------------------------------------------
# 6. Same-text path safety
# ---------------------------------------------------------------------------

class TestSameTextPathSafety:
    """Verify the existing same-text eval modules are unaffected."""

    def test_extractor_module_importable(self):
        from tailor.eval.extractor import extract, normalize_text  # noqa: F401

    def test_comparator_module_importable(self):
        from tailor.eval.comparator import compare  # noqa: F401

    def test_pipeline_module_importable(self):
        from tailor.eval.pipeline import run_pipeline  # noqa: F401

    def test_reporter_module_importable(self):
        from tailor.eval.reporter import (  # noqa: F401
            build_sample_report,
            build_run_summary,
        )

    def test_visualizer_module_importable(self):
        from tailor.eval.visualizer import render_page_artifacts  # noqa: F401

    def test_baseline_module_importable(self):
        from tailor.eval.baseline import load_baseline, promote  # noqa: F401

    def test_comparator_heading_helpers_unchanged(self):
        """Private helpers reused by scorer must retain original behaviour."""
        from tailor.eval.comparator import _heading_key, _is_known_section

        assert _heading_key("Technical Skills") == "technicalskills"
        assert _heading_key("1. Experience") == "experience"
        assert _is_known_section("Experience") is True
        assert _is_known_section("John Smith") is False
        assert _is_known_section("Technical Skills") is True

    def test_changed_content_imports_do_not_alter_comparator(self):
        """Importing the new changed_content package must not side-effect comparator."""
        from tailor.eval import comparator
        original_thresholds = dict(comparator.THRESHOLDS)

        import tailor.eval.changed_content  # noqa: F401

        assert comparator.THRESHOLDS == original_thresholds


# ---------------------------------------------------------------------------
# 7. Placement: heading normalization
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.placement import (
    SECTION_ALIASES,
    SectionAnchor,
    SectionPlacementResult,
    classify_region,
    extract_section_anchors,
    match_canonical_type,
    normalize_heading,
    score_placement,
)


class TestHeadingNormalization:

    def test_lowercase_and_trim(self):
        assert normalize_heading("  EXPERIENCE  ") == "experience"

    def test_ampersand_to_and(self):
        assert normalize_heading("Skills & Technologies") == "skills and technologies"

    def test_punctuation_stripped(self):
        assert normalize_heading("Education:") == "education"

    def test_leading_number_stripped(self):
        assert normalize_heading("1. Experience") == "experience"
        assert normalize_heading("2. Education") == "education"

    def test_whitespace_collapsed(self):
        assert normalize_heading("Technical  Skills") == "technical skills"


class TestMatchCanonicalType:

    def test_exact_canonical_names(self):
        assert match_canonical_type("Experience")       == "experience"
        assert match_canonical_type("Education")        == "education"
        assert match_canonical_type("Skills")           == "skills"
        assert match_canonical_type("Languages")        == "languages"
        assert match_canonical_type("Certifications")   == "certifications"
        assert match_canonical_type("Summary")          == "summary"

    def test_aliases(self):
        assert match_canonical_type("Professional Summary") == "summary"
        assert match_canonical_type("Employment History")   == "experience"
        assert match_canonical_type("Work Experience")      == "experience"
        assert match_canonical_type("Academic Background")  == "education"
        assert match_canonical_type("Technical Skills")     == "skills"
        assert match_canonical_type("Core Competencies")    == "skills"
        assert match_canonical_type("Certifications and Training") == "certifications"
        assert match_canonical_type("Spoken Languages")     == "languages"

    def test_case_insensitive(self):
        assert match_canonical_type("EXPERIENCE")          == "experience"
        assert match_canonical_type("technical skills")    == "skills"

    def test_non_section_returns_none(self):
        assert match_canonical_type("John Smith")          is None
        assert match_canonical_type("Senior Engineer")     is None
        assert match_canonical_type("New York, NY")        is None

    def test_all_aliases_resolve(self):
        """Every alias in SECTION_ALIASES must resolve to its canonical type."""
        for canonical, aliases in SECTION_ALIASES.items():
            for alias in aliases:
                result = match_canonical_type(alias)
                assert result == canonical, (
                    f"Alias '{alias}' should map to '{canonical}', got {result!r}"
                )


# ---------------------------------------------------------------------------
# 8. Placement: region classification
# ---------------------------------------------------------------------------

class TestRegionClassification:

    def test_wide_block_is_main(self):
        # Full-width block
        assert classify_region(0.10, 0.10, 0.90, 0.15) == "main"

    def test_narrow_left_block_is_sidebar_left(self):
        # 20% wide, centered at 15% → sidebar_left
        assert classify_region(0.05, 0.20, 0.25, 0.25) == "sidebar_left"

    def test_narrow_right_block_is_sidebar_right(self):
        # 20% wide, centered at 80% → sidebar_right
        assert classify_region(0.70, 0.20, 0.90, 0.25) == "sidebar_right"

    def test_narrow_centered_block_is_main(self):
        # Narrow but centered (e.g., narrow single-column resume)
        assert classify_region(0.30, 0.20, 0.70, 0.25) == "main"

    def test_zero_width_is_unknown(self):
        assert classify_region(0.50, 0.20, 0.50, 0.25) == "unknown"

    def test_borderline_left_sidebar(self):
        # width=0.44 (< 0.45), center=0.22 (< 0.35)
        assert classify_region(0.00, 0.0, 0.44, 0.1) == "sidebar_left"

    def test_borderline_wide_is_main(self):
        # width=0.46 (>= 0.45) → main regardless of position
        assert classify_region(0.00, 0.0, 0.46, 0.1) == "main"


# ---------------------------------------------------------------------------
# 9. Placement: anchor extraction from ExtractedDoc
# ---------------------------------------------------------------------------

from tailor.eval.models import BlockModel, LineModel


def _make_heading_block(
    text: str,
    x0: float, y0: float, x1: float, y1: float,
    block_id: str = "b1",
) -> BlockModel:
    """Build a minimal heading BlockModel with normalized-equivalent pt bbox."""
    line = LineModel(
        text=text,
        bbox=(x0, y0, x1, y1),
        spans=[],
        left_x=x0,
        right_x=x1,
        baseline_y=y0 + 5.0,
        is_heading_candidate=True,
    )
    return BlockModel(
        block_id=block_id,
        block_type="heading",
        bbox=(x0, y0, x1, y1),
        lines=[line],
        dominant_left_x=x0,
    )


def _make_extracted_with_blocks(
    page_width: float,
    page_height: float,
    heading_blocks: list[tuple[str, float, float, float, float]],  # (text, x0, y0, x1, y1) in pt
    extra_headings: list[str] | None = None,
) -> ExtractedDoc:
    """Build an ExtractedDoc with real block geometry for placement testing."""
    blocks = [
        _make_heading_block(text, x0, y0, x1, y1, block_id=f"b{i}")
        for i, (text, x0, y0, x1, y1) in enumerate(heading_blocks)
    ]
    page = PageModel(
        page_number=1,
        width=page_width,
        height=page_height,
        lines=[],
        blocks=blocks,
    )
    return ExtractedDoc(
        path="synthetic",
        pages=[page],
        features=_make_features(page_count=1),
        headings=(extra_headings or []) + [t for t, *_ in heading_blocks],
        bullet_count=0,
    )


class TestAnchorExtraction:
    PW = 612.0
    PH = 792.0

    def test_extracts_known_sections(self):
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [
                ("Summary",    72, 100, 540, 115),
                ("Experience", 72, 200, 540, 215),
                ("Education",  72, 400, 540, 415),
            ],
        )
        anchors = extract_section_anchors(doc)
        types = [a.canonical_type for a in anchors]
        assert "summary"    in types
        assert "experience" in types
        assert "education"  in types

    def test_ignores_non_section_headings(self):
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [
                ("John Smith",  72, 50, 540, 65),   # not a known section
                ("Experience",  72, 200, 540, 215),
            ],
        )
        anchors = extract_section_anchors(doc)
        assert all(a.canonical_type != "johnsmith" for a in anchors)
        assert len(anchors) == 1
        assert anchors[0].canonical_type == "experience"

    def test_bbox_normalized_to_0_1(self):
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [("Summary", 72, 100, 540, 115)],
        )
        anchors = extract_section_anchors(doc)
        assert len(anchors) == 1
        a = anchors[0]
        assert 0.0 <= a.nx0 <= 1.0
        assert 0.0 <= a.ny0 <= 1.0
        assert pytest.approx(a.nx0, abs=0.01) == 72 / self.PW
        assert pytest.approx(a.ny0, abs=0.01) == 100 / self.PH

    def test_sorted_in_reading_order(self):
        # Experience at y=200 comes before Summary at y=100? No — sorted by y asc.
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [
                ("Experience", 72, 300, 540, 315),
                ("Summary",    72, 100, 540, 115),
                ("Education",  72, 500, 540, 515),
            ],
        )
        anchors = extract_section_anchors(doc)
        types = [a.canonical_type for a in anchors]
        assert types == ["summary", "experience", "education"]

    def test_empty_blocks_returns_empty(self):
        doc = _make_extracted()  # no blocks
        anchors = extract_section_anchors(doc)
        assert anchors == []

    def test_region_classified_for_main_heading(self):
        # Wide block spanning most of the page → main
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [("Experience", 72, 200, 540, 215)],  # wide
        )
        anchors = extract_section_anchors(doc)
        assert anchors[0].region == "main"

    def test_region_classified_for_sidebar_heading(self):
        # Narrow block on the right → sidebar_right
        doc = _make_extracted_with_blocks(
            self.PW, self.PH,
            [("Languages", 420, 200, 560, 215)],  # right side, narrow
        )
        anchors = extract_section_anchors(doc)
        assert anchors[0].region == "sidebar_right"


# ---------------------------------------------------------------------------
# 10. Placement: per-section scoring
# ---------------------------------------------------------------------------

class TestSectionScoring:
    PW = 612.0
    PH = 792.0

    def _doc_with(self, headings_pt: list[tuple[str, float, float, float, float]]) -> ExtractedDoc:
        return _make_extracted_with_blocks(self.PW, self.PH, headings_pt)

    def test_same_page_same_region_same_y_high_score(self):
        src = self._doc_with([("Summary", 72, 80, 540, 95)])
        out = self._doc_with([("Summary", 72, 82, 540, 97)])  # 2pt drift only
        results, overall = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        assert summary.placement_score >= 0.85
        assert overall is not None
        assert overall >= 0.50

    def test_wrong_region_penalised(self):
        # Source: summary in main region; output: summary pushed to right sidebar
        src = self._doc_with([("Summary", 72, 80, 540, 95)])    # main
        out = self._doc_with([("Summary", 430, 80, 590, 95)])   # sidebar_right
        results, _ = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        assert summary.region_score == 0.0
        # region weight is 0.30; max score with region=0 is 1.0 - 0.30 = 0.70
        assert summary.placement_score <= 0.70
        assert summary.placement_score < 1.0

    def test_large_vertical_drift_penalised(self):
        # Source: summary at y=0.10, output: summary at y=0.60
        src = self._doc_with([("Summary", 72, int(0.10 * self.PH), 540, int(0.10 * self.PH) + 15)])
        out = self._doc_with([("Summary", 72, int(0.60 * self.PH), 540, int(0.60 * self.PH) + 15)])
        results, _ = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        assert summary.vertical_score < 0.40

    def test_missing_output_section_scores_zero(self):
        src = self._doc_with([("Experience", 72, 200, 540, 215)])
        out = self._doc_with([])  # experience missing in output
        results, _ = score_placement(src, out)
        exp = next(r for r in results if r.canonical_type == "experience")
        assert exp.input_found is True
        assert exp.output_found is False
        assert exp.placement_score == 0.0

    def test_page_drift_penalised(self):
        # Source on page 1, output on page 2
        page1 = PageModel(page_number=1, width=self.PW, height=self.PH, lines=[],
                          blocks=[_make_heading_block("Experience", 72, 200, 540, 215, "b1")])
        page2 = PageModel(page_number=2, width=self.PW, height=self.PH, lines=[],
                          blocks=[_make_heading_block("Experience", 72, 200, 540, 215, "b2")])
        src = ExtractedDoc("s", [page1], _make_features(), headings=["Experience"])
        out = ExtractedDoc("o", [page2], _make_features(), headings=["Experience"])
        results, _ = score_placement(src, out)
        exp = next(r for r in results if r.canonical_type == "experience")
        assert exp.page_score < 1.0
        assert exp.input_page == 1
        assert exp.output_page == 2

    def test_order_drift_penalised(self):
        # Source: Summary, Experience, Education
        # Output: Education, Experience, Summary (completely reversed)
        src = self._doc_with([
            ("Summary",    72, 100, 540, 115),
            ("Experience", 72, 250, 540, 265),
            ("Education",  72, 450, 540, 465),
        ])
        out = self._doc_with([
            ("Education",  72, 100, 540, 115),
            ("Experience", 72, 250, 540, 265),
            ("Summary",    72, 450, 540, 465),
        ])
        results, _ = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        # Summary moved from rank 0 to rank 2 → order_score should be < 1.0
        assert summary.order_score < 1.0

    def test_no_geometry_returns_none_overall(self):
        # Synthetic docs with empty blocks → no anchors
        src = _make_extracted()
        out = _make_extracted()
        results, overall = score_placement(src, out)
        assert results == []
        assert overall is None

    def test_all_six_section_types_in_results(self):
        src = self._doc_with([
            ("Summary",        72, 80,  540, 95),
            ("Experience",     72, 200, 540, 215),
            ("Education",      72, 400, 540, 415),
            ("Technical Skills", 72, 500, 540, 515),
            ("Languages",      420, 80,  590, 95),
            ("Certifications", 420, 200, 590, 215),
        ])
        out = self._doc_with([
            ("Summary",        72, 82,  540, 97),
            ("Experience",     72, 202, 540, 217),
            ("Education",      72, 402, 540, 417),
            ("Technical Skills", 72, 502, 540, 517),
            ("Languages",      420, 82,  590, 97),
            ("Certifications", 420, 202, 590, 217),
        ])
        results, overall = score_placement(src, out)
        types = {r.canonical_type for r in results}
        assert types == {"summary", "experience", "education", "skills", "languages", "certifications"}
        assert overall is not None
        assert overall >= 0.80


# ---------------------------------------------------------------------------
# 11. Placement: missing-summary expected-zone heuristic
# ---------------------------------------------------------------------------

class TestExpectedZoneHeuristic:
    PW = 612.0
    PH = 792.0

    def test_summary_in_expected_zone_scores_reasonably(self):
        # Source has no summary heading; output inserts one at top of page 1
        src = _make_extracted_with_blocks(self.PW, self.PH, [
            ("Experience", 72, 200, 540, 215),
        ])
        out = _make_extracted_with_blocks(self.PW, self.PH, [
            ("Summary",    72, 100, 540, 115),  # top 40% of page, main region
            ("Experience", 72, 250, 540, 265),
        ])
        results, _ = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        assert summary.input_found is False
        assert summary.output_found is True
        assert summary.placement_score >= 0.60  # expected zone → not penalised heavily
        assert any("expected zone" in n.lower() for n in summary.notes)

    def test_summary_outside_expected_zone_scores_neutral(self):
        # Output inserts summary at page 2 — not expected zone
        page1 = PageModel(page_number=1, width=self.PW, height=self.PH, lines=[],
                          blocks=[_make_heading_block("Experience", 72, 200, 540, 215)])
        page2 = PageModel(page_number=2, width=self.PW, height=self.PH, lines=[],
                          blocks=[_make_heading_block("Summary", 72, 100, 540, 115)])
        src = ExtractedDoc("s", [page1], _make_features(), headings=["Experience"])
        out = ExtractedDoc("o", [page1, page2], _make_features(page_count=2),
                          headings=["Experience", "Summary"])
        results, _ = score_placement(src, out)
        summary = next(r for r in results if r.canonical_type == "summary")
        assert summary.input_found is False
        assert summary.output_found is True
        # Page 2 summary is not the expected zone → neutral score 0.5
        assert summary.placement_score == pytest.approx(0.5, abs=0.01)


# ---------------------------------------------------------------------------
# 12. Taxonomy: placement-driven failure detection
# ---------------------------------------------------------------------------

class TestTaxonomyPlacementIntegration:

    def test_region_mismatch_triggers_class_B(self):
        # section_placement_results contains a region_score=0.0 entry
        pr = {
            "canonical_type": "skills",
            "input_found": True, "output_found": True,
            "input_region": "sidebar_left", "output_region": "main",
            "region_score": 0.0,
            "vertical_score": 0.9, "page_score": 1.0, "order_score": 1.0,
            "placement_score": 0.30,
        }
        score = _make_score(section_placement_results=[pr])
        fc = classify_failures(score)
        assert FailureClass.B_CONTAINER_ASSIGNMENT in fc.classes

    def test_missing_section_from_placement_triggers_class_A(self):
        pr = {
            "canonical_type": "experience",
            "input_found": True, "output_found": False,
            "input_region": "main", "output_region": None,
            "region_score": 0.0, "vertical_score": 0.0,
            "page_score": 0.0, "order_score": 0.0,
            "placement_score": 0.0,
        }
        score = _make_score(section_placement_results=[pr])
        fc = classify_failures(score)
        assert FailureClass.A_SECTION_BOUNDARY in fc.classes

    def test_two_bad_vertical_scores_trigger_class_F(self):
        def _bad_pr(ctype):
            return {
                "canonical_type": ctype,
                "input_found": True, "output_found": True,
                "input_region": "main", "output_region": "main",
                "region_score": 1.0,
                "vertical_score": 0.10,  # severely displaced
                "page_score": 1.0, "order_score": 1.0,
                "placement_score": 0.40,
            }
        score = _make_score(section_placement_results=[_bad_pr("summary"), _bad_pr("experience")])
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE in fc.classes

    def test_one_bad_vertical_does_not_trigger_class_F(self):
        pr = {
            "canonical_type": "summary",
            "input_found": True, "output_found": True,
            "input_region": "main", "output_region": "main",
            "region_score": 1.0, "vertical_score": 0.10,
            "page_score": 1.0, "order_score": 1.0,
            "placement_score": 0.40,
        }
        score = _make_score(section_placement_results=[pr])
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE not in fc.classes

    def test_no_placement_results_uses_heading_count_fallback(self):
        # No placement results → falls back to heading-count heuristic
        score = _make_score(
            section_placement_results=[],
            section_headings_found=1,
            section_headings_expected=5,
        )
        fc = classify_failures(score)
        assert FailureClass.A_SECTION_BOUNDARY in fc.classes


# ---------------------------------------------------------------------------
# 13. Scorer integration: placement feeds section_placement field
# ---------------------------------------------------------------------------

class TestScorerPlacementIntegration:

    def test_geometric_placement_used_when_blocks_present(self):
        """With real block geometry, section_placement comes from placement module."""
        PW, PH = 612.0, 792.0
        src = _make_extracted_with_blocks(PW, PH, [
            ("Summary", 72, 80, 540, 95),
            ("Experience", 72, 200, 540, 215),
        ])
        out = _make_extracted_with_blocks(PW, PH, [
            ("Summary", 72, 82, 540, 97),    # 2pt drift
            ("Experience", 72, 202, 540, 217),
        ])
        llm = "Summary\n- text\nExperience\n- job"
        score, _ = score_layout(src, out, "old text", llm)
        # Should have placement results (geometric data available)
        assert len(score.section_placement_results) > 0
        # section_placement should reflect geometric score
        assert score.section_placement > 0.70

    def test_fallback_when_no_blocks(self):
        """Without block geometry, falls back to heading count ratio."""
        src = _make_extracted(headings=["Summary", "Experience"])
        out = _make_extracted(headings=["Summary"])  # Experience missing
        llm = "Summary\n- text\nExperience\n- job"
        score, _ = score_layout(src, out, "old text", llm)
        # No blocks → no placement results
        assert score.section_placement_results == []
        # Falls back to 1/2 heading count ratio
        assert score.section_headings_found == 1
        assert score.section_headings_expected == 2
        assert score.section_placement == pytest.approx(0.5)

    def test_placement_results_in_report(self):
        """Placement results are serialized into the case report."""
        PW, PH = 612.0, 792.0
        src = _make_extracted_with_blocks(PW, PH, [("Experience", 72, 200, 540, 215)])
        out = _make_extracted_with_blocks(PW, PH, [("Experience", 72, 202, 540, 217)])
        llm = "Experience\n- job"
        score, _ = score_layout(src, out, "old text", llm)

        from dataclasses import asdict
        result = _make_case_result(
            metric_breakdown={**asdict(score)},
            layout_score=score.composite,
        )
        report = build_case_report(result)
        sp = report.get("section_placement", [])
        assert isinstance(sp, list)
        # Should have at least the experience section
        types = {r.get("canonical_type") for r in sp}
        assert "experience" in types


# ---------------------------------------------------------------------------
# 14. Topology classifier
# ---------------------------------------------------------------------------

from tailor.eval.changed_content.topology import (
    TOPOLOGY_AMBIG,
    TOPOLOGY_BALANCED,
    TOPOLOGY_LINEAR,
    TOPOLOGY_SB_LEFT,
    TOPOLOGY_SB_RIGHT,
    TopologyClassification,
    classify_page_topology,
    classify_topology,
    compute_horizontal_occupancy,
    compute_sidebar_persistence,
    topology_preservation_score,
    _filter_and_normalize,
    _NormBlock,
)


# --------------------------------------------------------------------------
# Topology test helpers
# --------------------------------------------------------------------------

def _make_para_block(
    text: str,
    x0: float, y0: float, x1: float, y1: float,
    block_id: str = "b",
) -> "BlockModel":
    """Build a minimal paragraph BlockModel with the given absolute bbox."""
    line = LineModel(text=text, bbox=(x0, y0, x1, y1), spans=[],
                     left_x=x0, right_x=x1, baseline_y=y0 + 5.0)
    return BlockModel(block_id=block_id, block_type="paragraph",
                      bbox=(x0, y0, x1, y1), lines=[line], dominant_left_x=x0)


def _page_with_blocks(blocks: list, width: float = 612.0, height: float = 792.0) -> "PageModel":
    """Return a PageModel populated with the given blocks."""
    return PageModel(page_number=1, width=width, height=height, lines=[], blocks=blocks)


def _doc_with_page(page: "PageModel") -> ExtractedDoc:
    """Wrap a single PageModel in a minimal ExtractedDoc."""
    return ExtractedDoc(
        path="synthetic",
        pages=[page],
        features=_make_features(page_count=1),
        headings=[],
        bullet_count=0,
    )


# ---------------------------------------------------------------------------
# Synthetic layout factories (all coords in absolute pts for PW=612, PH=792)
# ---------------------------------------------------------------------------

_PW, _PH = 612.0, 792.0


def _wide_blocks(n: int = 5) -> list:
    """n wide blocks evenly distributed vertically — single-column layout."""
    blocks = []
    for i in range(n):
        y0 = 50 + i * 130
        y1 = y0 + 80
        blocks.append(_make_para_block(
            "Lorem ipsum dolor sit amet consectetur", 61, y0, 551, y1, f"w{i}",
        ))
    return blocks


def _sidebar_left_blocks(n_side: int = 4, n_main: int = 4) -> list:
    """Narrow left sidebar + wider right main body."""
    blocks = []
    for i in range(n_side):
        y0 = 80 + i * 140
        y1 = y0 + 50
        blocks.append(_make_para_block(
            "Skill item here", 12, y0, 160, y1, f"sl{i}",
        ))
    for i in range(n_main):
        y0 = 80 + i * 140
        y1 = y0 + 90
        blocks.append(_make_para_block(
            "Main body content paragraph much longer", 190, y0, 590, y1, f"ml{i}",
        ))
    return blocks


def _sidebar_right_blocks(n_side: int = 4, n_main: int = 4) -> list:
    """Wider left main body + narrow right sidebar."""
    blocks = []
    for i in range(n_side):
        y0 = 80 + i * 140
        y1 = y0 + 50
        blocks.append(_make_para_block(
            "Skill item here", 452, y0, 600, y1, f"sr{i}",
        ))
    for i in range(n_main):
        y0 = 80 + i * 140
        y1 = y0 + 90
        blocks.append(_make_para_block(
            "Main body content paragraph much longer", 22, y0, 420, y1, f"mr{i}",
        ))
    return blocks


def _balanced_two_column_blocks(n: int = 4) -> list:
    """Two medium-width columns of similar area."""
    blocks = []
    for i in range(n):
        y0 = 80 + i * 150
        y1 = y0 + 100
        # Left column: nx0≈0.05, nx1≈0.47
        blocks.append(_make_para_block("Left column content here", 30, y0, 288, y1, f"cl{i}"))
        # Right column: nx0≈0.50, nx1≈0.95
        blocks.append(_make_para_block("Right column content here", 306, y0, 582, y1, f"cr{i}"))
    return blocks


# ---------------------------------------------------------------------------
# TestTopologySignals — unit tests for helper functions
# ---------------------------------------------------------------------------

class TestTopologySignals:

    def test_filter_drops_empty_blocks(self):
        block = _make_para_block("", 60, 100, 550, 120)
        page  = _page_with_blocks([block])
        result = _filter_and_normalize(page)
        assert result == []

    def test_filter_drops_tiny_width(self):
        block = _make_para_block("ok", 60, 100, 62, 120)   # width = 2 pt → norm ~0.003
        page  = _page_with_blocks([block])
        result = _filter_and_normalize(page)
        assert result == []

    def test_filter_keeps_valid_block(self):
        block = _make_para_block("Hello world content", 60, 100, 550, 130)
        page  = _page_with_blocks([block])
        result = _filter_and_normalize(page)
        assert len(result) == 1
        nb = result[0]
        assert nb.nx0 == pytest.approx(60 / 612, rel=1e-3)
        assert nb.nx1 == pytest.approx(550 / 612, rel=1e-3)
        assert not nb.is_narrow  # ~80% wide

    def test_narrow_flag(self):
        # width = 160 pt / 612 ≈ 0.261 — narrow
        block = _make_para_block("short", 12, 100, 172, 130)
        page  = _page_with_blocks([block])
        result = _filter_and_normalize(page)
        assert len(result) == 1
        assert result[0].is_narrow

    def test_occupancy_histogram_single_wide_block(self):
        blocks = [_NormBlock(nx0=0.1, ny0=0.0, nx1=0.9, ny1=0.1,
                             width=0.8, height=0.1, center_x=0.5, area=0.08, is_narrow=False)]
        hist = compute_horizontal_occupancy(blocks, bins=10)
        assert len(hist) == 10
        # All bins from 1 to 8 (0-indexed) should be non-zero
        assert all(v > 0 for v in hist[1:9])
        # Edge bins 0 and 9 partially or not covered
        assert hist[0] == 0.0  # starts at 0.1, bin 0 is [0.0,0.1)
        assert hist[9] == 0.0  # ends at 0.9, bin 9 is [0.9,1.0)

    def test_sidebar_persistence_one_block_low(self):
        """A single narrow left block should produce low persistence (1/6 ≈ 0.167)."""
        blocks = [_NormBlock(nx0=0.0, ny0=0.05, nx1=0.25, ny1=0.15,
                             width=0.25, height=0.10, center_x=0.125, area=0.025, is_narrow=True)]
        pers = compute_sidebar_persistence(blocks, side="left")
        assert pers == pytest.approx(1 / 6, rel=0.01)

    def test_sidebar_persistence_many_blocks_high(self):
        """Four narrow left blocks spread across four different bands → 4/6 ≈ 0.667."""
        blocks = []
        for i in range(4):
            mid_y = (i * 2 + 1) / 12.0   # places in band i×2 ÷ 6
            blocks.append(_NormBlock(
                nx0=0.0, ny0=mid_y - 0.02, nx1=0.25, ny1=mid_y + 0.02,
                width=0.25, height=0.04, center_x=0.125, area=0.01, is_narrow=True,
            ))
        pers = compute_sidebar_persistence(blocks, side="left")
        assert pers >= 4 / 6 - 0.01

    def test_sidebar_persistence_right_side(self):
        """Narrow blocks on the right side are counted correctly."""
        blocks = [
            _NormBlock(nx0=0.72, ny0=0.1, nx1=0.97, ny1=0.2,
                       width=0.25, height=0.1, center_x=0.845, area=0.025, is_narrow=True),
            _NormBlock(nx0=0.72, ny0=0.4, nx1=0.97, ny1=0.5,
                       width=0.25, height=0.1, center_x=0.845, area=0.025, is_narrow=True),
            _NormBlock(nx0=0.72, ny0=0.7, nx1=0.97, ny1=0.8,
                       width=0.25, height=0.1, center_x=0.845, area=0.025, is_narrow=True),
        ]
        assert compute_sidebar_persistence(blocks, side="right") >= 3 / 6

    def test_persistence_empty_blocks(self):
        assert compute_sidebar_persistence([], side="left") == 0.0
        assert compute_sidebar_persistence([], side="right") == 0.0


# ---------------------------------------------------------------------------
# TestTopologyPageClassification — page-level classification
# ---------------------------------------------------------------------------

class TestTopologyPageClassification:

    def test_single_column_layout(self):
        page = _page_with_blocks(_wide_blocks())
        result = classify_page_topology(page)
        assert result.topology == TOPOLOGY_LINEAR

    def test_sidebar_left_layout(self):
        page = _page_with_blocks(_sidebar_left_blocks())
        result = classify_page_topology(page)
        assert result.topology == TOPOLOGY_SB_LEFT

    def test_sidebar_right_layout(self):
        page = _page_with_blocks(_sidebar_right_blocks())
        result = classify_page_topology(page)
        assert result.topology == TOPOLOGY_SB_RIGHT

    def test_balanced_two_column_layout(self):
        page = _page_with_blocks(_balanced_two_column_blocks())
        result = classify_page_topology(page)
        assert result.topology == TOPOLOGY_BALANCED

    def test_empty_page_is_ambiguous(self):
        page = _page_with_blocks([])
        result = classify_page_topology(page)
        assert result.topology == TOPOLOGY_AMBIG

    def test_single_narrow_contact_block_does_not_trigger_sidebar(self):
        """One tiny top-left block alone must NOT classify as sidebar_left."""
        contact = _make_para_block("john@example.com", 12, 30, 160, 50)
        page = _page_with_blocks([contact])
        result = classify_page_topology(page)
        assert result.topology != TOPOLOGY_SB_LEFT

    def test_balanced_not_misclassified_as_sidebar(self):
        """Medium-width columns should NOT be misclassified as sidebar."""
        page = _page_with_blocks(_balanced_two_column_blocks())
        result = classify_page_topology(page)
        assert result.topology not in (TOPOLOGY_SB_LEFT, TOPOLOGY_SB_RIGHT)

    def test_signals_returned_in_result(self):
        page = _page_with_blocks(_wide_blocks())
        result = classify_page_topology(page)
        assert "left_persistence" in result.signals
        assert "right_persistence" in result.signals
        assert "wide_area_frac" in result.signals
        assert result.signals["total_blocks"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# TestTopologyDocumentClassification — document-level aggregation
# ---------------------------------------------------------------------------

class TestTopologyDocumentClassification:

    def test_single_page_single_column(self):
        doc = _doc_with_page(_page_with_blocks(_wide_blocks()))
        tc = classify_topology(doc)
        assert tc.doc_topology == TOPOLOGY_LINEAR
        assert tc.confidence > 0.0
        assert len(tc.page_topologies) == 1

    def test_single_page_sidebar_left(self):
        doc = _doc_with_page(_page_with_blocks(_sidebar_left_blocks()))
        tc = classify_topology(doc)
        assert tc.doc_topology == TOPOLOGY_SB_LEFT

    def test_empty_doc_is_ambiguous(self):
        doc = ExtractedDoc(
            path="synthetic",
            pages=[],
            features=_make_features(page_count=0),
            headings=[],
            bullet_count=0,
        )
        tc = classify_topology(doc)
        assert tc.doc_topology == TOPOLOGY_AMBIG
        assert tc.confidence == 0.0

    def test_no_blocks_doc_is_ambiguous(self):
        """Doc with one page but zero blocks → ambiguous."""
        doc = _make_extracted()   # uses _make_extracted() from above with no blocks
        tc = classify_topology(doc)
        assert tc.doc_topology == TOPOLOGY_AMBIG

    def test_signals_aggregated(self):
        doc = _doc_with_page(_page_with_blocks(_wide_blocks()))
        tc = classify_topology(doc)
        assert "left_persistence" in tc.signals

    def test_page_topologies_list_length(self):
        page1 = _page_with_blocks(_wide_blocks())
        page2 = _page_with_blocks(_wide_blocks())
        page2.page_number = 2
        doc = ExtractedDoc(
            path="synthetic",
            pages=[page1, page2],
            features=_make_features(page_count=2),
            headings=[],
            bullet_count=0,
        )
        tc = classify_topology(doc)
        assert len(tc.page_topologies) == 2


# ---------------------------------------------------------------------------
# TestTopologyPreservation — preservation scoring
# ---------------------------------------------------------------------------

def _tc(topology: str, confidence: float = 0.80) -> TopologyClassification:
    return TopologyClassification(
        doc_topology=topology,
        confidence=confidence,
        page_topologies=[topology],
        notes=[],
        signals={},
    )


class TestTopologyPreservation:

    def test_same_class_high_score(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_LINEAR), _tc(TOPOLOGY_LINEAR))
        assert score >= 0.70

    def test_same_class_perfect_confidence_near_one(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_SB_LEFT, 1.0), _tc(TOPOLOGY_SB_LEFT, 1.0))
        assert score == pytest.approx(1.0)

    def test_both_ambiguous_soft_score(self):
        score, ev = topology_preservation_score(_tc(TOPOLOGY_AMBIG), _tc(TOPOLOGY_AMBIG))
        assert score == pytest.approx(0.60)
        assert any("ambiguous" in e for e in ev)

    def test_one_ambiguous_neutral(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_LINEAR), _tc(TOPOLOGY_AMBIG))
        assert score == pytest.approx(0.55)

    def test_sidebar_flip_strong_penalty(self):
        score, ev = topology_preservation_score(_tc(TOPOLOGY_SB_LEFT), _tc(TOPOLOGY_SB_RIGHT))
        assert score == pytest.approx(0.25)
        assert any("→" in e for e in ev)

    def test_linear_to_sidebar_strong_penalty(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_LINEAR), _tc(TOPOLOGY_SB_LEFT))
        assert score == pytest.approx(0.10)

    def test_linear_to_balanced_moderate_penalty(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_LINEAR), _tc(TOPOLOGY_BALANCED))
        assert score == pytest.approx(0.25)

    def test_sidebar_to_balanced_moderate_penalty(self):
        score, _ = topology_preservation_score(_tc(TOPOLOGY_SB_LEFT), _tc(TOPOLOGY_BALANCED))
        assert score == pytest.approx(0.50)

    def test_evidence_includes_topology_names(self):
        _, ev = topology_preservation_score(_tc(TOPOLOGY_SB_LEFT), _tc(TOPOLOGY_LINEAR))
        assert any(TOPOLOGY_SB_LEFT in e and TOPOLOGY_LINEAR in e for e in ev)


# ---------------------------------------------------------------------------
# TestTopologyIntegration — wired into score_layout / report
# ---------------------------------------------------------------------------

class TestTopologyIntegration:

    def test_score_layout_exposes_topology_fields(self):
        """score_layout sets src_topology / out_topology on LayoutScore."""
        from tailor.eval.changed_content.scorer import score_layout

        src = _make_extracted()
        out = _make_extracted()
        score, _ = score_layout(src, out, "source text here", "generated text here")
        # Synthetic docs have no blocks → both will be ambiguous
        assert hasattr(score, "src_topology")
        assert hasattr(score, "out_topology")
        assert hasattr(score, "topology_confidence")

    def test_topology_fields_in_case_report(self):
        """build_case_report includes topology raw metrics."""
        from dataclasses import asdict
        from tailor.eval.changed_content.scorer import score_layout

        src = _make_extracted()
        out = _make_extracted()
        score, _ = score_layout(src, out, "source text here", "generated text here")

        result = _make_case_result(
            metric_breakdown={**asdict(score)},
            layout_score=score.composite,
        )
        report = build_case_report(result)
        rm = report["raw_metrics"]
        assert "src_topology" in rm
        assert "out_topology" in rm
        assert "topology_confidence" in rm

    def test_topology_fields_in_summary_text(self):
        """Summary text includes a Topology line."""
        from dataclasses import asdict
        from tailor.eval.changed_content.scorer import score_layout

        src = _make_extracted()
        out = _make_extracted()
        score, _ = score_layout(src, out, "source text here", "generated text here")
        result = _make_case_result(
            metric_breakdown={**asdict(score)},
            layout_score=score.composite,
        )
        report = build_case_report(result)
        from tailor.eval.changed_content.report import _case_summary_text
        txt = _case_summary_text(report)
        assert "Topology:" in txt

    def test_taxonomy_uses_rich_topology_for_class_f(self):
        """When src/out topology differ (non-ambiguous), taxonomy emits Class F."""
        from tailor.eval.changed_content.taxonomy import classify_failures

        score = _make_score(
            src_topology=TOPOLOGY_SB_LEFT,
            out_topology=TOPOLOGY_LINEAR,
            # Keep column counts matching so old fallback would NOT trigger
            src_column_count=1,
            out_column_count=1,
            column_confidence="high",
        )
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE in fc.classes

    def test_taxonomy_ambiguous_topology_falls_back_to_column_count(self):
        """When both topologies are ambiguous, taxonomy falls back to column count."""
        from tailor.eval.changed_content.taxonomy import classify_failures

        score = _make_score(
            src_topology=TOPOLOGY_AMBIG,
            out_topology=TOPOLOGY_AMBIG,
            src_column_count=2,
            out_column_count=1,
            column_confidence="high",
        )
        fc = classify_failures(score)
        assert FailureClass.F_TOPOLOGY_COLLAPSE in fc.classes
