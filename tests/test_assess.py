"""Unit tests for the assess module.

Covers:
  - positions file parsing
  - assessment response schema validation
  - integrated score math
  - per-category aggregation
  - calibration cover letter preparation
  - calibration data matching (company name normalization, file index, samples)
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from tailor.assess import (
    _COVER_TEMPLATE_COMPANY,
    _COVER_TEMPLATE_TITLE,
    _DEFAULT_WEIGHTS,
    _SCORE_KEYS,
    _aggregate,
    _extract_company_slug,
    _find_in_index,
    _names_match,
    _normalize_name,
    _prepare_calibration_cover_letter,
    build_calibration_index,
    compute_integrated_score,
    load_positions,
    validate_assessment_response,
    validate_calibration_coverage,
)


# ---------------------------------------------------------------------------
# load_positions
# ---------------------------------------------------------------------------

def _write_positions(lines: list[str], tmp_path) -> str:
    p = tmp_path / "positions.txt"
    p.write_text("\n".join(lines), encoding="utf-8")
    return str(p)


def test_load_positions_basic(tmp_path):
    path = _write_positions(
        ["https://example.com/job1", "https://example.com/job2"],
        tmp_path,
    )
    assert load_positions(path) == [
        "https://example.com/job1",
        "https://example.com/job2",
    ]


def test_load_positions_ignores_blank_lines(tmp_path):
    path = _write_positions(
        ["", "https://example.com/job1", "  ", "https://example.com/job2", ""],
        tmp_path,
    )
    assert load_positions(path) == [
        "https://example.com/job1",
        "https://example.com/job2",
    ]


def test_load_positions_ignores_comments(tmp_path):
    path = _write_positions(
        [
            "# batch 2026-02-28",
            "https://example.com/job1",
            "# skip this one",
            "https://example.com/job2",
        ],
        tmp_path,
    )
    assert load_positions(path) == [
        "https://example.com/job1",
        "https://example.com/job2",
    ]


def test_load_positions_empty_file(tmp_path):
    path = _write_positions(["# only comments", "", "   "], tmp_path)
    assert load_positions(path) == []


def test_load_positions_mixed(tmp_path):
    path = _write_positions(
        [
            "# start",
            "",
            "https://a.com",
            "  ",
            "# comment",
            "https://b.com",
            "https://c.com",
        ],
        tmp_path,
    )
    assert load_positions(path) == ["https://a.com", "https://b.com", "https://c.com"]


# ---------------------------------------------------------------------------
# validate_assessment_response
# ---------------------------------------------------------------------------

def _valid_response() -> dict:
    scores = {key: {"score": 8, "evidence": ["good output"]} for key in _SCORE_KEYS}
    return {
        "version": "assess_v1",
        "position": {"position_url": "https://x.com", "company": "Acme", "role_title": "SWE"},
        "scores": scores,
        "flags": {"suspected_hallucinations": [], "most_damaging_gaps": []},
        "notes": {"top_3_improvements": ["a", "b", "c"]},
    }


def test_validate_assessment_response_valid():
    assert validate_assessment_response(_valid_response()) == []


def test_validate_assessment_response_missing_key():
    data = _valid_response()
    del data["scores"]["truthfulness"]
    errs = validate_assessment_response(data)
    assert any("truthfulness" in e for e in errs)


def test_validate_assessment_response_score_out_of_range_low():
    data = _valid_response()
    data["scores"]["role_fit"]["score"] = 0
    errs = validate_assessment_response(data)
    assert any("role_fit" in e for e in errs)


def test_validate_assessment_response_score_out_of_range_high():
    data = _valid_response()
    data["scores"]["clarity_impact"]["score"] = 11
    errs = validate_assessment_response(data)
    assert any("clarity_impact" in e for e in errs)


def test_validate_assessment_response_score_float_rejected():
    data = _valid_response()
    data["scores"]["mechanism_quality"]["score"] = 7.5
    errs = validate_assessment_response(data)
    assert any("mechanism_quality" in e for e in errs)


def test_validate_assessment_response_score_string_rejected():
    data = _valid_response()
    data["scores"]["overall_readiness"]["score"] = "9"
    errs = validate_assessment_response(data)
    assert any("overall_readiness" in e for e in errs)


def test_validate_assessment_response_entry_not_dict():
    data = _valid_response()
    data["scores"]["cover_letter_effectiveness"] = 8  # int instead of dict
    errs = validate_assessment_response(data)
    assert any("cover_letter_effectiveness" in e for e in errs)


def test_validate_assessment_response_not_dict():
    errs = validate_assessment_response("not a dict")  # type: ignore[arg-type]
    assert errs


def test_validate_assessment_response_scores_not_dict():
    errs = validate_assessment_response({"scores": "bad"})
    assert errs


def test_validate_all_score_keys_validated():
    """Every key in _SCORE_KEYS must trigger an error when its score is invalid."""
    for key in _SCORE_KEYS:
        data = _valid_response()
        data["scores"][key]["score"] = 0
        errs = validate_assessment_response(data)
        assert errs, f"Expected validation error for key {key!r}"


# ---------------------------------------------------------------------------
# compute_integrated_score
# ---------------------------------------------------------------------------

def test_integrated_score_all_tens():
    scores = {k: 10 for k in _SCORE_KEYS}
    result = compute_integrated_score(scores, _DEFAULT_WEIGHTS)
    assert result == 10.0


def test_integrated_score_all_ones():
    scores = {k: 1 for k in _SCORE_KEYS}
    result = compute_integrated_score(scores, _DEFAULT_WEIGHTS)
    assert result == 1.0


def test_integrated_score_weights_sum_to_one():
    total = sum(_DEFAULT_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9


def test_integrated_score_manual():
    # truthfulness=10 (w=0.20), role_fit=5 (w=0.20), rest=0 (w sum=0.60)
    weights = {"truthfulness": 0.20, "role_fit": 0.20, "other": 0.60}
    scores = {"truthfulness": 10, "role_fit": 5, "other": 0}
    # (10*0.20 + 5*0.20 + 0*0.60) / 1.0 = 3.0
    result = compute_integrated_score(scores, weights)
    assert result == 3.0


def test_integrated_score_empty_scores():
    result = compute_integrated_score({}, _DEFAULT_WEIGHTS)
    assert result == 0.0


def test_integrated_score_subset_keys():
    """When only some keys are present, weight is re-normalised to those keys."""
    weights = {"truthfulness": 0.5, "role_fit": 0.5}
    scores = {"truthfulness": 8}
    # Only truthfulness matches; weight = 0.5; total_weight = 0.5
    # weighted_sum = 8 * 0.5 = 4.0; result = 4.0 / 0.5 = 8.0
    result = compute_integrated_score(scores, weights)
    assert result == 8.0


def test_integrated_score_rounding():
    weights = {"a": 1.0}
    scores = {"a": 7}
    result = compute_integrated_score(scores, weights)
    assert result == 7.0


# ---------------------------------------------------------------------------
# _aggregate
# ---------------------------------------------------------------------------

def _make_entry(scores: dict[str, int]) -> dict:
    return {"company": "Acme", "role_title": "SWE", "scores": scores}


def test_aggregate_single_entry():
    entry = _make_entry({k: 8 for k in _SCORE_KEYS})
    result = _aggregate([entry], _DEFAULT_WEIGHTS)
    for k in _SCORE_KEYS:
        assert result["category_averages"][k] == 8.0
    assert result["integrated_score"] == 8.0


def test_aggregate_two_entries():
    e1 = _make_entry({k: 6 for k in _SCORE_KEYS})
    e2 = _make_entry({k: 8 for k in _SCORE_KEYS})
    result = _aggregate([e1, e2], _DEFAULT_WEIGHTS)
    for k in _SCORE_KEYS:
        assert result["category_averages"][k] == 7.0


def test_aggregate_missing_key_in_entry():
    """A missing score key in one entry is excluded from that key's average."""
    e1 = _make_entry({k: 10 for k in _SCORE_KEYS})
    e2_scores = {k: 10 for k in _SCORE_KEYS}
    del e2_scores["truthfulness"]
    e2 = _make_entry(e2_scores)
    result = _aggregate([e1, e2], _DEFAULT_WEIGHTS)
    # truthfulness: only e1 contributes → average = 10
    assert result["category_averages"]["truthfulness"] == 10.0


