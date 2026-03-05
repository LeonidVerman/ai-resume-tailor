"""Tests for structured VALIDATION_ERRORS produced by validate_phase2_output
and the helper functions used by the repair loop (_build_validation_context,
_derive_repair_targets).
"""

from __future__ import annotations

import pytest

from tailor.phase2_validator import (
    CL_BRIDGE_MISSING,
    CL_P1_SPAN_MISSING,
    CL_P2_SPAN_MISSING,
    CL_PARAGRAPH_COUNT_INVALID,
    DOMAIN_TRANSLATION_ANCHOR_NOT_MET,
    DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET,
    NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED,
    NARRATIVE_SUMMARY_THEME_MISSING,
    SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION,
    validate_phase2_output,
)
from tailor.llm import _build_validation_context, _derive_repair_targets


# ---------------------------------------------------------------------------
# Minimal builder helpers
# ---------------------------------------------------------------------------

def _minimal_wp(**overrides) -> dict:
    """Return a minimal WriterPacket that passes all checks by default."""
    base = {
        "role_level": "senior",
        "role_priorities": {},
        "role_source_bullet_counts": {},
        "role_source_char_counts": {},
        "jd_is_delivery_oriented": False,
        "must_keep_metrics": [],
        "must_include_skills": [],
        "allowed_skill_pool": [],
        "unsafe_jd_nouns": [],
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
        },
        "role_density_shortfall_allowance": {},
        "must_surface_arch_mechanisms": [],
        "arch_mechanisms_primary": [],
        "arch_mechanisms_backstop": [],
        "master_resume_role_names": [],
        "master_role_dates": {},
        "jd_vocab_must_embed": [],
        "domain_mismatch": False,
        "domain_translation_rules_applied": [],
        "skill_allowlist_skills_section": [],
        "skill_allowlist_experience_claims": [],
        "direct_skills_set": [],
        "narrative_plan": None,
        "cover_letter_plan": None,
        "jd_domain": "software_engineering",
        "candidate_primary_domain": "software_engineering",
    }
    base.update(overrides)
    return base


