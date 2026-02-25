"""Tests for the deterministic Phase 2 token compliance post-processor."""

import pytest

from tailor.token_postprocessor import (
    _canonicalize,
    _compute_missing_metrics,
    _compute_missing_skills,
    _extract_skills_section_text,
    _fix_cover_letter_date,
    _inject_metrics_into_summary,
    _inject_skills,
    _is_supported,
    postprocess_token_compliance,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_DATE = "February 24, 2026"

_MASTER = (
    "John Doe\n\n"
    "Professional Summary\n"
    "Senior engineer.\n\n"
    "Experience\nEngineer | Acme Corp | 2020-2023\n"
    "- Built 1M+ user system\n"
    "- Improved throughput by 25%\n\n"
    "Technical Skills\nJava, Kafka, Redis, Docker"
)


def _resume(summary: str = "Senior engineer.", skills: str = "Java, Kafka") -> str:
    return (
        f"Professional Summary\n{summary}\n\n"
        "Experience\nEngineer | Acme Corp | 2020-2023\n"
        "- Built distributed systems\n\n"
        f"Technical Skills\n{skills}"
    )


def _cover(date_str: str = _DATE) -> str:
    return f"{date_str}\n\nDear Hiring Manager, I am a strong fit."


def _packet(skills=None, metrics=None, pool=None) -> dict:
    return {
        "must_include_skills": skills or [],
        "must_keep_metrics": metrics or [],
        "allowed_skill_pool": pool or ["Java", "Kafka", "Redis", "Docker"],
        "unsafe_jd_nouns": [],
    }


# ---------------------------------------------------------------------------
# _canonicalize
# ---------------------------------------------------------------------------

class TestCanonicalize:
    def test_lowercase(self):
        assert _canonicalize("Redis") == "redis"

    def test_hyphens_to_space(self):
        assert _canonicalize("read-replica") == "read replica"

    def test_underscores_to_space(self):
        assert _canonicalize("fault_tolerance") == "fault tolerance"

    def test_collapse_whitespace(self):
        assert _canonicalize("a  b   c") == "a b c"

    def test_mixed(self):
        assert _canonicalize("Read-Replica_Cache") == "read replica cache"


# ---------------------------------------------------------------------------
# _is_supported
# ---------------------------------------------------------------------------

class TestIsSupported:
    def test_verbatim_in_master(self):
        assert _is_supported("Kafka", "Java, Kafka, Redis", "", {})

    def test_case_insensitive(self):
        assert _is_supported("Kafka", "java, kafka, redis", "", {})

    def test_canonical_hyphen_match(self):
        # "read-replica" should match "read replica" in source
        assert _is_supported("read-replica", "uses read replica for caching", "", {})

    def test_canonical_underscore_match(self):
        assert _is_supported("fault_tolerance", "designed for fault tolerance", "", {})

    def test_not_supported(self):
        assert not _is_supported("SNMP", "Java, Python, Redis", "Java engineer", {})

    def test_no_sources_trusts_packet(self):
        assert _is_supported("AnyToken", "", "", {})

    def test_allowed_skill_pool_verbatim(self):
        packet = {"allowed_skill_pool": ["Kafka"]}
        # Not in master/profile but in pool
        assert _is_supported("Kafka", "no kafka here", "", packet)

    def test_allowed_skill_pool_canonical(self):
        packet = {"allowed_skill_pool": ["read-replica"]}
        assert _is_supported("read replica", "nothing relevant", "", packet)

    def test_candidate_profile_text_checked(self):
        assert _is_supported("Docker", "", "container experience with Docker", {})


# ---------------------------------------------------------------------------
# _compute_missing_metrics
# ---------------------------------------------------------------------------

class TestComputeMissingMetrics:
    def test_present_exact(self):
        assert _compute_missing_metrics(_packet(metrics=["1M+"]), "scaled to 1M+ users") == []

    def test_present_via_variant(self):
        # Validator uses _metric_found which checks "over 1 million" as variant of "1M+"
        assert _compute_missing_metrics(_packet(metrics=["1M+"]), "served over 1 million users") == []

    def test_missing(self):
        assert _compute_missing_metrics(_packet(metrics=["25%"]), "improved throughput") == ["25%"]

    def test_multiple_some_present(self):
        resume = "improved by 25% and served 1M+ users"
        result = _compute_missing_metrics(_packet(metrics=["25%", "20%", "1M+"]), resume)
        assert result == ["20%"]

    def test_empty_packet(self):
        assert _compute_missing_metrics({}, "any text") == []


# ---------------------------------------------------------------------------
# _compute_missing_skills
# ---------------------------------------------------------------------------

class TestComputeMissingSkills:
    def test_present(self):
        assert _compute_missing_skills(_packet(skills=["Kafka"]), "Java, Kafka, Redis") == []

    def test_case_insensitive(self):
        assert _compute_missing_skills(_packet(skills=["Kafka"]), "kafka, java") == []

    def test_missing(self):
        assert _compute_missing_skills(_packet(skills=["Kafka"]), "Java, Redis") == ["Kafka"]

    def test_multiple_some_missing(self):
        result = _compute_missing_skills(_packet(skills=["Java", "Kafka", "Docker"]), "Java, Redis")
        assert result == ["Kafka", "Docker"]

    def test_empty_packet(self):
        assert _compute_missing_skills({}, "anything") == []


# ---------------------------------------------------------------------------
# _extract_skills_section_text
# ---------------------------------------------------------------------------

class TestExtractSkillsSectionText:
    def test_extracts_skills(self):
        resume = "Professional Summary\nOK\n\nTechnical Skills\nJava, Kafka\n\nEducation\nBSc"
        text = _extract_skills_section_text(resume)
        assert "Java" in text
        assert "Kafka" in text
        assert "BSc" not in text

    def test_no_section(self):
        resume = "Professional Summary\nOK\n\nExperience\nEngineer | Acme"
        assert _extract_skills_section_text(resume) == ""

    def test_skills_alias(self):
        resume = "Skills\nPython, Go\n\nEducation\nBSc"
        text = _extract_skills_section_text(resume)
        assert "Python" in text


# ---------------------------------------------------------------------------
# _inject_metrics_into_summary
# ---------------------------------------------------------------------------

class TestInjectMetricsIntoSummary:
    def test_appends_to_last_summary_line(self):
        resume = "Professional Summary\nSenior engineer with 15 years."
        result = _inject_metrics_into_summary(resume, ["1M+"])
        assert "Senior engineer with 15 years. (1M+)" in result

    def test_multiple_metrics_semicolon_separated(self):
        resume = "Professional Summary\nExperienced engineer."
        result = _inject_metrics_into_summary(resume, ["1M+", "25%"])
        assert "(1M+; 25%)" in result

    def test_picks_last_line_of_multiline_summary(self):
        resume = "Professional Summary\nLine one.\nLine two.\n\nExperience\n..."
        result = _inject_metrics_into_summary(resume, ["20%"])
        lines = result.split("\n")
        assert any("Line two." in ln and "(20%)" in ln for ln in lines)
        assert any(ln.strip() == "Line one." for ln in lines)

    def test_fallback_before_first_section_header(self):
        resume = "John Doe\n\nExperience\nEngineer | Acme | 2020-2023\n- Built stuff"
        result = _inject_metrics_into_summary(resume, ["25%"])
        assert "(25%)" in result
        idx_payload = result.index("(25%)")
        idx_exp = result.index("Experience")
        assert idx_payload < idx_exp

    def test_no_op_for_empty_metrics(self):
        resume = "Professional Summary\nOK."
        assert _inject_metrics_into_summary(resume, []) == resume

    def test_summary_alias(self):
        resume = "Summary\nBrief bio.\n\nExperience\n..."
        result = _inject_metrics_into_summary(resume, ["top-5"])
        assert "(top-5)" in result


# ---------------------------------------------------------------------------
# _inject_skills
# ---------------------------------------------------------------------------

class TestInjectSkills:
    def test_creates_other_line_when_none_exists(self):
        resume = "Technical Skills\nJava, Python"
        result = _inject_skills(resume, ["Kafka"])
        assert "Other: Kafka" in result

    def test_appends_to_existing_other_line(self):
        resume = "Technical Skills\nJava, Python\nOther: Redis"
        result = _inject_skills(resume, ["Kafka"])
        assert "Other: Redis, Kafka" in result

    def test_appends_multiple_skills(self):
        resume = "Technical Skills\nOther: Redis"
        result = _inject_skills(resume, ["Kafka", "Docker"])
        assert "Other: Redis, Kafka, Docker" in result

    def test_other_line_with_trailing_comma(self):
        resume = "Technical Skills\nOther: Redis,"
        result = _inject_skills(resume, ["Kafka"])
        assert "Redis, Kafka" in result
        # No double-comma
        assert "Redis,, Kafka" not in result and "Redis, , Kafka" not in result

    def test_no_skills_section_adds_minimal(self):
        resume = "John Doe\n\nExperience\nEngineer | Acme | 2020\n- Built stuff"
        result = _inject_skills(resume, ["Kafka"])
        assert "Technical Skills" in result
        assert "Other: Kafka" in result

    def test_other_line_stays_inside_skills_block(self):
        resume = (
            "Technical Skills\n"
            "Languages: Java\n\n"
            "Education\nBSc Computer Science"
        )
        result = _inject_skills(resume, ["Kafka"])
        idx_other = result.index("Other: Kafka")
        idx_edu = result.index("Education")
        assert idx_other < idx_edu

    def test_no_op_for_empty_skills(self):
        resume = "Technical Skills\nJava"
        assert _inject_skills(resume, []) == resume


# ---------------------------------------------------------------------------
# _fix_cover_letter_date
# ---------------------------------------------------------------------------

class TestFixCoverLetterDate:
    def test_prepends_date_when_missing(self):
        cl = "Dear Hiring Manager, I am applying."
        result = _fix_cover_letter_date(cl, _DATE)
        assert result.startswith(f"{_DATE}\n\n")
        assert "Dear Hiring Manager" in result

    def test_no_change_when_present(self):
        cl = f"{_DATE}\n\nDear Hiring Manager..."
        assert _fix_cover_letter_date(cl, _DATE) == cl

    def test_no_change_for_empty_date(self):
        cl = "Dear Hiring Manager..."
        assert _fix_cover_letter_date(cl, "") == cl

    def test_no_change_when_date_mid_letter(self):
        cl = f"Dear Manager,\n\nWritten on {_DATE}."
        assert _fix_cover_letter_date(cl, _DATE) == cl


# ---------------------------------------------------------------------------
# postprocess_token_compliance — integration
# ---------------------------------------------------------------------------

class TestPostprocessTokenCompliance:

    def test_output_has_exactly_two_fields(self):
        out = postprocess_token_compliance({"resume": "A", "cover_letter": "B"}, _packet(), _DATE)
        assert set(out.keys()) == {"resume", "cover_letter"}

    def test_no_changes_when_all_tokens_present(self):
        resume = _resume(summary="Senior engineer with 1M+ experience.", skills="Java, Kafka")
        cover = _cover()
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": cover},
            _packet(skills=["Kafka"], metrics=["1M+"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert out["resume"] == resume
        assert out["cover_letter"] == cover

    def test_injects_missing_metric_into_summary(self):
        resume = _resume(summary="Senior engineer.", skills="Java, Kafka")
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(metrics=["25%"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert "25%" in out["resume"]

    def test_injects_missing_skill(self):
        resume = _resume(skills="Java")
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(skills=["Kafka"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert "Kafka" in out["resume"]

    def test_fixes_missing_cover_letter_date(self):
        cl = "Dear Hiring Manager, I am great."
        out = postprocess_token_compliance(
            {"resume": _resume(), "cover_letter": cl},
            _packet(),
            _DATE,
        )
        assert out["cover_letter"].startswith(f"{_DATE}\n\n")

    def test_support_check_blocks_unsupported_token(self):
        resume = _resume(skills="Java")
        # SNMP not in master/profile
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(skills=["SNMP"]),
            _DATE,
            master_resume_text="Java Python Redis",
            candidate_profile_text="",
        )
        assert "SNMP" not in out["resume"]

    def test_no_duplicate_skill_already_present(self):
        resume = _resume(skills="Java, Kafka, Redis")
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(skills=["Kafka"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert out["resume"].lower().count("kafka") == resume.lower().count("kafka")

    def test_no_duplicate_metric_already_present(self):
        resume = _resume(summary="Led team, served 1M+ users.")
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(metrics=["1M+"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert out["resume"].count("1M+") == resume.count("1M+")

    def test_employer_and_dates_unchanged(self):
        resume = _resume()
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": _cover()},
            _packet(metrics=["25%"], skills=["Docker"]),
            _DATE, master_resume_text=_MASTER,
        )
        assert "Engineer | Acme Corp | 2020-2023" in out["resume"]

    def test_integration_validator_passes_after_postprocess(self):
        """Full integration: simulate failing Phase 2 → postprocess → validator passes."""
        from tailor.phase2_validator import validate_phase2_output

        resume = (
            "Professional Summary\nSenior engineer.\n\n"
            "Experience\nEngineer | Acme Corp | 2020-2023\n"
            "- Built distributed systems\n"
            "- Improved performance significantly\n\n"
            "Technical Skills\nJava, Redis"
        )
        cover = "Dear Hiring Manager, I am applying."

        packet = {
            "must_keep_metrics": ["25%"],
            "must_include_skills": ["Kafka"],
            "allowed_skill_pool": ["Java", "Redis", "Kafka"],
            "unsafe_jd_nouns": [],
            "role_priorities": {"Engineer | Acme Corp": "high"},
            "role_source_bullet_counts": {"Engineer | Acme Corp": 5},
            "role_source_char_counts": {},
            "jd_is_delivery_oriented": False,
            "density_targets": {
                "bullet_min_by_priority": {"high": 2, "medium": 2, "low": 1},
                "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
            },
        }

        # Verify failure before postprocessing
        pre = validate_phase2_output(packet, resume, cover, _DATE)
        assert not pre["ok"], f"Expected pre-validation to fail: {pre['errors']}"

        master = "Improved throughput by 25% using horizontal scaling. Java, Kafka, Redis"
        out = postprocess_token_compliance(
            {"resume": resume, "cover_letter": cover},
            packet, _DATE,
            master_resume_text=master,
        )

        post = validate_phase2_output(packet, out["resume"], out["cover_letter"], _DATE)
        assert post["ok"], f"Expected post-validation to pass: {post['errors']}"
