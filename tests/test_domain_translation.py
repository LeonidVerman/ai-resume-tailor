"""Tests for the domain translation rules feature.

All tests are deterministic — no LLM calls, no file I/O beyond reading the
actual config/domain_translation_rules.json file (which is part of the repo).
"""

import json
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_plan_with_domains(
    jd_domain: str = "b2b_saas_platform",
    candidate_primary_domain: str = "fintech_trading",
    domain_mismatch: bool = False,
    domain_translation_rule_ids: list | None = None,
) -> dict:
    return {
        "role_level": "senior",
        "resume_mode": "technical_depth",
        "jd_domain": jd_domain,
        "candidate_primary_domain": candidate_primary_domain,
        "domain_mismatch": domain_mismatch,
        "domain_translation_rule_ids": domain_translation_rule_ids or [],
        "jd_top_themes": [
            {
                "theme": f"Theme {i}",
                "priority": "primary",
                "why_important": "important",
                "keywords": ["scalability"],
            }
            for i in range(5)
        ],
        "vocabulary_anchoring": {
            "must_embed": ["scalability"],
            "optional_embed": [],
        },
        "evidence_map": [
            {
                "theme": "Theme 0",
                "evidence": [
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Scaled platform to 1M+ users",
                        "allowed_claims": ["scaled to 1M+ users"],
                    }
                ],
                "gaps": [],
                "safe_translation": [],
            }
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
                }
            ],
            "skills": {
                "promote_skills": [],
                "demote_skills": [],
                "do_not_add_skills": [],
            },
        },
        "cover_letter_strategy": {
            "company_and_role_mentions": [],
            "structure": [],
        },
        "risk_checks": {
            "do_not_invent": [],
            "likely_hallucination_traps": [],
            "claims_requiring_strict_grounding": [],
        },
    }


def _minimal_writer_packet(
    domain_mismatch: bool = False,
    domain_translation_rules_applied: list | None = None,
) -> dict:
    """Minimal writer packet for Phase 2 validator tests."""
    return {
        "role_level": "senior",
        "jd_domain": "b2b_saas_platform",
        "candidate_primary_domain": "fintech_trading",
        "domain_mismatch": domain_mismatch,
        "domain_translation_rules_applied": domain_translation_rules_applied or [],
        "jd_vocab_must_embed": [],
        "jd_vocab_optional_embed": [],
        "master_resume_role_names": [],
        "must_keep_metrics": [],
        "must_surface_arch_mechanisms": [],
        "arch_mechanisms_primary": [],
        "arch_mechanisms_backstop": [],
        "must_surface_strategic_signals": [],
        "must_surface_operational_signals": [],
        "mechanism_dedup_map": {},
        "role_weight_profile": {"arch_weight": 0.7, "strategic_weight": 0.2, "operational_weight": 0.1},
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "do_not_add_terms": [],
        "unsafe_jd_nouns": [],
        "integration_extensibility_reframes": [],
        "role_priorities": {},
        "role_source_bullet_counts": {},
        "role_source_char_counts": {},
        "jd_is_delivery_oriented": False,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
        },
        "role_density_shortfall_allowance": {},
    }


# ---------------------------------------------------------------------------
# 3.1 — Config loader
# ---------------------------------------------------------------------------

