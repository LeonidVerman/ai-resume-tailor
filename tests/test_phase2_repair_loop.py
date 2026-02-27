"""Tests for Phase 2 repair loop and RepairBrief generation.

Verifies:
- validate_phase2_output emits a machine-readable repair_brief.
- repair_brief structure is correct (global_issues, roles with action_plan).
- Postprocessor modules have been removed.
"""

import pytest

from tailor.phase2_validator import validate_phase2_output


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_DATE = "February 27, 2026"


def _make_packet(
    *,
    role_name: str = "Senior Engineer | Acme Corp",
    priority: str = "high",
    source_bullets: int = 5,
    must_keep_metrics: list[str] | None = None,
    must_include_skills: list[str] | None = None,
    must_surface_arch_mechanisms: list[str] | None = None,
    arch_mechanisms_primary: list[str] | None = None,
    arch_mechanisms_backstop: list[str] | None = None,
    unsafe_jd_nouns: list[str] | None = None,
) -> dict:
    mechanisms = must_surface_arch_mechanisms or []
    return {
        "must_keep_metrics": must_keep_metrics or [],
        "must_surface_arch_mechanisms": mechanisms,
        "arch_mechanisms_primary": arch_mechanisms_primary if arch_mechanisms_primary is not None else mechanisms,
        "arch_mechanisms_backstop": arch_mechanisms_backstop or [],
        "must_include_skills": must_include_skills or [],
        "allowed_skill_pool": ["Java", "Kafka", "Redis", "Docker"],
        "unsafe_jd_nouns": unsafe_jd_nouns or [],
        "role_priorities": {role_name: priority},
        "role_source_bullet_counts": {role_name: source_bullets},
        "role_source_char_counts": {role_name: 400},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
        },
        "role_density_shortfall_allowance": {},
    }


def _make_resume(bullets: list[str], role_header: str = "Senior Engineer | Acme Corp | 2022 - Present") -> str:
    bullet_lines = "\n".join(f"- {b}" for b in bullets)
    return (
        "Professional Summary\nSenior engineer with broad experience.\n\n"
        f"Experience\n{role_header}\n{bullet_lines}\n\n"
        "Technical Skills\nJava, Redis"
    )


def _make_cover(date_str: str = _DATE) -> str:
    return f"{date_str}\n\nDear Hiring Manager, I am a strong fit for this role."


# ---------------------------------------------------------------------------
# repair_brief structure
# ---------------------------------------------------------------------------

