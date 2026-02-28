"""Acceptance tests for evidence-ledger-driven Phase 2 validation.

Covers the four spec acceptance tests (F1–F4) plus helpers and backward compat.

All tests are deterministic — no LLM calls.
"""

from __future__ import annotations

import pytest

from tailor.phase2_validator import (
    LEDGER_ENTRY_MISSING_PREFIX,
    LEDGER_MISMATCH_ERROR_PREFIX,
    LEDGER_SPAN_NOT_FOUND_PREFIX,
    _build_ledger_index,
    _check_ledger_honesty,
    _check_ledger_requirement,
    apply_judge_to_validation,
    validate_phase2_output,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DATE = "February 27, 2026"


def _make_packet(
    *,
    must_keep_metrics: list[str] | None = None,
    must_include_skills: list[str] | None = None,
    unsafe_jd_nouns: list[str] | None = None,
    role_name: str = "Senior Engineer | Acme Corp",
) -> dict:
    # Mechanism enforcement is disabled (min=0) so ledger tests stay focused on
    # skill/metric/unsafe-noun checks only.
    return {
        "must_keep_metrics": must_keep_metrics or [],
        "must_include_skills": must_include_skills or [],
        "must_surface_arch_mechanisms": [],
        "arch_mechanisms_primary": [],
        "arch_mechanisms_backstop": [],
        "allowed_skill_pool": ["Java", "Kafka", "Redis", "Docker"],
        "unsafe_jd_nouns": unsafe_jd_nouns or [],
        "role_priorities": {role_name: "high"},
        "role_source_bullet_counts": {role_name: 5},
        "role_source_char_counts": {role_name: 400},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
        },
        "role_density_shortfall_allowance": {},
    }


def _make_resume(
    bullets: list[str] | None = None,
    skills: str = "Java, Redis",
    role: str = "Senior Engineer | Acme Corp | 2022 - Present",
) -> str:
    bullet_lines = "\n".join(f"- {b}" for b in (bullets or ["B1", "B2", "B3", "B4"]))
    return (
        "Professional Summary\nSenior engineer.\n\n"
        f"Experience\n{role}\n{bullet_lines}\n\n"
        f"Technical Skills\n{skills}"
    )


def _make_cover(date_str: str = _DATE) -> str:
    return f"{date_str}\n\nDear Hiring Manager."


def _ledger(*entries: dict) -> dict:
    """Build a minimal evidence_ledger dict."""
    return {"entries": list(entries)}