def test_aggregate_empty():
    result = _aggregate([], _DEFAULT_WEIGHTS)
    assert result["integrated_score"] == 0.0
    for k in _SCORE_KEYS:
        assert result["category_averages"][k] == 0.0


def test_aggregate_integrated_score_matches_compute():
    entries = [_make_entry({k: i + 1 for i, k in enumerate(_SCORE_KEYS)})]
    result = _aggregate(entries, _DEFAULT_WEIGHTS)
    expected = compute_integrated_score(result["category_averages"], _DEFAULT_WEIGHTS)
    assert result["integrated_score"] == expected


# ---------------------------------------------------------------------------
# _prepare_calibration_cover_letter
# ---------------------------------------------------------------------------

def _cover_with(date_str: str = "", company: str = "", title: str = "") -> str:
    """Build a minimal cover letter string containing the given tokens."""
    parts = []
    if date_str:
        parts.append(date_str)
    parts.append(f"I am applying for the {title} position at {company}.")
    return "\n".join(parts)


def test_calibration_date_replaced():
    text = _cover_with(date_str="February 16th, 2026", company=_COVER_TEMPLATE_COMPANY, title=_COVER_TEMPLATE_TITLE)
    result = _prepare_calibration_cover_letter(text, _COVER_TEMPLATE_COMPANY, _COVER_TEMPLATE_TITLE, "March 5, 2026")
    assert "March 5, 2026" in result
    assert "February" not in result