class TestConfigLoader:
    def test_loads_and_returns_dict(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        assert isinstance(data, dict)

    def test_required_top_level_keys(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        assert "version" in data
        assert "domains" in data
        assert "rules" in data

    def test_domains_is_list_of_strings(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        domains = data["domains"]
        assert isinstance(domains, list)
        assert len(domains) > 0
        assert all(isinstance(d, str) for d in domains)

    def test_known_domains_present(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        domains = set(data["domains"])
        for expected in ("fintech_trading", "b2b_saas_platform", "healthcare_enterprise"):
            assert expected in domains, f"Expected domain {expected!r} missing"

    def test_rules_is_list_of_dicts(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        rules = data["rules"]
        assert isinstance(rules, list)
        assert len(rules) > 0
        assert all(isinstance(r, dict) for r in rules)

    def test_index_built(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        assert "_index" in data
        index = data["_index"]
        assert isinstance(index, dict)

    def test_index_keys_match_rule_ids(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        index = data["_index"]
        for rule in data["rules"]:
            rid = rule["rule_id"]
            assert rid in index
            assert index[rid] is rule

    def test_known_rule_id_in_index(self):
        from tailor.config import load_domain_translation_rules
        data = load_domain_translation_rules()
        assert "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE" in data["_index"]

    def test_caching_returns_same_object(self):
        from tailor.config import load_domain_translation_rules
        first = load_domain_translation_rules()
        second = load_domain_translation_rules()
        assert first is second

    def test_missing_key_raises(self, tmp_path, monkeypatch):
        """A JSON file missing 'domains' should raise ValueError."""
        import tailor.config as cfg_mod
        bad_json = tmp_path / "domain_translation_rules.json"
        bad_json.write_text(json.dumps({"version": "1.0", "rules": []}))
        monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(cfg_mod, "_DOMAIN_RULES_CACHE", None)
        with pytest.raises(ValueError, match="missing required key"):
            cfg_mod.load_domain_translation_rules()


# ---------------------------------------------------------------------------
# 3.2 — Plan schema accepts domain fields
# ---------------------------------------------------------------------------

class TestPlanSchemaDomainFields:
    def test_valid_plan_with_domain_fields_passes(self):
        from tailor.llm import validate_plan
        plan = _minimal_plan_with_domains(
            jd_domain="b2b_saas_platform",
            candidate_primary_domain="fintech_trading",
            domain_mismatch=False,
            domain_translation_rule_ids=[],
        )
        result = validate_plan(plan)
        assert result["jd_domain"] == "b2b_saas_platform"
        assert result["candidate_primary_domain"] == "fintech_trading"
        assert result["domain_mismatch"] is False
        assert result["domain_translation_rule_ids"] == []

    def test_domain_mismatch_true_valid(self):
        from tailor.llm import validate_plan
        plan = _minimal_plan_with_domains(
            jd_domain="healthcare_enterprise",
            candidate_primary_domain="fintech_trading",
            domain_mismatch=True,
            domain_translation_rule_ids=["R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE"],
        )
        result = validate_plan(plan)
        assert result["domain_mismatch"] is True
        assert len(result["domain_translation_rule_ids"]) == 1

    def test_invalid_domain_value_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan_with_domains(jd_domain="not_a_real_domain")
        with pytest.raises(PlanValidationError, match="not a valid domain"):
            validate_plan(plan)

    def test_domain_mismatch_non_bool_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan_with_domains()
        plan["domain_mismatch"] = "yes"
        with pytest.raises(PlanValidationError, match="domain_mismatch must be a boolean"):
            validate_plan(plan)

    def test_too_many_rule_ids_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan_with_domains(
            domain_translation_rule_ids=["R01", "R02", "R03", "R04"]
        )
        with pytest.raises(PlanValidationError, match="at most 3"):
            validate_plan(plan)

    def test_missing_jd_domain_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan_with_domains()
        del plan["jd_domain"]
        with pytest.raises(PlanValidationError, match="missing required keys"):
            validate_plan(plan)


# ---------------------------------------------------------------------------
# 3.4 — Writer packet attaches rules correctly
# ---------------------------------------------------------------------------

class TestWriterPacketDomainFields:
    def _make_resume(self) -> str:
        return (
            "Experience\n"
            "Senior Engineer | Acme Corp | Nov 2022 – Present\n"
            "- Built high-availability distributed services under strict reliability targets\n"
            "- Improved platform reliability and operability at scale\n"
        )

    def test_domain_fields_present_no_mismatch(self):
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains(
            jd_domain="b2b_saas_platform",
            candidate_primary_domain="fintech_trading",
            domain_mismatch=False,
            domain_translation_rule_ids=[],
        )
        wp = build_writer_packet(plan, "{}", self._make_resume(), "some job description")
        assert wp["jd_domain"] == "b2b_saas_platform"
        assert wp["candidate_primary_domain"] == "fintech_trading"
        assert wp["domain_mismatch"] is False
        assert wp["domain_translation_rules_applied"] == []

    def test_domain_rules_resolved_for_known_id(self):
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains(
            jd_domain="healthcare_enterprise",
            candidate_primary_domain="fintech_trading",
            domain_mismatch=True,
            domain_translation_rule_ids=["R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE"],
        )
        wp = build_writer_packet(plan, "{}", self._make_resume(), "job description")
        rules = wp["domain_translation_rules_applied"]
        assert len(rules) == 1
        assert rules[0]["rule_id"] == "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE"
        assert "forbidden_phrases" in rules[0]
        assert "allowed_phrases" in rules[0]

    def test_multiple_rule_ids_resolved(self):
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains(
            jd_domain="gov_public_sector",
            candidate_primary_domain="fintech_trading",
            domain_mismatch=True,
            domain_translation_rule_ids=[
                "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE",
                "R02_TRADING_PLATFORM_TO_ENTERPRISE_PLATFORM",
            ],
        )
        wp = build_writer_packet(plan, "{}", self._make_resume(), "job description")
        rules = wp["domain_translation_rules_applied"]
        assert len(rules) == 2

    def test_unknown_rule_id_skipped(self):
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains(
            domain_mismatch=True,
            domain_translation_rule_ids=["NONEXISTENT_RULE"],
        )
        wp = build_writer_packet(plan, "{}", self._make_resume(), "job description")
        assert wp["domain_translation_rules_applied"] == []

    def test_no_rule_ids_returns_empty_list(self):
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains(domain_translation_rule_ids=[])
        wp = build_writer_packet(plan, "{}", self._make_resume(), "job description")
        assert wp["domain_translation_rules_applied"] == []

    def test_plan_without_domain_fields_defaults_gracefully(self):
        """Writer packet handles plans that lack domain fields (backward compat)."""
        from tailor.writer_packet import build_writer_packet
        plan = _minimal_plan_with_domains()
        del plan["jd_domain"]
        del plan["candidate_primary_domain"]
        del plan["domain_mismatch"]
        del plan["domain_translation_rule_ids"]
        wp = build_writer_packet(plan, "{}", self._make_resume(), "job description")
        assert wp["jd_domain"] == ""
        assert wp["candidate_primary_domain"] == ""
        assert wp["domain_mismatch"] is False
        assert wp["domain_translation_rules_applied"] == []


# ---------------------------------------------------------------------------
# 3.6 — Phase 2 validator: forbidden phrases guardrail
# ---------------------------------------------------------------------------

class TestDomainForbiddenPhrasesValidator:
    _CURRENT_DATE = "March 2, 2026"

    def _resume_with_phrase(self, phrase: str) -> str:
        return (
            "Experience\n"
            f"Senior Engineer | Acme Corp | Nov 2022 – Present\n"
            f"- Operated under regulatory and security constraints. {phrase}\n"
            "- Built high-availability services at scale.\n"
            "- Improved platform reliability through monitoring.\n"
            "- Reduced latency via targeted optimization.\n"
        )

    def _clean_resume(self) -> str:
        return (
            "Experience\n"
            "Senior Engineer | Acme Corp | Nov 2022 – Present\n"
            "- Operated under regulatory and security constraints.\n"
            "- Built high-availability services at scale.\n"
            "- Improved platform reliability through monitoring.\n"
            "- Reduced latency via targeted optimization.\n"
        )

    def test_forbidden_phrase_in_resume_raises_error(self):
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE",
            "forbidden_phrases": ["healthcare experience", "HIPAA"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[rule],
        )
        resume = self._resume_with_phrase("healthcare experience")
        report = validate_phase2_output(wp, resume, "Cover letter text.", self._CURRENT_DATE)
        assert any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])
        assert "healthcare experience" in report["stats"]["domain_forbidden_phrases_found"]

    def test_forbidden_phrase_in_cover_letter_raises_error(self):
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R07_SECURITY_ENGINEERING_TO_ENTERPRISE_SECURITY_POSTURE",
            "forbidden_phrases": ["HIPAA compliance"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[rule],
        )
        cover = "I bring HIPAA compliance expertise to your team."
        report = validate_phase2_output(wp, self._clean_resume(), cover, self._CURRENT_DATE)
        assert any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])

    def test_no_forbidden_phrases_no_error(self):
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE",
            "forbidden_phrases": ["healthcare experience", "HIPAA"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[rule],
        )
        report = validate_phase2_output(
            wp, self._clean_resume(), "Cover letter text.", self._CURRENT_DATE
        )
        assert not any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])
        assert report["stats"]["domain_forbidden_phrases_found"] == []

    def test_no_mismatch_skips_check_even_if_phrase_present(self):
        """When domain_mismatch is False, forbidden phrase check is skipped entirely."""
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE",
            "forbidden_phrases": ["healthcare experience"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=False,  # no mismatch
            domain_translation_rules_applied=[rule],
        )
        resume = self._resume_with_phrase("healthcare experience")
        report = validate_phase2_output(wp, resume, "Cover letter.", self._CURRENT_DATE)
        assert not any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])

    def test_empty_applied_rules_no_error(self):
        from tailor.phase2_validator import validate_phase2_output
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[],
        )
        resume = self._resume_with_phrase("healthcare experience")
        report = validate_phase2_output(wp, resume, "Cover letter.", self._CURRENT_DATE)
        assert not any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])

    def test_multiple_rules_multiple_forbidden_phrases(self):
        from tailor.phase2_validator import validate_phase2_output
        rules = [
            {
                "rule_id": "R01_REGULATED_CONSTRAINTS_TO_REGULATED_ENTERPRISE",
                "forbidden_phrases": ["healthcare experience", "HIPAA"],
            },
            {
                "rule_id": "R07_SECURITY_ENGINEERING_TO_ENTERPRISE_SECURITY_POSTURE",
                "forbidden_phrases": ["FedRAMP"],
            },
        ]
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=rules,
        )
        resume = self._resume_with_phrase("HIPAA FedRAMP healthcare experience")
        report = validate_phase2_output(wp, resume, "Cover letter.", self._CURRENT_DATE)
        found = report["stats"]["domain_forbidden_phrases_found"]
        assert "healthcare experience" in found
        assert "HIPAA" in found
        assert "FedRAMP" in found

    def test_forbidden_phrase_check_is_case_insensitive(self):
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R01",
            "forbidden_phrases": ["HIPAA"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[rule],
        )
        resume = self._resume_with_phrase("hipaa")  # lowercase in document
        report = validate_phase2_output(wp, resume, "Cover letter.", self._CURRENT_DATE)
        assert any("DOMAIN_FORBIDDEN_PHRASES" in e for e in report["errors"])

    def test_repair_brief_includes_domain_forbidden_phrases(self):
        from tailor.phase2_validator import validate_phase2_output
        rule = {
            "rule_id": "R01",
            "forbidden_phrases": ["government clearance"],
        }
        wp = _minimal_writer_packet(
            domain_mismatch=True,
            domain_translation_rules_applied=[rule],
        )
        resume = self._resume_with_phrase("government clearance required")
        report = validate_phase2_output(wp, resume, "Cover letter.", self._CURRENT_DATE)
        assert "government clearance" in report["repair_brief"]["global_issues"]["domain_forbidden_phrases"]