class TestRepairBriefStructure:

    def test_report_has_repair_brief_key(self):
        """validate_phase2_output must always return a repair_brief key."""
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert "repair_brief" in report

    def test_repair_brief_has_global_issues(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        brief = report["repair_brief"]
        assert "global_issues" in brief
        gi = brief["global_issues"]
        assert "missing_metrics" in gi
        assert "missing_skills" in gi
        assert "unsafe_nouns_in_resume" in gi
        assert "cover_letter_date_missing" in gi

    def test_repair_brief_has_roles(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        brief = report["repair_brief"]
        assert "roles" in brief
        assert isinstance(brief["roles"], list)
        assert len(brief["roles"]) >= 1

    def test_role_entry_has_required_keys(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        role = report["repair_brief"]["roles"][0]
        assert "role_header" in role
        assert "effective_priority" in role
        assert "bullets" in role
        assert "mechanisms" in role
        assert "allowed_phrases" in role
        assert "action_plan" in role

    def test_role_bullets_subfields(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        bullets = report["repair_brief"]["roles"][0]["bullets"]
        for key in ("have", "need", "allowance", "effective_min", "deficit"):
            assert key in bullets, f"Missing key: {key}"

    def test_role_mechanisms_subfields(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        mechanisms = report["repair_brief"]["roles"][0]["mechanisms"]
        for key in ("have", "need", "deficit"):
            assert key in mechanisms, f"Missing key: {key}"

    def test_role_action_plan_subfields(self):
        wp = _make_packet()
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        ap = report["repair_brief"]["roles"][0]["action_plan"]
        for key in ("add_bullets", "rewrite_bullets_for_mechanisms", "split_bullets"):
            assert key in ap, f"Missing key: {key}"

    def test_role_allowed_phrases_subfields(self):
        wp = _make_packet(
            arch_mechanisms_primary=["horizontal scaling", "Redis caching"],
            arch_mechanisms_backstop=["message queue"],
        )
        resume = _make_resume(["Built distributed system"] * 4)
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        ap = report["repair_brief"]["roles"][0]["allowed_phrases"]
        assert "arch_mechanisms_primary" in ap
        assert "arch_mechanisms_backstop" in ap
        assert "horizontal scaling" in ap["arch_mechanisms_primary"]
        assert "message queue" in ap["arch_mechanisms_backstop"]


# ---------------------------------------------------------------------------
# Deficit correctness
# ---------------------------------------------------------------------------

class TestRepairBriefDeficits:

    def test_bullet_deficit_when_short(self):
        """Role with 2 bullets (need 4) should report deficit=2."""
        wp = _make_packet()
        resume = _make_resume(["Built system", "Improved latency"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        role = report["repair_brief"]["roles"][0]
        assert role["bullets"]["have"] == 2
        assert role["bullets"]["need"] == 4
        assert role["bullets"]["deficit"] == 2

    def test_no_bullet_deficit_when_satisfied(self):
        """Role with 4 bullets (need 4) should report deficit=0."""
        wp = _make_packet()
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        role = report["repair_brief"]["roles"][0]
        assert role["bullets"]["have"] == 4
        assert role["bullets"]["deficit"] == 0

    def test_mechanism_deficit_when_short(self):
        """High-priority role with 0 mechanisms (need 2) should report deficit=2."""
        wp = _make_packet(
            must_surface_arch_mechanisms=["horizontal scaling", "Redis caching", "message queue"],
        )
        resume = _make_resume(["Built system", "Improved latency", "Led team", "Shipped feature"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        role = report["repair_brief"]["roles"][0]
        assert role["mechanisms"]["need"] == 2
        assert role["mechanisms"]["have"] == 0
        assert role["mechanisms"]["deficit"] == 2

    def test_mechanism_no_deficit_when_present(self):
        """Role with 2 mechanisms in bullets (need 2) should report deficit=0."""
        wp = _make_packet(
            must_surface_arch_mechanisms=["horizontal scaling", "Redis caching"],
        )
        resume = _make_resume([
            "Scaled system via horizontal scaling",
            "Added Redis caching layer",
            "Led team of 5",
            "Shipped major release",
        ])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        role = report["repair_brief"]["roles"][0]
        assert role["mechanisms"]["deficit"] == 0

    def test_action_plan_add_bullets_matches_deficit(self):
        wp = _make_packet()
        resume = _make_resume(["B1", "B2"])  # 2 bullets, need 4
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        ap = report["repair_brief"]["roles"][0]["action_plan"]
        assert ap["add_bullets"] == 2

    def test_action_plan_split_bullets_set_when_deficit(self):
        wp = _make_packet()
        resume = _make_resume(["B1", "B2"])  # has deficit
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        ap = report["repair_brief"]["roles"][0]["action_plan"]
        assert ap["split_bullets"] == 1

    def test_action_plan_zeros_when_no_deficit(self):
        wp = _make_packet(must_surface_arch_mechanisms=[])
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        ap = report["repair_brief"]["roles"][0]["action_plan"]
        assert ap["add_bullets"] == 0
        assert ap["split_bullets"] == 0


# ---------------------------------------------------------------------------
# Global issues
# ---------------------------------------------------------------------------

class TestRepairBriefGlobalIssues:

    def test_missing_metrics_reported(self):
        wp = _make_packet(must_keep_metrics=["25%", "1M+"])
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert "25%" in gi["missing_metrics"]
        assert "1M+" in gi["missing_metrics"]

    def test_no_missing_metrics_when_present(self):
        wp = _make_packet(must_keep_metrics=["25%"])
        resume = _make_resume(["Improved throughput by 25%", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert gi["missing_metrics"] == []

    def test_missing_skills_reported(self):
        wp = _make_packet(must_include_skills=["Kafka"])
        resume = _make_resume(["B1", "B2", "B3", "B4"])  # Technical Skills: Java, Redis
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert "Kafka" in gi["missing_skills"]

    def test_no_missing_skills_when_present(self):
        wp = _make_packet(must_include_skills=["Java"])
        resume = _make_resume(["B1", "B2", "B3", "B4"])  # has Java in Technical Skills
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert gi["missing_skills"] == []

    def test_unsafe_nouns_reported(self):
        wp = _make_packet(unsafe_jd_nouns=["SNMP"])
        resume = _make_resume(["B1", "B2", "Configured SNMP endpoints", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert "SNMP" in gi["unsafe_nouns_in_resume"]

    def test_no_unsafe_nouns_when_absent(self):
        wp = _make_packet(unsafe_jd_nouns=["SNMP"])
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        gi = report["repair_brief"]["global_issues"]
        assert gi["unsafe_nouns_in_resume"] == []

    def test_cover_letter_date_missing_true_when_absent(self):
        wp = _make_packet()
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        cover = "Dear Hiring Manager, I am a strong fit."  # no date
        report = validate_phase2_output(wp, resume, cover, _DATE)
        assert report["repair_brief"]["global_issues"]["cover_letter_date_missing"] is True

    def test_cover_letter_date_missing_false_when_present(self):
        wp = _make_packet()
        resume = _make_resume(["B1", "B2", "B3", "B4"])
        report = validate_phase2_output(wp, resume, _make_cover(), _DATE)
        assert report["repair_brief"]["global_issues"]["cover_letter_date_missing"] is False


# ---------------------------------------------------------------------------
# Postprocessor modules have been removed
# ---------------------------------------------------------------------------

class TestPostprocessorModulesRemoved:

    def test_mechanism_postprocessor_module_deleted(self):
        """mechanism_postprocessor.py must no longer exist in the package."""
        with pytest.raises(ImportError):
            import tailor.mechanism_postprocessor  # noqa: F401

    def test_token_postprocessor_module_deleted(self):
        """token_postprocessor.py must no longer exist in the package."""
        with pytest.raises(ImportError):
            import tailor.token_postprocessor  # noqa: F401