def _minimal_resume_with_role(role_header: str, bullets: list[str]) -> str:
    lines = ["Professional Summary", "Experienced engineer.", "", "Experience", role_header]
    lines += [f"- {b}" for b in bullets]
    lines += ["", "Technical Skills", "Python, Go"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Section 1: structured_errors key is always present in the report
# ---------------------------------------------------------------------------


class TestStructuredErrorsPresence:
    def test_structured_errors_key_always_returned(self):
        wp = _minimal_wp()
        resume = _minimal_resume_with_role(
            "Software Engineer | Acme | 2020 - Present", ["Built services."] * 4
        )
        report = validate_phase2_output(wp, resume, "cover letter text", "January 1, 2025")
        assert "structured_errors" in report
        assert isinstance(report["structured_errors"], list)

    def test_no_errors_means_empty_structured_errors(self):
        wp = _minimal_wp(
            # Disable mechanism requirement so the minimal bullets don't trigger density errors.
            density_targets={
                "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
                "mechanism_min_by_priority": {"high": 0, "medium": 0, "low": 0},
            },
        )
        resume = _minimal_resume_with_role(
            "Software Engineer | Acme | 2020 - Present", ["Built services."] * 4
        )
        report = validate_phase2_output(wp, resume, "cover letter text", "")
        assert report["ok"] is True
        assert report["structured_errors"] == []


# ---------------------------------------------------------------------------
# Section 2: CL_PARAGRAPH_COUNT_INVALID structured payload
# ---------------------------------------------------------------------------


class TestCLParagraphCountStructuredError:
    def _wp_with_cl_plan(self, bridge_required=True) -> dict:
        return _minimal_wp(
            cover_letter_plan={
                "structure_version": "CL_V1_4PARA_2PROOF",
                "bridge_sentence_required": bridge_required,
                "proof_points": [
                    {"proof_id": "P1", "required_exact_span": "span1", "allowed_tool_mentions": []},
                    {"proof_id": "P2", "required_exact_span": "span2", "allowed_tool_mentions": []},
                ],
                "hook_theme_id": "T1",
                "closing_guidance": "closing",
            },
            direct_skills_set=[],
        )

    def test_cl_count_error_has_structured_entry(self):
        wp = self._wp_with_cl_plan(bridge_required=True)
        # Cover letter with only 2 body paragraphs (should be 4 when bridge required)
        cl = "Dear Hiring Manager,\n\nHook.\n\nP1 text."
        report = validate_phase2_output(wp, "resume", cl, "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert CL_PARAGRAPH_COUNT_INVALID in codes

    def test_cl_count_payload_shape(self):
        wp = self._wp_with_cl_plan(bridge_required=True)
        cl = "Dear Hiring Manager,\n\nHook.\n\nP1 text."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_PARAGRAPH_COUNT_INVALID]
        assert errs, "No CL_PARAGRAPH_COUNT_INVALID structured error"
        payload = errs[0]["payload"]
        assert payload["expected_body_paragraphs"] == 4
        assert payload["actual_body_paragraphs"] == 2
        assert isinstance(payload["has_salutation"], bool)
        assert payload["has_salutation"] is True
        assert isinstance(payload["body_paragraphs_preview"], list)

    def test_cl_count_payload_preview_truncated_at_160(self):
        wp = self._wp_with_cl_plan(bridge_required=False)
        long_para = "X" * 200
        cl = f"Dear Hiring Manager,\n\n{long_para}\n\nShort."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_PARAGRAPH_COUNT_INVALID]
        if errs:
            for preview in errs[0]["payload"]["body_paragraphs_preview"]:
                assert len(preview) <= 160

    def test_cl_count_no_salutation_detected(self):
        wp = self._wp_with_cl_plan(bridge_required=True)
        cl = "Hook.\n\nP1 text."  # no "Dear" at all
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_PARAGRAPH_COUNT_INVALID]
        if errs:
            assert errs[0]["payload"]["has_salutation"] is False


# ---------------------------------------------------------------------------
# Section 3: CL_P1/P2_SPAN_MISSING structured payload
# ---------------------------------------------------------------------------


class TestCLSpanStructuredError:
    def _wp_spans(self, p1_span="unique span one", p2_span="unique span two") -> dict:
        return _minimal_wp(
            cover_letter_plan={
                "structure_version": "CL_V1_4PARA_2PROOF",
                "bridge_sentence_required": False,
                "proof_points": [
                    {"proof_id": "P1", "required_exact_span": p1_span, "allowed_tool_mentions": []},
                    {"proof_id": "P2", "required_exact_span": p2_span, "allowed_tool_mentions": []},
                ],
                "hook_theme_id": "T1",
                "closing_guidance": "closing",
            },
            direct_skills_set=[],
        )

    def test_p1_span_missing_structured_error(self):
        wp = self._wp_spans()
        # P1 span absent from body paragraph 2; P2 span absent too
        cl = "Dear Hiring Manager,\n\nHook.\n\nNo span here.\n\nNo span here.\n\nClosing."
        report = validate_phase2_output(wp, "resume", cl, "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert CL_P1_SPAN_MISSING in codes

    def test_p1_span_payload_shape(self):
        wp = self._wp_spans(p1_span="unique span one", p2_span="unique span two")
        cl = "Dear Hiring Manager,\n\nHook.\n\nNo span here.\n\nNo span here.\n\nClosing."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_P1_SPAN_MISSING]
        assert errs
        payload = errs[0]["payload"]
        assert payload["proof_id"] == "P1"
        assert payload["required_exact_span"] == "unique span one"
        assert payload["expected_body_paragraph_index"] == 2
        assert payload["found_in_body_paragraph_index"] is None

    def test_p1_span_found_elsewhere_reports_location(self):
        wp = self._wp_spans(p1_span="unique span one", p2_span="unique span two")
        # Span is in paragraph 1 (hook) instead of paragraph 2
        cl = (
            "Dear Hiring Manager,\n\n"
            "Hook unique span one.\n\n"  # para 1 — wrong
            "P1 paragraph text.\n\n"
            "P2 paragraph text.\n\n"
            "Closing."
        )
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_P1_SPAN_MISSING]
        assert errs
        assert errs[0]["payload"]["found_in_body_paragraph_index"] == 1

    def test_p2_span_missing_structured_error(self):
        wp = self._wp_spans()
        cl = "Dear Hiring Manager,\n\nHook.\n\nNo span here.\n\nNo span here.\n\nClosing."
        report = validate_phase2_output(wp, "resume", cl, "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert CL_P2_SPAN_MISSING in codes

    def test_p2_span_payload_shape(self):
        wp = self._wp_spans(p1_span="unique span one", p2_span="unique span two")
        cl = "Dear Hiring Manager,\n\nHook.\n\nNo span here.\n\nNo span here.\n\nClosing."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_P2_SPAN_MISSING]
        assert errs
        payload = errs[0]["payload"]
        assert payload["proof_id"] == "P2"
        assert payload["required_exact_span"] == "unique span two"
        assert payload["expected_body_paragraph_index"] == 3
        assert payload["found_in_body_paragraph_index"] is None


