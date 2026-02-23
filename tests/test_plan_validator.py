"""Tests for the v2.1 extended plan validator (plan_validator.py)."""

import copy

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_plan() -> dict:
    """Return a fully valid v2.1 plan for use as a test fixture."""
    return {
        "role_level": "senior",
        "jd_top_themes": [
            {"theme": "Distributed Systems", "why_important": "core", "keywords": ["scale"]},
            {"theme": "Integration Patterns", "why_important": "core", "keywords": ["api"]},
            {"theme": "Reliability Engineering", "why_important": "core", "keywords": ["sla"]},
        ],
        "evidence_map": [
            {
                "theme": "Distributed Systems",
                "evidence": [
                    {"source": "master_resume", "location": "Acme", "quote": "Scaled to 1M+", "allowed_claims": []},
                    {"source": "master_resume", "location": "Acme", "quote": "Horizontal scaling", "allowed_claims": []},
                    {"source": "candidate_profile", "location": "highlights", "quote": "Read replicas", "allowed_claims": []},
                ],
                "gaps": [],
                "safe_translation": [],
            },
            {
                "theme": "Integration Patterns",
                "evidence": [
                    {"source": "master_resume", "location": "Acme", "quote": "Built async messaging pipeline", "allowed_claims": []},
                    {"source": "master_resume", "location": "Acme", "quote": "Designed adapter layer", "allowed_claims": []},
                ],
                "gaps": [],
                "safe_translation": [],
            },
            {
                "theme": "Reliability Engineering",
                "evidence": [
                    {"source": "master_resume", "location": "Acme", "quote": "99.9% uptime SLA", "allowed_claims": []},
                ],
                "gaps": [],
                "safe_translation": [],
            },
        ],
        "resume_strategy": {
            "summary": {"include_points": [], "avoid_points": []},
            "experience": [
                {
                    "role_name": "Senior Engineer | Acme Corp",
                    "priority": "high",
                    "keep_metrics": [],
                    "bullets_to_emphasize": [],
                    "bullets_to_compress": [],
                    "bullets_to_reframe": [],
                },
                {
                    "role_name": "Engineer | Beta Corp",
                    "priority": "medium",
                    "keep_metrics": [],
                    "bullets_to_emphasize": [],
                    "bullets_to_compress": [],
                    "bullets_to_reframe": [],
                },
            ],
            "skills": {
                "reorder_categories": [],
                "promote_skills": [],
                "demote_skills": [],
                "do_not_add_skills": [],
            },
        },
        "cover_letter_strategy": {
            "company_and_role_mentions": [],
            "bullet_overlaps_to_reference": [],
            "structure": [],
        },
        "risk_checks": {
            "do_not_invent": [],
            "likely_hallucination_traps": [],
            "claims_requiring_strict_grounding": [],
        },
        "theme_priority": {
            "primary": ["Distributed Systems"],
            "secondary": ["Integration Patterns"],
            "supporting": ["Reliability Engineering"],
        },
        "role_repositioning_intent": [
            {
                "role_name": "Senior Engineer | Acme Corp",
                "intent": "Reframe as distributed systems architect.",
            },
            {
                "role_name": "Engineer | Beta Corp",
                "intent": "Highlight integration and async messaging experience.",
            },
        ],
        "domain_de_emphasis": {
            "enabled": False,
            "downweight_terms": [],
            "preferred_replacement_frame": "",
        },
        "evidence_saturation_rules": {
            "primary_theme_min_evidence": 3,
            "secondary_theme_min_evidence": 2,
            "supporting_theme_min_evidence": 1,
        },
        "bullet_allocation_plan": [
            {
                "role_name": "Senior Engineer | Acme Corp",
                "theme_to_min_bullets": {"Distributed Systems": 2, "Integration Patterns": 1},
            },
            {
                "role_name": "Engineer | Beta Corp",
                "theme_to_min_bullets": {"Integration Patterns": 2, "Reliability Engineering": 1},
            },
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPlanSchemaHasNewFields:
    """Test 1 — validate_plan_extended passes on a fully populated v2.1 plan."""

    def test_valid_plan_returns_no_errors(self):
        from tailor.plan_validator import validate_plan_extended
        errors = validate_plan_extended(_base_plan())
        assert errors == [], f"Unexpected errors: {errors}"

    def test_missing_single_v21_key_returns_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        del plan["theme_priority"]
        errors = validate_plan_extended(plan)
        assert any("theme_priority" in e for e in errors)

    def test_missing_all_v21_keys_returns_error_and_stops_early(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        for key in ("theme_priority", "role_repositioning_intent",
                    "domain_de_emphasis", "evidence_saturation_rules",
                    "bullet_allocation_plan"):
            del plan[key]
        errors = validate_plan_extended(plan)
        # Should report the missing keys and return early
        assert len(errors) == 1
        assert "v2.1 required keys" in errors[0]


class TestPlanPrimaryEvidenceMinimum:
    """Test 2 — evidence saturation enforcement per tier."""

    def test_primary_theme_too_few_evidence_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        # Primary theme "Distributed Systems" needs 3 evidence items; reduce to 1
        plan["evidence_map"][0]["evidence"] = [
            {"source": "master_resume", "location": "Acme", "quote": "Only one item", "allowed_claims": []}
        ]
        errors = validate_plan_extended(plan)
        assert any("Distributed Systems" in e and "primary" in e for e in errors)

    def test_secondary_theme_too_few_evidence_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        # Secondary theme "Integration Patterns" needs 2 evidence; reduce to 1
        plan["evidence_map"][1]["evidence"] = [
            {"source": "master_resume", "location": "Acme", "quote": "Only one item", "allowed_claims": []}
        ]
        errors = validate_plan_extended(plan)
        assert any("Integration Patterns" in e and "secondary" in e for e in errors)

    def test_supporting_theme_one_evidence_is_sufficient(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        # "Reliability Engineering" is supporting with 1 evidence — should pass
        errors = validate_plan_extended(plan)
        assert not any("Reliability Engineering" in e for e in errors)


class TestThemePriorityReferencesValidThemes:
    """Test 3 — theme_priority tiers must reference known jd_top_themes."""

    def test_unknown_theme_in_primary_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["theme_priority"]["primary"] = ["Nonexistent Theme"]
        errors = validate_plan_extended(plan)
        assert any("Nonexistent Theme" in e for e in errors)

    def test_empty_primary_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["theme_priority"]["primary"] = []
        errors = validate_plan_extended(plan)
        assert any("primary" in e and "at least 1" in e for e in errors)

    def test_empty_secondary_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["theme_priority"]["secondary"] = []
        errors = validate_plan_extended(plan)
        assert any("secondary" in e and "at least 1" in e for e in errors)

    def test_valid_tiers_return_no_errors(self):
        from tailor.plan_validator import validate_plan_extended
        errors = validate_plan_extended(_base_plan())
        assert errors == []


class TestDomainDeEmphasisEnabled:
    """Test 4 — domain_de_emphasis.enabled=true requires populated fields."""

    def test_enabled_with_empty_downweight_terms_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["domain_de_emphasis"] = {
            "enabled": True,
            "downweight_terms": [],
            "preferred_replacement_frame": "distributed systems framing",
        }
        errors = validate_plan_extended(plan)
        assert any("downweight_terms" in e for e in errors)

    def test_enabled_with_empty_replacement_frame_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["domain_de_emphasis"] = {
            "enabled": True,
            "downweight_terms": ["fintech"],
            "preferred_replacement_frame": "",
        }
        errors = validate_plan_extended(plan)
        assert any("preferred_replacement_frame" in e for e in errors)

    def test_enabled_false_skips_field_checks(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["domain_de_emphasis"] = {
            "enabled": False,
            "downweight_terms": [],
            "preferred_replacement_frame": "",
        }
        errors = validate_plan_extended(plan)
        assert not any("downweight_terms" in e for e in errors)
        assert not any("preferred_replacement_frame" in e for e in errors)

    def test_enabled_with_all_fields_populated_passes(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["domain_de_emphasis"] = {
            "enabled": True,
            "downweight_terms": ["trading", "fintech"],
            "preferred_replacement_frame": "high-throughput distributed systems",
        }
        errors = validate_plan_extended(plan)
        assert not any("domain_de_emphasis" in e for e in errors)


class TestBulletAllocationIncludesTopRoles:
    """Test 5 — bullet_allocation_plan must cover top 2 roles with >= 2 themes."""

    def test_missing_top_role_entry_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        # Remove entry for first role
        plan["bullet_allocation_plan"] = [
            {
                "role_name": "Engineer | Beta Corp",
                "theme_to_min_bullets": {"Integration Patterns": 2, "Reliability Engineering": 1},
            }
        ]
        errors = validate_plan_extended(plan)
        assert any("Senior Engineer | Acme Corp" in e for e in errors)

    def test_top_role_with_only_one_theme_raises_error(self):
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        plan["bullet_allocation_plan"][0]["theme_to_min_bullets"] = {"Distributed Systems": 2}
        errors = validate_plan_extended(plan)
        assert any("at least 2" in e for e in errors)

    def test_top_roles_with_two_themes_each_passes(self):
        from tailor.plan_validator import validate_plan_extended
        errors = validate_plan_extended(_base_plan())
        assert errors == []

    def test_role_name_matching_is_case_insensitive_and_punctuation_tolerant(self):
        """Role names with different capitalisation/punctuation should still match."""
        from tailor.plan_validator import validate_plan_extended
        plan = _base_plan()
        # Use slightly different punctuation in bullet_allocation_plan
        plan["bullet_allocation_plan"][0]["role_name"] = "senior engineer  acme corp"
        errors = validate_plan_extended(plan)
        # Fuzzy matching should still find the top role — no missing-entry error
        assert not any("Senior Engineer | Acme Corp" in e and "missing" in e for e in errors)