def test_calibration_company_replaced():
    text = _cover_with(company=_COVER_TEMPLATE_COMPANY, title=_COVER_TEMPLATE_TITLE)
    result = _prepare_calibration_cover_letter(text, "Acme Corp", _COVER_TEMPLATE_TITLE, "March 5, 2026")
    assert "Acme Corp" in result
    assert _COVER_TEMPLATE_COMPANY not in result


def test_calibration_title_replaced():
    text = _cover_with(company=_COVER_TEMPLATE_COMPANY, title=_COVER_TEMPLATE_TITLE)
    result = _prepare_calibration_cover_letter(text, _COVER_TEMPLATE_COMPANY, "Engineering Manager", "March 5, 2026")
    assert "Engineering Manager" in result
    assert _COVER_TEMPLATE_TITLE not in result


def test_calibration_all_three_replaced():
    text = _cover_with(date_str="February 16h, 2026", company=_COVER_TEMPLATE_COMPANY, title=_COVER_TEMPLATE_TITLE)
    result = _prepare_calibration_cover_letter(text, "Fingerprint", "Engineering Manager", "March 2, 2026")
    assert "Fingerprint" in result
    assert "Engineering Manager" in result
    assert "March 2, 2026" in result
    assert "February" not in result
    assert _COVER_TEMPLATE_COMPANY not in result
    assert _COVER_TEMPLATE_TITLE not in result


def test_calibration_no_date_unchanged():
    text = f"I apply for {_COVER_TEMPLATE_TITLE} at {_COVER_TEMPLATE_COMPANY}."
    result = _prepare_calibration_cover_letter(text, "NewCo", "Director", "March 5, 2026")
    # No date was present, so no date replacement artefacts
    assert "March 5, 2026" not in result
    assert "NewCo" in result
    assert "Director" in result


def test_calibration_various_date_formats():
    """Regex should handle ordinal suffixes and the typo 'h' seen in the template."""
    for date_str in ["February 16h, 2026", "March 5, 2026", "January 1st, 2026", "December 31st, 2025"]:
        text = f"{date_str}\nSome content."
        result = _prepare_calibration_cover_letter(text, "Co", "Role", "April 1, 2026")
        assert "April 1, 2026" in result, f"Date not replaced for input: {date_str!r}"
        assert date_str not in result, f"Original date still present for input: {date_str!r}"


