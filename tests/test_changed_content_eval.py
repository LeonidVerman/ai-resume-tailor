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