def _entry(
    id_: str,
    kind: str,
    target: str,
    location: str,
    exact_span: str,
    notes: str = "",
) -> dict:
    return {
        "id": id_,
        "kind": kind,
        "target": target,
        "location": location,
        "exact_span": exact_span,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Unit: _build_ledger_index
# ---------------------------------------------------------------------------

class TestBuildLedgerIndex:

    def test_empty_ledger(self):
        assert _build_ledger_index({}) == {}

    def test_empty_entries(self):
        assert _build_ledger_index({"entries": []}) == {}

    def test_single_entry(self):
        e = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        idx = _build_ledger_index(_ledger(e))
        assert ("required_skill", "Kafka") in idx
        assert idx[("required_skill", "Kafka")]["id"] == "S1"

    def test_multiple_entries_indexed_by_kind_and_target(self):
        e1 = _entry("S1", "required_skill", "Java", "resume", "Java")
        e2 = _entry("M1", "required_metric", "25%", "resume", "25%")
        idx = _build_ledger_index(_ledger(e1, e2))
        assert ("required_skill", "Java") in idx
        assert ("required_metric", "25%") in idx

    def test_last_entry_wins_on_duplicate(self):
        e1 = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        e2 = _entry("S2", "required_skill", "Kafka", "cover_letter", "Kafka")
        idx = _build_ledger_index(_ledger(e1, e2))
        assert idx[("required_skill", "Kafka")]["id"] == "S2"

    def test_entry_with_empty_kind_skipped(self):
        e = {"id": "X1", "kind": "", "target": "Kafka", "location": "resume", "exact_span": "Kafka"}
        idx = _build_ledger_index(_ledger(e))
        assert len(idx) == 0

    def test_entry_with_empty_target_skipped(self):
        e = {"id": "X1", "kind": "required_skill", "target": "", "location": "resume", "exact_span": "Kafka"}
        idx = _build_ledger_index(_ledger(e))
        assert len(idx) == 0


# ---------------------------------------------------------------------------
# Unit: _check_ledger_honesty
# ---------------------------------------------------------------------------

class TestCheckLedgerHonesty:

    def test_resume_span_found(self):
        """F1: exact_span present in resume text, location correct → pass."""
        e = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        assert _check_ledger_honesty(e, "Technical Skills\nJava, Kafka, Redis", "") is True

    def test_resume_span_not_found(self):
        """F2: location=resume, exact_span not in resume → fail."""
        e = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        assert _check_ledger_honesty(e, "Technical Skills\nJava, Redis", "") is False

    def test_cover_letter_span_found(self):
        e = _entry("S1", "required_skill", "Kafka", "cover_letter", "Kafka")
        assert _check_ledger_honesty(e, "", "I have used Kafka extensively.") is True

    def test_cover_letter_span_not_found(self):
        e = _entry("S1", "required_skill", "Kafka", "cover_letter", "Kafka")
        assert _check_ledger_honesty(e, "", "I have broad experience.") is False

    def test_both_location_found_in_resume(self):
        e = _entry("S1", "required_skill", "Kafka", "both", "Kafka")
        assert _check_ledger_honesty(e, "Kafka expertise", "") is True

    def test_both_location_found_in_cover(self):
        e = _entry("S1", "required_skill", "Kafka", "both", "Kafka")
        assert _check_ledger_honesty(e, "Java expertise", "Kafka expertise") is True

    def test_both_location_not_found(self):
        e = _entry("S1", "required_skill", "Kafka", "both", "Kafka")
        assert _check_ledger_honesty(e, "Java expertise", "Java expertise") is False

    def test_case_insensitive(self):
        e = _entry("S1", "required_skill", "kafka", "resume", "kafka")
        assert _check_ledger_honesty(e, "Technical Skills\nJava, Kafka, Redis", "") is True

    def test_empty_span_always_false(self):
        e = _entry("S1", "required_skill", "Kafka", "resume", "")
        assert _check_ledger_honesty(e, "Kafka", "") is False

    def test_unknown_location_false(self):
        e = _entry("S1", "required_skill", "Kafka", "nowhere", "Kafka")
        assert _check_ledger_honesty(e, "Kafka", "Kafka") is False


# ---------------------------------------------------------------------------
# Unit: _check_ledger_requirement
# ---------------------------------------------------------------------------

class TestCheckLedgerRequirement:

    def _idx_from(self, *entries: dict) -> dict:
        return _build_ledger_index(_ledger(*entries))

    def test_no_entry_returns_entry_missing_error(self):
        idx = {}
        res = _check_ledger_requirement("required_skill", "Kafka", idx, "Java Redis", "")
        assert not res["satisfied"]
        assert not res["mismatch"]
        assert LEDGER_ENTRY_MISSING_PREFIX in res["error"]
        assert "Kafka" in res["error"]

    def test_location_missing_returns_missing_error(self):
        e = _entry("S1", "required_skill", "Kafka", "missing", "")
        idx = self._idx_from(e)
        res = _check_ledger_requirement("required_skill", "Kafka", idx, "Java Redis", "")
        assert not res["satisfied"]
        assert not res["mismatch"]
        assert "missing" in res["error"].lower()

    def test_honesty_fail_returns_span_not_found_error(self):
        e = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        idx = self._idx_from(e)
        res = _check_ledger_requirement("required_skill", "Kafka", idx, "Java Redis", "")
        assert not res["satisfied"]
        assert not res["mismatch"]
        assert LEDGER_SPAN_NOT_FOUND_PREFIX in res["error"]

    def test_exact_match_returns_satisfied(self):
        e = _entry("S1", "required_skill", "Kafka", "resume", "Kafka")
        idx = self._idx_from(e)
        res = _check_ledger_requirement("required_skill", "Kafka", idx, "Java, Kafka, Redis", "")
        assert res["satisfied"]
        assert not res["mismatch"]
        assert res["error"] == ""

    def test_case_insensitive_exact_match_satisfied(self):
        e = _entry("S1", "required_skill", "kafka", "resume", "Kafka")
        idx = self._idx_from(e)
        res = _check_ledger_requirement("required_skill", "kafka", idx, "Java, Kafka, Redis", "")
        assert res["satisfied"]

    def test_mismatch_returns_mismatch_flag(self):
        """Honesty passes but span wording differs → mismatch, no error string."""
        e = _entry("S1", "required_skill", "AI-assisted coding tools", "resume", "AI-assisted development tools")
        idx = self._idx_from(e)
        resume = "Technical Skills\nJava, AI-assisted development tools"
        res = _check_ledger_requirement("required_skill", "AI-assisted coding tools", idx, resume, "")
        assert not res["satisfied"]
        assert res["mismatch"]
        assert res["error"] == ""
        assert res["span"] == "AI-assisted development tools"

    def test_mismatch_entry_id_preserved(self):
        e = _entry("REQ_SKILL_7", "required_skill", "AI-assisted coding tools", "resume", "AI-assisted development tools")
        idx = self._idx_from(e)
        resume = "Technical Skills\nAI-assisted development tools"
        res = _check_ledger_requirement("required_skill", "AI-assisted coding tools", idx, resume, "")
        assert res["entry_id"] == "REQ_SKILL_7"


# ---------------------------------------------------------------------------
# F1: Ledger honesty pass
# ---------------------------------------------------------------------------

class TestLedgerHonestyPass:
    """F1: exact_span present in resume text, location correct → no honesty errors."""

    def test_skill_exact_match_no_error(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger(_entry("REQ_SKILL_1", "required_skill", "Kafka", "resume", "Kafka"))
        resume = _make_resume(skills="Java, Kafka, Redis")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        skill_errors = [e for e in report["errors"] if "Kafka" in e and "missing" in e.lower()]
        assert skill_errors == [], f"Unexpected skill errors: {skill_errors}"

    def test_metric_exact_match_no_error(self):
        wp = _make_packet(must_keep_metrics=["25%"])
        ledger = _ledger(_entry("REQ_METRIC_1", "required_metric", "25%", "resume", "25%"))
        resume = _make_resume(bullets=["Improved throughput by 25%", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        metric_errors = [e for e in report["errors"] if "25%" in e]
        assert metric_errors == [], f"Unexpected metric errors: {metric_errors}"

    def test_cover_letter_location_honesty_pass(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger(
            _entry("REQ_SKILL_1", "required_skill", "Kafka", "cover_letter", "Kafka")
        )
        resume = _make_resume()
        cover = f"{_DATE}\n\nI have deep Kafka expertise."
        report = validate_phase2_output(wp, resume, cover, _DATE, evidence_ledger=ledger)
        honesty_errors = [e for e in report["errors"] if LEDGER_SPAN_NOT_FOUND_PREFIX in e]
        assert honesty_errors == [], f"Unexpected honesty errors: {honesty_errors}"


# ---------------------------------------------------------------------------
# F2: Ledger lie caught
# ---------------------------------------------------------------------------

class TestLedgerLieCaught:
    """F2: location=resume, exact_span not in resume → LEDGER_SPAN_NOT_FOUND error."""

    def test_skill_span_not_in_resume(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger(_entry("REQ_SKILL_1", "required_skill", "Kafka", "resume", "Kafka"))
        resume = _make_resume(skills="Java, Redis")  # Kafka absent
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any(LEDGER_SPAN_NOT_FOUND_PREFIX in e for e in report["errors"])

    def test_metric_span_not_in_resume(self):
        wp = _make_packet(must_keep_metrics=["25%"])
        ledger = _ledger(_entry("REQ_METRIC_1", "required_metric", "25%", "resume", "25%"))
        resume = _make_resume()  # no 25% in bullets
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any(LEDGER_SPAN_NOT_FOUND_PREFIX in e for e in report["errors"])

    def test_cover_letter_span_not_in_cover(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger(
            _entry("REQ_SKILL_1", "required_skill", "Kafka", "cover_letter", "Kafka")
        )
        resume = _make_resume()
        cover = f"{_DATE}\n\nDear Hiring Manager, no mention of the messaging system."
        report = validate_phase2_output(wp, resume, cover, _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any(LEDGER_SPAN_NOT_FOUND_PREFIX in e for e in report["errors"])

    def test_ledger_entry_missing_fires_error(self):
        """Required skill has no ledger entry at all → LEDGER_ENTRY_MISSING."""
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger()  # empty
        resume = _make_resume(skills="Java, Kafka")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any(LEDGER_ENTRY_MISSING_PREFIX in e for e in report["errors"])

    def test_ledger_says_missing_fires_error(self):
        """Ledger entry with location='missing' fires error even if text has it."""
        wp = _make_packet(must_include_skills=["Kafka"])
        ledger = _ledger(
            _entry("REQ_SKILL_1", "required_skill", "Kafka", "missing", "")
        )
        resume = _make_resume(skills="Java, Kafka")  # Kafka present but ledger says missing
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any("missing" in e.lower() for e in report["errors"])


# ---------------------------------------------------------------------------
# F3: Rewording allowed (with judge)
# ---------------------------------------------------------------------------

class TestRewordingWithJudge:
    """F3: rewording where exact_span ≠ target → mismatch error + judge candidate.
    After apply_judge_to_validation with verdict=yes → validation passes.
    """

    def test_mismatch_generates_error_and_judge_candidate(self):
        wp = _make_packet(must_include_skills=["AI-assisted coding tools"])
        ledger = _ledger(_entry(
            "REQ_SKILL_1", "required_skill",
            "AI-assisted coding tools", "resume", "AI-assisted development tools",
        ))
        resume = _make_resume(skills="Java, AI-assisted development tools")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)

        assert not report["ok"]
        mismatch_errors = [e for e in report["errors"] if LEDGER_MISMATCH_ERROR_PREFIX in e]
        assert len(mismatch_errors) == 1, f"Expected 1 mismatch error, got: {mismatch_errors}"
        assert len(report["judge_candidates"]) == 1
        candidate = report["judge_candidates"][0]
        assert candidate["id"] == "REQ_SKILL_1"
        assert candidate["target"] == "AI-assisted coding tools"
        assert candidate["exact_span"] == "AI-assisted development tools"

    def test_judge_approval_clears_mismatch_error(self):
        """F3: judge says yes → validation passes."""
        wp = _make_packet(must_include_skills=["AI-assisted coding tools"])
        ledger = _ledger(_entry(
            "REQ_SKILL_1", "required_skill",
            "AI-assisted coding tools", "resume", "AI-assisted development tools",
        ))
        resume = _make_resume(skills="Java, AI-assisted development tools")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]

        # Simulate judge saying yes
        updated = apply_judge_to_validation(report, {"REQ_SKILL_1": True})
        assert updated["ok"], f"Expected ok after judge, errors: {updated['errors']}"
        assert updated["errors"] == []

    def test_judge_rejection_keeps_mismatch_error(self):
        """Judge says no → error remains."""
        wp = _make_packet(must_include_skills=["AI-assisted coding tools"])
        ledger = _ledger(_entry(
            "REQ_SKILL_1", "required_skill",
            "AI-assisted coding tools", "resume", "something completely different",
        ))
        resume = _make_resume(skills="Java, something completely different")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]

        updated = apply_judge_to_validation(report, {"REQ_SKILL_1": False})
        assert not updated["ok"]
        assert any(LEDGER_MISMATCH_ERROR_PREFIX in e for e in updated["errors"])

    def test_judge_verdict_stored_in_candidate(self):
        wp = _make_packet(must_include_skills=["AI-assisted coding tools"])
        ledger = _ledger(_entry(
            "REQ_SKILL_1", "required_skill",
            "AI-assisted coding tools", "resume", "AI-assisted development tools",
        ))
        resume = _make_resume(skills="Java, AI-assisted development tools")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        updated = apply_judge_to_validation(report, {"REQ_SKILL_1": True})
        candidate = updated["judge_candidates"][0]
        assert candidate["judge_verdict"] is True

    def test_partial_judge_approval(self):
        """Two mismatches; judge approves one → only one error removed."""
        wp = _make_packet(must_include_skills=["Skill A", "Skill B"])
        ledger = _ledger(
            _entry("REQ_SKILL_1", "required_skill", "Skill A", "resume", "Skill Alpha"),
            _entry("REQ_SKILL_2", "required_skill", "Skill B", "resume", "Skill Beta"),
        )
        resume = _make_resume(skills="Skill Alpha, Skill Beta")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert len(report["judge_candidates"]) == 2

        updated = apply_judge_to_validation(report, {"REQ_SKILL_1": True, "REQ_SKILL_2": False})
        mismatch_errors = [e for e in updated["errors"] if LEDGER_MISMATCH_ERROR_PREFIX in e]
        # Skill A approved → removed; Skill B rejected → remains
        assert len(mismatch_errors) == 1
        assert "REQ_SKILL_2" in mismatch_errors[0]

    def test_metric_mismatch_judge_eligible(self):
        """Metric mismatch (e.g. 'twenty-five percent') is also judge-eligible."""
        wp = _make_packet(must_keep_metrics=["25%"])
        ledger = _ledger(_entry(
            "REQ_METRIC_1", "required_metric",
            "25%", "resume", "twenty-five percent improvement",
        ))
        resume = _make_resume(bullets=["Improved latency by twenty-five percent improvement", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert any(LEDGER_MISMATCH_ERROR_PREFIX in e for e in report["errors"])
        assert len(report["judge_candidates"]) == 1

        updated = apply_judge_to_validation(report, {"REQ_METRIC_1": True})
        metric_errors = [e for e in updated["errors"] if "25%" in e or LEDGER_MISMATCH_ERROR_PREFIX in e]
        assert metric_errors == []


# ---------------------------------------------------------------------------
# F4: Unsafe noun still strict
# ---------------------------------------------------------------------------

class TestUnsafeNounStrict:
    """F4: unsafe noun present in doc → fail, regardless of ledger claims."""

    def test_unsafe_noun_in_resume_ledger_claims_missing(self):
        """Unsafe noun appears, ledger claims location='missing' → fail."""
        wp = _make_packet(unsafe_jd_nouns=["ServiceNow"])
        ledger = _ledger(
            _entry("UNSAFE_1", "unsafe_noun_check", "ServiceNow", "missing", "")
        )
        resume = _make_resume(bullets=["Configured ServiceNow workflows", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any("ServiceNow" in e for e in report["errors"])

    def test_unsafe_noun_absent_ledger_says_present_fires_error(self):
        """Ledger claims unsafe noun is present (location='resume') → error."""
        wp = _make_packet(unsafe_jd_nouns=["ServiceNow"])
        ledger = _ledger(
            _entry("UNSAFE_1", "unsafe_noun_check", "ServiceNow", "resume", "ServiceNow")
        )
        resume = _make_resume()  # ServiceNow absent from text
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        assert not report["ok"]
        assert any("LEDGER_UNSAFE_PRESENT" in e for e in report["errors"])

    def test_unsafe_noun_absent_ledger_says_missing_ok(self):
        """Unsafe noun absent, ledger correctly says missing → no unsafe error."""
        wp = _make_packet(unsafe_jd_nouns=["ServiceNow"])
        ledger = _ledger(
            _entry("UNSAFE_1", "unsafe_noun_check", "ServiceNow", "missing", "")
        )
        resume = _make_resume()
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        unsafe_errors = [e for e in report["errors"] if "ServiceNow" in e]
        assert unsafe_errors == [], f"Unexpected unsafe errors: {unsafe_errors}"

    def test_unsafe_noun_judge_cannot_override(self):
        """Judge candidates should not include unsafe_noun_check items."""
        wp = _make_packet(unsafe_jd_nouns=["ServiceNow"])
        ledger = _ledger(
            _entry("UNSAFE_1", "unsafe_noun_check", "ServiceNow", "resume", "ServiceNow")
        )
        resume = _make_resume(bullets=["Configured ServiceNow", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE, evidence_ledger=ledger)
        # No unsafe noun entries in judge_candidates
        unsafe_candidates = [c for c in report["judge_candidates"] if c["kind"] == "unsafe_noun_check"]
        assert unsafe_candidates == []


# ---------------------------------------------------------------------------
# Backward compatibility: no ledger → old verbatim behaviour unchanged
# ---------------------------------------------------------------------------

class TestBackwardCompatibilityNoLedger:
    """When evidence_ledger is None the old verbatim checks run unchanged."""

    def test_no_ledger_skill_verbatim_check(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        resume = _make_resume(skills="Java, Redis")  # Kafka absent
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert not report["ok"]
        assert any("Kafka" in e for e in report["errors"])

    def test_no_ledger_skill_present(self):
        wp = _make_packet(must_include_skills=["Java"])
        resume = _make_resume(skills="Java, Redis")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        skill_errors = [e for e in report["errors"] if "Java" in e and "missing" in e.lower()]
        assert skill_errors == []

    def test_no_ledger_metric_verbatim_check(self):
        wp = _make_packet(must_keep_metrics=["25%"])
        resume = _make_resume()  # no 25% in bullets
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert not report["ok"]
        assert any("25%" in e for e in report["errors"])

    def test_no_ledger_judge_candidates_empty(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        resume = _make_resume(skills="Kafka")
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert report["judge_candidates"] == []

    def test_no_ledger_ledger_findings_present_false(self):
        wp = _make_packet()
        resume = _make_resume()
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert report["ledger_findings"]["ledger_present"] is False


# ---------------------------------------------------------------------------
# apply_judge_to_validation edge cases
# ---------------------------------------------------------------------------

class TestApplyJudgeToValidation:

    def _base_report(self, mismatch_error: str = None) -> dict:
        errors = [mismatch_error or f"{LEDGER_MISMATCH_ERROR_PREFIX}[REQ_SKILL_1]: target 'X', span 'Y' in resume (judge-eligible)"]
        return {
            "ok": False,
            "errors": errors,
            "warnings": [],
            "judge_candidates": [
                {"id": "REQ_SKILL_1", "kind": "required_skill", "target": "X", "location": "resume", "exact_span": "Y"}
            ],
        }

    def test_empty_verdicts_returns_unchanged(self):
        report = self._base_report()
        result = apply_judge_to_validation(report, {})
        assert result is report

    def test_all_rejected_keeps_errors(self):
        report = self._base_report()
        result = apply_judge_to_validation(report, {"REQ_SKILL_1": False})
        assert not result["ok"]
        assert len(result["errors"]) == 1

    def test_approval_makes_ok(self):
        report = self._base_report()
        result = apply_judge_to_validation(report, {"REQ_SKILL_1": True})
        assert result["ok"]
        assert result["errors"] == []

    def test_non_mismatch_errors_preserved(self):
        """Non-mismatch errors are never removed by judge."""
        report = dict(self._base_report())
        report["errors"] = [
            f"{LEDGER_MISMATCH_ERROR_PREFIX}[REQ_SKILL_1]: ...",
            "Role 'Acme' has 2 bullets; minimum is 4",
        ]
        result = apply_judge_to_validation(report, {"REQ_SKILL_1": True})
        # Mismatch removed, bullet error stays
        assert not result["ok"]
        assert len(result["errors"]) == 1
        assert "bullet" in result["errors"][0]

    def test_malformed_mismatch_prefix_kept(self):
        """Malformed prefix (no bracket) stays in the error list."""
        report = dict(self._base_report())
        report["errors"] = [f"{LEDGER_MISMATCH_ERROR_PREFIX} malformed no bracket"]
        result = apply_judge_to_validation(report, {"REQ_SKILL_1": True})
        # Malformed error must not crash and must be kept
        assert len(result["errors"]) == 1

    def test_judge_verdict_None_when_not_evaluated(self):
        """Candidate not in verdicts dict gets judge_verdict=None."""
        report = self._base_report()
        result = apply_judge_to_validation(report, {"OTHER_ID": True})
        candidate = result["judge_candidates"][0]
        assert candidate.get("judge_verdict") is None

    def test_all_approved_no_verdicts_for_nonexistent(self):
        """Approving an ID not in the error list leaves errors unchanged."""
        report = self._base_report()
        original_error_count = len(report["errors"])
        result = apply_judge_to_validation(report, {"NONEXISTENT_ID": True})
        assert len(result["errors"]) == original_error_count