# ---------------------------------------------------------------------------
# _normalize_name
# ---------------------------------------------------------------------------

def test_normalize_name_basic():
    assert _normalize_name("Plata Card") == ["plata", "card"]


def test_normalize_name_uppercase():
    assert _normalize_name("NEOGOV") == ["neogov"]


def test_normalize_name_underscores():
    assert _normalize_name("Jonas_Software") == ["jonas", "software"]


def test_normalize_name_empty():
    assert _normalize_name("") == []


def test_normalize_name_special_chars_stripped():
    assert _normalize_name("Co. Ltd.") == ["co", "ltd"]


def test_normalize_name_numbers_kept():
    assert _normalize_name("Web3 Corp") == ["web3", "corp"]


# ---------------------------------------------------------------------------
# _names_match
# ---------------------------------------------------------------------------

def test_names_match_exact():
    assert _names_match("NEOGOV", "NEOGOV")


def test_names_match_case_insensitive():
    assert _names_match("neogov", "NEOGOV")


def test_names_match_partial_shorter_first():
    assert _names_match("Plata", "Plata Card")


def test_names_match_partial_longer_first():
    assert _names_match("Plata Card", "Plata")


def test_names_match_spaces_vs_underscores():
    assert _names_match("Jonas Software", "Jonas_Software")


def test_names_match_no_match():
    assert not _names_match("NEOGOV", "Fingerprint")


def test_names_match_empty_a():
    assert not _names_match("", "NEOGOV")


def test_names_match_empty_b():
    assert not _names_match("NEOGOV", "")


def test_names_match_single_word_subset():
    assert _names_match("League", "League Healthcare")


def test_names_match_unrelated_words():
    assert not _names_match("Alpha Beta", "Gamma Delta")


# ---------------------------------------------------------------------------
# _extract_company_slug
# ---------------------------------------------------------------------------

def test_extract_company_slug_resume_simple():
    assert _extract_company_slug("Leonid_Verman_Resume_NEOGOV.docx") == "NEOGOV"


def test_extract_company_slug_cover_simple():
    assert _extract_company_slug("Leonid_Verman_Cover_Letter_Fingerprint.docx") == "Fingerprint"


def test_extract_company_slug_cover_multi_word():
    assert _extract_company_slug("Leonid_Verman_Cover_Letter_Jonas_Software.docx") == "Jonas Software"


def test_extract_company_slug_resume_multi_word():
    assert _extract_company_slug("Leonid_Verman_Resume_Plata_Card.docx") == "Plata Card"


def test_extract_company_slug_plata():
    assert _extract_company_slug("Leonid_Verman_Resume_Plata.docx") == "Plata"


# ---------------------------------------------------------------------------
# _find_in_index
# ---------------------------------------------------------------------------

def test_find_in_index_exact():
    index = {"NEOGOV": "resume text", "Fingerprint": "other text"}
    assert _find_in_index("NEOGOV", index) == "resume text"


def test_find_in_index_partial_match():
    index = {"Plata": "plata text"}
    assert _find_in_index("Plata Card", index) == "plata text"


def test_find_in_index_not_found():
    index = {"NEOGOV": "text"}
    assert _find_in_index("Fingerprint", index) is None


def test_find_in_index_empty_index():
    assert _find_in_index("NEOGOV", {}) is None


# ---------------------------------------------------------------------------
# validate_calibration_coverage
# ---------------------------------------------------------------------------

def test_validate_coverage_all_present():
    resume_idx = {"NEOGOV": "r1", "Fingerprint": "r2"}
    cover_idx = {"NEOGOV": "c1", "Fingerprint": "c2"}
    errors = validate_calibration_coverage(["NEOGOV", "Fingerprint"], resume_idx, cover_idx)
    assert errors == []


def test_validate_coverage_missing_resume():
    resume_idx = {"Fingerprint": "r"}
    cover_idx = {"NEOGOV": "c", "Fingerprint": "c2"}
    errors = validate_calibration_coverage(["NEOGOV", "Fingerprint"], resume_idx, cover_idx)
    assert any("resume" in e and "NEOGOV" in e for e in errors)