# ---------------------------------------------------------------------------
# Section 4: CL_BRIDGE_MISSING structured payload
# ---------------------------------------------------------------------------


class TestCLBridgeStructuredError:
    def _wp_bridge(self) -> dict:
        return _minimal_wp(
            cover_letter_plan={
                "structure_version": "CL_V1_4PARA_2PROOF",
                "bridge_sentence_required": True,
                "proof_points": [
                    {"proof_id": "P1", "required_exact_span": "spanA", "allowed_tool_mentions": []},
                    {"proof_id": "P2", "required_exact_span": "spanB", "allowed_tool_mentions": []},
                ],
                "hook_theme_id": "T1",
                "closing_guidance": "closing",
            },
            direct_skills_set=[],
            candidate_primary_domain="data_engineering",
            jd_domain="software_engineering",
        )

    def test_bridge_missing_structured_error_present(self):
        wp = self._wp_bridge()
        # Only 2 body paragraphs — bridge para (4) is missing
        cl = "Dear Hiring Manager,\n\nHook.\n\nP1 text spanA here.\n\nP2 text spanB here."
        report = validate_phase2_output(wp, "resume", cl, "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert CL_BRIDGE_MISSING in codes

    def test_bridge_missing_payload_shape(self):
        wp = self._wp_bridge()
        cl = "Dear Hiring Manager,\n\nHook.\n\nP1 text spanA here.\n\nP2 text spanB here."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_BRIDGE_MISSING]
        assert errs
        payload = errs[0]["payload"]
        assert "bridge_sentence" in payload
        assert isinstance(payload["bridge_sentence"], str)
        assert len(payload["bridge_sentence"]) > 10
        assert payload["expected_body_paragraph_index"] == 4

    def test_bridge_sentence_uses_domain_values(self):
        wp = self._wp_bridge()
        cl = "Dear Hiring Manager,\n\nHook.\n\nP1 text spanA here.\n\nP2 text spanB here."
        report = validate_phase2_output(wp, "resume", cl, "")
        errs = [e for e in report["structured_errors"] if e["code"] == CL_BRIDGE_MISSING]
        if errs:
            bridge = errs[0]["payload"]["bridge_sentence"]
            assert "data_engineering" in bridge
            assert "software_engineering" in bridge


# ---------------------------------------------------------------------------
# Section 5: SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION structured payload
# ---------------------------------------------------------------------------


