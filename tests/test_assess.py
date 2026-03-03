"""Unit tests for the assess module.

Covers:
  - positions file parsing
  - assessment response schema validation
  - integrated score math
  - per-category aggregation
  - calibration cover letter preparation
"""

import json
import os
import tempfile

import pytest

from tailor.assess import (
    _COVER_TEMPLATE_COMPANY,
    _COVER_TEMPLATE_TITLE,
    _DEFAULT_WEIGHTS,
    _SCORE_KEYS,
    _aggregate,
    _prepare_calibration_cover_letter,
    compute_integrated_score,
    load_positions,
    validate_assessment_response,
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