def test_validate_coverage_missing_cover():
    resume_idx = {"NEOGOV": "r", "Fingerprint": "r2"}
    cover_idx = {"Fingerprint": "c"}
    errors = validate_calibration_coverage(["NEOGOV", "Fingerprint"], resume_idx, cover_idx)
    assert any("cover" in e and "NEOGOV" in e for e in errors)


def test_validate_coverage_partial_match_ok():
    resume_idx = {"Plata": "resume text"}
    cover_idx = {"Plata": "cover text"}
    errors = validate_calibration_coverage(["Plata Card"], resume_idx, cover_idx)
    assert errors == []


# ---------------------------------------------------------------------------
# build_calibration_index — directory does not exist
# ---------------------------------------------------------------------------

def test_build_calibration_index_missing_dir():
    with pytest.raises(ValueError, match="not found"):
        build_calibration_index("/nonexistent/path/that/does/not/exist")


def test_build_calibration_index_empty_dir(tmp_path):
    resume_idx, cover_idx = build_calibration_index(str(tmp_path))
    assert resume_idx == {}
    assert cover_idx == {}


# ---------------------------------------------------------------------------
# tests/samples — verify sample data consistency
# ---------------------------------------------------------------------------

# Positions used in the calibration run; company names as returned by the
# scraper for the five hiring.cafe URLs.
_SAMPLE_COMPANIES = ["NEOGOV", "Plata Card", "Lillio", "Fingerprint", "League"]
_SAMPLES_DIR = str(Path(__file__).parent / "samples")


@pytest.mark.skipif(
    not Path(_SAMPLES_DIR).is_dir(),
    reason="tests/samples directory not present",
)
class TestSamplesDirectory:
    """Verify that tests/samples contains matching files for all known positions."""

    def test_samples_dir_is_readable(self):
        docx_files = list(Path(_SAMPLES_DIR).glob("*.docx"))
        assert docx_files, "Expected at least one .docx file in tests/samples"

    def test_build_calibration_index_succeeds(self):
        resume_idx, cover_idx = build_calibration_index(_SAMPLES_DIR)
        assert len(resume_idx) > 0, "No resume files found in tests/samples"
        assert len(cover_idx) > 0, "No cover letter files found in tests/samples"

    def test_all_companies_have_resume(self):
        resume_idx, _ = build_calibration_index(_SAMPLES_DIR)
        missing = [
            c for c in _SAMPLE_COMPANIES
            if _find_in_index(c, resume_idx) is None
        ]
        assert not missing, f"No resume found for: {missing}"

    def test_all_companies_have_cover_letter(self):
        _, cover_idx = build_calibration_index(_SAMPLES_DIR)
        missing = [
            c for c in _SAMPLE_COMPANIES
            if _find_in_index(c, cover_idx) is None
        ]
        assert not missing, f"No cover letter found for: {missing}"

    def test_validate_calibration_coverage_passes(self):
        resume_idx, cover_idx = build_calibration_index(_SAMPLES_DIR)
        errors = validate_calibration_coverage(_SAMPLE_COMPANIES, resume_idx, cover_idx)
        assert errors == [], f"Coverage errors:\n" + "\n".join(errors)

    def test_resume_texts_not_empty(self):
        resume_idx, _ = build_calibration_index(_SAMPLES_DIR)
        empty = [slug for slug, text in resume_idx.items() if not text.strip()]
        assert not empty, f"Empty resume text for: {empty}"

    def test_cover_texts_not_empty(self):
        _, cover_idx = build_calibration_index(_SAMPLES_DIR)
        empty = [slug for slug, text in cover_idx.items() if not text.strip()]
        assert not empty, f"Empty cover letter text for: {empty}"

    def test_resume_count_matches_cover_count(self):
        resume_idx, cover_idx = build_calibration_index(_SAMPLES_DIR)
        assert len(resume_idx) == len(cover_idx), (
            f"Resume count ({len(resume_idx)}) != cover letter count ({len(cover_idx)})"
        )