class TestSkillAllowlistStructuredError:
    def _wp_skills(self, allowlist: list[str]) -> dict:
        return _minimal_wp(
            skill_allowlist_skills_section=allowlist,
        )

    def _resume_with_skills(self, skills_line: str) -> str:
        return f"Professional Summary\nExperienced.\n\nExperience\nRole | Co | 2020 - Present\n- Did work.\n\nTechnical Skills\n{skills_line}"

    def test_violation_produces_structured_error(self):
        wp = self._wp_skills(["python", "java"])
        resume = self._resume_with_skills("Python, Java, GraphQL")
        report = validate_phase2_output(wp, resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION in codes

    def test_violation_payload_shape(self):
        wp = self._wp_skills(["python", "java"])
        resume = self._resume_with_skills("Python, Java, GraphQL")
        report = validate_phase2_output(wp, resume, "", "")
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION
        ]
        assert errs
        payload = errs[0]["payload"]
        assert payload["section"] == "resume.skills"
        assert isinstance(payload["offending_tokens"], list)
        tokens = payload["offending_tokens"]
        assert all("raw" in t and "normalized" in t for t in tokens)
        norm_vals = [t["normalized"] for t in tokens]
        assert "graphql" in norm_vals
        assert isinstance(payload["skills_truth_allowlist_sample"], list)
        assert payload["output_format_hint"] == "flat_list_preferred"

    def test_allowlist_sample_max_20(self):
        big_allowlist = [f"skill{i}" for i in range(30)]
        wp = self._wp_skills(big_allowlist)
        resume = self._resume_with_skills("Python, unknown_tool")
        report = validate_phase2_output(wp, resume, "", "")
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION
        ]
        if errs:
            assert len(errs[0]["payload"]["skills_truth_allowlist_sample"]) <= 20

    def test_violation_payload_has_focus_fields(self):
        """SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION payload includes focus coverage fields."""
        wp = self._wp_skills(["python", "java"])
        resume = self._resume_with_skills("Python, Java, GraphQL")
        report = validate_phase2_output(wp, resume, "", "")
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION
        ]
        assert errs
        payload = errs[0]["payload"]
        assert "focus_skills_min_count" in payload
        assert "focus_skills_found_count" in payload
        assert "missing_focus_skills_sample" in payload
        assert isinstance(payload["missing_focus_skills_sample"], list)
        assert isinstance(payload["skills_focus_allowlist_sample"], list)

    def test_no_violation_no_structured_error(self):
        wp = self._wp_skills(["python", "java"])
        resume = self._resume_with_skills("Python, Java")
        report = validate_phase2_output(wp, resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION not in codes


# ---------------------------------------------------------------------------
# Section 5b: SKILL_FOCUS_MIN_NOT_MET structured error (soft)
# ---------------------------------------------------------------------------


class TestFocusCoverageStructuredError:
    """SKILL_FOCUS_MIN_NOT_MET appears in structured_errors but NOT in errors (soft)."""

    from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET as _FMN

    def _wp_focus(
        self,
        truth_list: list[str],
        focus_list: list[str],
        focus_min: int,
    ) -> dict:
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET  # noqa: F401
        return _minimal_wp(
            skill_allowlist_skills_section=truth_list,
            skill_policy={
                "skills_truth_allowlist": truth_list,
                "skills_truth_allowlist_norm": [s.lower() for s in truth_list],
                "skills_focus_allowlist": focus_list,
                "skills_focus_allowlist_norm": [s.lower() for s in focus_list],
                "focus_skills_min_count": focus_min,
                "claim_experience_allowlist": truth_list,
                "claim_experience_allowlist_norm": [s.lower() for s in truth_list],
                "cover_letter_tool_allowlist_global": [],
                "cover_letter_tool_allowlist_global_norm": [],
            },
        )

    def _resume(self, skills: str) -> str:
        return (
            "Professional Summary\nExperienced.\n\n"
            "Experience\nRole | Co | 2020 - Present\n- Did work.\n\n"
            f"Technical Skills\n{skills}"
        )

    def test_focus_shortfall_emits_structured_error(self):
        """When focus count < min, SKILL_FOCUS_MIN_NOT_MET is in structured_errors."""
        wp = self._wp_focus(["python", "java", "kafka"], ["java", "kafka"], 2)
        resume = self._resume("Python")  # only 1 focus skill; min is 2
        report = validate_phase2_output(wp, resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET
        assert SKILL_FOCUS_MIN_NOT_MET in codes

    def test_focus_shortfall_not_in_hard_errors(self):
        """SKILL_FOCUS_MIN_NOT_MET must not appear in errors (soft constraint)."""
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET
        wp = self._wp_focus(["python", "java", "kafka"], ["java", "kafka"], 2)
        resume = self._resume("Python")
        report = validate_phase2_output(wp, resume, "", "")
        assert not any(SKILL_FOCUS_MIN_NOT_MET in e for e in report["errors"])

    def test_focus_met_no_structured_error(self):
        """When focus count ≥ min, no SKILL_FOCUS_MIN_NOT_MET structured error."""
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET
        wp = self._wp_focus(["python", "java", "kafka"], ["java", "kafka"], 2)
        resume = self._resume("Java, Kafka")  # both focus skills present
        report = validate_phase2_output(wp, resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert SKILL_FOCUS_MIN_NOT_MET not in codes

    def test_focus_structured_error_payload_shape(self):
        """SKILL_FOCUS_MIN_NOT_MET payload has all required fields."""
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET
        wp = self._wp_focus(["python", "java", "kafka"], ["java", "kafka"], 2)
        resume = self._resume("Python")  # only python, no focus skills
        report = validate_phase2_output(wp, resume, "", "")
        focus_errs = [
            e for e in report["structured_errors"]
            if e["code"] == SKILL_FOCUS_MIN_NOT_MET
        ]
        assert focus_errs
        payload = focus_errs[0]["payload"]
        assert payload["focus_skills_min_count"] == 2
        assert payload["focus_skills_found_count"] == 0
        assert isinstance(payload["missing_focus_skills_sample"], list)
        assert isinstance(payload["skills_truth_allowlist_sample"], list)
        assert isinstance(payload["skills_focus_allowlist_sample"], list)

    def test_focus_missing_sample_up_to_8(self):
        """missing_focus_skills_sample is capped at 8 items."""
        from tailor.phase2_validator import SKILL_FOCUS_MIN_NOT_MET
        many_focus = [f"skill{i}" for i in range(15)]
        truth = many_focus + ["python"]
        wp = self._wp_focus(truth, many_focus, 10)
        resume = self._resume("Python")  # none of the 15 focus skills
        report = validate_phase2_output(wp, resume, "", "")
        focus_errs = [
            e for e in report["structured_errors"]
            if e["code"] == SKILL_FOCUS_MIN_NOT_MET
        ]
        assert focus_errs
        assert len(focus_errs[0]["payload"]["missing_focus_skills_sample"]) <= 8


# ---------------------------------------------------------------------------
# Section 6: DOMAIN_TRANSLATION structured payload
# ---------------------------------------------------------------------------


class TestDomainTranslationStructuredError:
    def _wp_dt(self) -> dict:
        narrative_plan = {
            "anchor_role_id": "Senior Engineer | DataCo",
            "theme_ranked": [],
            "summary_coverage": {"must_cover_theme_ids": []},
            "anchor_role_coverage": {
                "first_k_bullets": 3,
                "top_k_themes_to_cover": 2,
                "min_theme_occurrences": [],
            },
            "domain_translation_binding": {
                "min_total_rule_instantiations": 2,
                "min_instantiations_in_anchor_role": 1,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        }
        return _minimal_wp(
            domain_mismatch=True,
            narrative_plan=narrative_plan,
            domain_translation_rules_applied=[
                {
                    "rule_id": "dt_rule_1",
                    "allowed_phrases": ["scalable systems", "high-throughput pipelines"],
                    "target_frames": ["data pipeline reliability"],
                    "forbidden_phrases": [],
                }
            ],
        )

    def test_min_total_not_met_structured_error(self):
        wp = self._wp_dt()
        resume = _minimal_resume_with_role(
            "Senior Engineer | DataCo | 2020 - Present", ["Built things."] * 4
        )
        # Empty DT ledger → 0 instantiations found (need 2)
        dt_ledger: list = []
        report = validate_phase2_output(
            wp, resume, "", "", domain_translation_ledger=dt_ledger
        )
        codes = [e["code"] for e in report["structured_errors"]]
        assert DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET in codes

    def test_min_total_payload_shape(self):
        wp = self._wp_dt()
        resume = _minimal_resume_with_role(
            "Senior Engineer | DataCo | 2020 - Present", ["Built things."] * 4
        )
        report = validate_phase2_output(
            wp, resume, "", "", domain_translation_ledger=[]
        )
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET
        ]
        assert errs
        payload = errs[0]["payload"]
        assert payload["required_total"] == 2
        assert payload["found_total"] == 0
        assert "required_in_anchor_role" in payload
        assert "found_in_anchor_role" in payload
        assert "anchor_role_id" in payload
        assert isinstance(payload["applied_rules"], list)
        assert "first_k_bullets" in payload

    def test_anchor_not_met_structured_error(self):
        wp = self._wp_dt()
        resume = _minimal_resume_with_role(
            "Senior Engineer | DataCo | 2020 - Present", ["Built things."] * 4
        )
        # 2 entries total but none in the anchor role
        dt_ledger = [
            {"exact_span": "scalable systems", "location": "resume.other_role"},
            {"exact_span": "high-throughput pipelines", "location": "resume.other_role"},
        ]
        report = validate_phase2_output(
            wp, resume, "", "", domain_translation_ledger=dt_ledger
        )
        codes = [e["code"] for e in report["structured_errors"]]
        assert DOMAIN_TRANSLATION_ANCHOR_NOT_MET in codes


# ---------------------------------------------------------------------------
# Section 7: NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED structured payload
# ---------------------------------------------------------------------------


class TestNarrativeAnchorStructuredError:
    def _wp_narrative(self) -> dict:
        narrative_plan = {
            "anchor_role_id": "Lead Engineer | TechCorp",
            "theme_ranked": [
                {
                    "theme_id": "T1",
                    "label": "Platform Scale",
                    "signature_terms": ["platform scale", "distributed architecture"],
                },
                {
                    "theme_id": "T2",
                    "label": "Data Reliability",
                    "signature_terms": ["data reliability", "pipeline integrity"],
                },
            ],
            "summary_coverage": {"must_cover_theme_ids": []},
            "anchor_role_coverage": {
                "first_k_bullets": 2,
                "top_k_themes_to_cover": 2,
                "min_theme_occurrences": [
                    {"theme_id": "T1", "min_count": 1},
                    {"theme_id": "T2", "min_count": 1},
                ],
            },
            "domain_translation_binding": {},
        }
        return _minimal_wp(narrative_plan=narrative_plan)

    def test_anchor_dominance_structured_error(self):
        resume = _minimal_resume_with_role(
            "Lead Engineer | TechCorp | 2021 - Present",
            ["Built microservices.", "Wrote unit tests."],  # no theme terms
        )
        report = validate_phase2_output(self._wp_narrative(), resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED in codes

    def test_anchor_dominance_payload_shape(self):
        resume = _minimal_resume_with_role(
            "Lead Engineer | TechCorp | 2021 - Present",
            ["Built microservices.", "Wrote unit tests."],
        )
        report = validate_phase2_output(self._wp_narrative(), resume, "", "")
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED
        ]
        assert errs
        payload = errs[0]["payload"]
        assert payload["anchor_role_id"] == "Lead Engineer | TechCorp"
        assert payload["first_k_bullets"] == 2
        assert isinstance(payload["missing_theme_ids"], list)
        assert len(payload["missing_theme_ids"]) > 0
        assert isinstance(payload["required_signature_terms"], dict)
        for tid in payload["missing_theme_ids"]:
            assert tid in payload["required_signature_terms"]
            assert isinstance(payload["required_signature_terms"][tid], list)

    def test_anchor_dominance_no_error_when_terms_present(self):
        resume = _minimal_resume_with_role(
            "Lead Engineer | TechCorp | 2021 - Present",
            [
                "Improved platform scale via distributed architecture.",
                "Ensured data reliability and pipeline integrity.",
            ],
        )
        report = validate_phase2_output(self._wp_narrative(), resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED not in codes


# ---------------------------------------------------------------------------
# Section 8: NARRATIVE_SUMMARY_THEME_MISSING structured payload
# ---------------------------------------------------------------------------


class TestNarrativeSummaryStructuredError:
    def _wp_summary_themes(self) -> dict:
        narrative_plan = {
            "anchor_role_id": "Engineer | Co",
            "theme_ranked": [
                {
                    "theme_id": "T1",
                    "label": "Cloud Infrastructure",
                    "signature_terms": ["cloud infrastructure", "multi-cloud"],
                },
            ],
            "summary_coverage": {"must_cover_theme_ids": ["T1"]},
            "anchor_role_coverage": {
                "first_k_bullets": 3,
                "top_k_themes_to_cover": 1,
                "min_theme_occurrences": [{"theme_id": "T1", "min_count": 1}],
            },
            "domain_translation_binding": {},
        }
        return _minimal_wp(narrative_plan=narrative_plan)

    def test_summary_theme_missing_structured_error(self):
        resume = (
            "Professional Summary\n"
            "Experienced backend engineer with strong Python skills.\n\n"
            "Experience\nEngineer | Co | 2020 - Present\n"
            "- Built cloud infrastructure systems.\n"
            "- Improved multi-cloud pipelines.\n"
            "- Deployed services.\n"
        )
        report = validate_phase2_output(self._wp_summary_themes(), resume, "", "")
        codes = [e["code"] for e in report["structured_errors"]]
        assert NARRATIVE_SUMMARY_THEME_MISSING in codes

    def test_summary_theme_payload_shape(self):
        resume = (
            "Professional Summary\n"
            "Experienced backend engineer.\n\n"
            "Experience\nEngineer | Co | 2020 - Present\n"
            "- Built cloud infrastructure systems.\n"
            "- Improved multi-cloud pipelines.\n"
            "- Deployed services.\n"
        )
        report = validate_phase2_output(self._wp_summary_themes(), resume, "", "")
        errs = [
            e for e in report["structured_errors"]
            if e["code"] == NARRATIVE_SUMMARY_THEME_MISSING
        ]
        assert errs
        payload = errs[0]["payload"]
        assert "missing_theme_ids" in payload
        assert "T1" in payload["missing_theme_ids"]
        assert "required_signature_terms" in payload
        assert "T1" in payload["required_signature_terms"]
        assert isinstance(payload["required_signature_terms"]["T1"], list)


# ---------------------------------------------------------------------------
# Section 9: _build_validation_context
# ---------------------------------------------------------------------------


class TestBuildValidationContext:
    def test_returns_required_keys(self):
        wp = _minimal_wp(
            narrative_plan={
                "anchor_role_id": "Staff Engineer | Corp",
                "anchor_role_coverage": {"first_k_bullets": 3},
                "theme_ranked": [],
                "summary_coverage": {"must_cover_theme_ids": []},
                "domain_translation_binding": {},
            },
            cover_letter_plan={
                "structure_version": "CL_V1_4PARA_2PROOF",
                "bridge_sentence_required": True,
                "proof_points": [
                    {"proof_id": "P1", "required_exact_span": "span1", "allowed_tool_mentions": []},
                ],
                "hook_theme_id": "T1",
                "closing_guidance": "",
            },
            skill_allowlist_skills_section=["python", "go", "java"],
            domain_mismatch=True,
        )
        ctx = _build_validation_context(wp)
        assert ctx["anchor_role_id"] == "Staff Engineer | Corp"
        assert ctx["first_k_bullets"] == 3
        assert ctx["domain_mismatch"] is True
        assert "cl_plan_summary" in ctx
        assert isinstance(ctx["skills_truth_allowlist_sample"], list)

    def test_cl_plan_summary_has_proof_points(self):
        wp = _minimal_wp(
            cover_letter_plan={
                "structure_version": "CL_V1_4PARA_2PROOF",
                "bridge_sentence_required": False,
                "proof_points": [
                    {"proof_id": "P1", "required_exact_span": "abc", "allowed_tool_mentions": []},
                    {"proof_id": "P2", "required_exact_span": "def", "allowed_tool_mentions": []},
                ],
                "hook_theme_id": "T1",
                "closing_guidance": "",
            },
        )
        ctx = _build_validation_context(wp)
        pps = ctx["cl_plan_summary"]["proof_points"]
        assert len(pps) == 2
        assert pps[0]["proof_id"] == "P1"
        assert pps[0]["required_exact_span"] == "abc"

    def test_skills_allowlist_sample_max_20(self):
        big_list = [f"skill{i}" for i in range(30)]
        wp = _minimal_wp(skill_allowlist_skills_section=big_list)
        ctx = _build_validation_context(wp)
        assert len(ctx["skills_truth_allowlist_sample"]) <= 20

    def test_context_prefers_skill_policy_truth_allowlist(self):
        """When skill_policy present, skills_truth_allowlist_sample uses truth tier, not old field."""
        wp = _minimal_wp(
            skill_allowlist_skills_section=["old_skill"],
            skill_policy={
                "skills_truth_allowlist": ["new_skill_a", "new_skill_b"],
                "skills_truth_allowlist_norm": ["new_skill_a", "new_skill_b"],
            },
        )
        ctx = _build_validation_context(wp)
        sample = ctx["skills_truth_allowlist_sample"]
        assert "new_skill_a" in sample
        assert "old_skill" not in sample

    def test_empty_narrative_plan_defaults(self):
        wp = _minimal_wp(narrative_plan=None)
        ctx = _build_validation_context(wp)
        assert ctx["anchor_role_id"] == ""
        assert ctx["first_k_bullets"] == 3
        assert ctx["domain_mismatch"] is False


# ---------------------------------------------------------------------------
# Section 10: _derive_repair_targets
# ---------------------------------------------------------------------------


class TestDeriveRepairTargets:
    def test_cl_error_gives_cover_letter_target(self):
        errs = [{"code": "CL_PARAGRAPH_COUNT_INVALID", "message": "", "payload": {}}]
        assert "cover_letter" in _derive_repair_targets(errs)

    def test_cl_span_error_gives_cover_letter_target(self):
        errs = [{"code": "CL_P1_SPAN_MISSING", "message": "", "payload": {}}]
        assert "cover_letter" in _derive_repair_targets(errs)

    def test_skill_error_gives_skills_target(self):
        errs = [{"code": "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION", "message": "", "payload": {}}]
        assert "skills" in _derive_repair_targets(errs)

    def test_narrative_error_gives_anchor_role_target(self):
        errs = [{"code": "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED", "message": "", "payload": {}}]
        assert "anchor_role_bullets" in _derive_repair_targets(errs)

    def test_dt_error_gives_anchor_role_target(self):
        errs = [{"code": "DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET", "message": "", "payload": {}}]
        assert "anchor_role_bullets" in _derive_repair_targets(errs)

    def test_multiple_errors_multiple_targets(self):
        errs = [
            {"code": "CL_PARAGRAPH_COUNT_INVALID", "message": "", "payload": {}},
            {"code": "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION", "message": "", "payload": {}},
            {"code": "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED", "message": "", "payload": {}},
        ]
        targets = _derive_repair_targets(errs)
        assert "cover_letter" in targets
        assert "skills" in targets
        assert "anchor_role_bullets" in targets

    def test_empty_errors_empty_targets(self):
        assert _derive_repair_targets([]) == []

    def test_unknown_code_ignored(self):
        errs = [{"code": "SOME_OTHER_ERROR", "message": "", "payload": {}}]
        assert _derive_repair_targets(errs) == []

    def test_targets_are_sorted(self):
        errs = [
            {"code": "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION", "message": "", "payload": {}},
            {"code": "CL_PARAGRAPH_COUNT_INVALID", "message": "", "payload": {}},
        ]
        targets = _derive_repair_targets(errs)
        assert targets == sorted(targets)
