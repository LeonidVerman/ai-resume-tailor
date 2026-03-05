"""Tests for cover-letter validators V1–V4 and WriterPacket CL fields.

All tests are deterministic — no LLM calls, no file I/O.
"""

import json
from datetime import date

import pytest

from tailor.phase2_validator import (
    CL_BRIDGE_MISSING,
    CL_LEDGER_LOCATION_INVALID,
    CL_LEDGER_MISSING_ENTRY,
    CL_LEDGER_SPAN_NOT_FOUND,
    CL_P1_SPAN_MISSING,
    CL_P2_SPAN_MISSING,
    CL_PARAGRAPH_COUNT_INVALID,
    CL_TOOL_ALLOWLIST_VIOLATION,
    _split_cover_letter_paragraphs,
    _validate_cl_ledger,
    _validate_cl_paragraph_count,
    _validate_cl_required_spans,
    _validate_cl_tool_allowlist,
    validate_phase2_output,
)
from tailor.writer_packet import build_writer_packet


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _make_date() -> str:
    d = date.today()
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _minimal_resume() -> str:
    return (
        "Professional Summary\n"
        "Experienced engineer.\n\n"
        "Experience\n"
        "Senior Engineer | Acme Corp | 2020 - Present\n"
        f"- Scaled platform to 1M+ users using horizontal scaling\n"
        "- Implemented caching\n"
        "- Designed microservices architecture\n"
        "- Led backend platform team\n\n"
        "Technical Skills\n"
        "Languages: Python, Java\n"
        "Infrastructure: AWS, Docker\n"
    )


def _four_para_cover(
    p1_span: str = "reduced deployment cycle by 40%",
    p2_span: str = "scaled platform to 1M+ users",
    bridge: str = "I am excited to bring my expertise to your team.",
) -> str:
    return (
        "Dear Hiring Manager,\n"
        "Opening paragraph hooking the reader.\n\n"
        f"First proof: {p1_span} via CI/CD improvements.\n\n"
        f"Second proof: {p2_span} using horizontal scaling.\n\n"
        f"{bridge}\n"
        "Thank you for your consideration."
    )


def _three_para_cover() -> str:
    return (
        "Dear Hiring Manager,\n"
        "Opening paragraph.\n\n"
        "First proof paragraph with details.\n\n"
        "Closing paragraph with call to action."
    )


def _minimal_plan(
    bridge_required: bool = True,
    p1_span: str = "reduced deployment cycle by 40%",
    p2_span: str = "scaled platform to 1M+ users",
    direct_skills: list[str] | None = None,
) -> dict:
    return {
        "role_level": "senior",
        "jd_top_themes": [
            {
                "theme": "Scalability",
                "priority": "primary",
                "why_important": "important",
                "keywords": ["distributed systems", "microservices", "cloud"],
            }
        ],
        "evidence_map": [
            {
                "theme": "Scalability",
                "evidence": [
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Scaled platform",
                        "allowed_claims": ["scaled platform"],
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
        "vocabulary_anchoring": {"must_embed": [], "optional_embed": []},
        "jd_domain": "b2b_saas_platform",
        "candidate_primary_domain": "b2b_saas_platform",
        "domain_mismatch": False,
        "domain_translation_rule_ids": [],
        "narrative_plan": {
            "anchor_role_id": "Senior Engineer | Acme Corp",
            "theme_ranked": [
                {
                    "theme_id": "scalability",
                    "label": "Scalability",
                    "priority": "primary",
                    "signature_terms": ["scaling", "distributed"],
                }
            ],
            "summary_coverage": {
                "must_cover_theme_ids": [],
                "should_cover_theme_ids": [],
            },
            "anchor_role_coverage": {
                "first_k_bullets": 3,
                "top_k_themes_to_cover": 1,
                "min_theme_occurrences": {},
            },
            "domain_translation_binding": {
                "min_total_rule_instantiations": 0,
                "min_instantiations_in_anchor_role": 0,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        },
        "skill_graph": {
            "direct_skills": direct_skills or ["Python", "AWS", "Docker"],
            "related_skills": [
                {
                    "root_skill": "Python",
                    "item": "FastAPI",
                    "derivation_type": "tool",
                    "confidence": "high",
                    "allowed_usage": "can_claim_experience",
                },
                {
                    "root_skill": "AWS",
                    "item": "ECS",
                    "derivation_type": "tool",
                    "confidence": "medium",
                    "allowed_usage": "skills_section_only",
                },
                {
                    "root_skill": "AWS",
                    "item": "Azure",
                    "derivation_type": "tool",
                    "confidence": "low",
                    "allowed_usage": "forbidden",
                },
            ],
        },
        "cover_letter_plan": {
            "structure_version": "CL_V1_4PARA_2PROOF",
            "hook_theme_id": "scalability",
            "proof_points": [
                {
                    "proof_id": "P1",
                    "theme_id": "scalability",
                    "evidence_ref": "Acme Corp CI/CD work",
                    "mechanism_hint": "CI/CD pipeline",
                    "outcome_hint": "faster deploys",
                    "required_exact_span": p1_span,
                    "allowed_tool_mentions": ["Docker", "FastAPI"],
                },
                {
                    "proof_id": "P2",
                    "theme_id": "scalability",
                    "evidence_ref": "Acme Corp platform scaling",
                    "mechanism_hint": "horizontal scaling",
                    "outcome_hint": "1M+ users",
                    "required_exact_span": p2_span,
                    "allowed_tool_mentions": ["AWS"],
                },
            ],
            "bridge_sentence_required": bridge_required,
            "closing_guidance": "Express enthusiasm for the role.",
        },
    }


def _candidate_profile_str() -> str:
    return json.dumps(
        {
            "candidate": {"name": "Test User"},
            "experience_highlights": [],
            "technical_skills": {
                "languages": ["Python", "Java"],
                "infra_devops": ["AWS", "Docker"],
            },
            "scalability_reliability_patterns": [],
        }
    )


# ---------------------------------------------------------------------------
# Unit tests: _split_cover_letter_paragraphs
# ---------------------------------------------------------------------------

class TestSplitCoverLetterParagraphs:
    def test_splits_on_blank_line(self):
        text = "Para 1.\n\nPara 2.\n\nPara 3."
        assert _split_cover_letter_paragraphs(text) == ["Para 1.", "Para 2.", "Para 3."]

    def test_multiple_blank_lines_treated_as_one(self):
        text = "Para 1.\n\n\nPara 2."
        assert _split_cover_letter_paragraphs(text) == ["Para 1.", "Para 2."]

    def test_strips_whitespace(self):
        text = "  Para 1.  \n\n  Para 2.  "
        assert _split_cover_letter_paragraphs(text) == ["Para 1.", "Para 2."]

    def test_empty_string_gives_empty_list(self):
        assert _split_cover_letter_paragraphs("") == []


# ---------------------------------------------------------------------------
# Unit tests: V1 – _validate_cl_paragraph_count
# ---------------------------------------------------------------------------

class TestV1ParagraphCount:
    def test_bridge_required_4_paragraphs_ok(self):
        paras = ["P1", "P2", "P3", "P4"]
        assert _validate_cl_paragraph_count(paras, bridge_required=True) == []

    def test_bridge_required_3_paragraphs_fails(self):
        paras = ["P1", "P2", "P3"]
        errs = _validate_cl_paragraph_count(paras, bridge_required=True)
        assert len(errs) == 1
        assert CL_PARAGRAPH_COUNT_INVALID in errs[0]

    def test_bridge_required_5_paragraphs_fails(self):
        paras = ["P1", "P2", "P3", "P4", "P5"]
        errs = _validate_cl_paragraph_count(paras, bridge_required=True)
        assert len(errs) == 1
        assert CL_PARAGRAPH_COUNT_INVALID in errs[0]

    def test_no_bridge_3_paragraphs_ok(self):
        paras = ["P1", "P2", "P3"]
        assert _validate_cl_paragraph_count(paras, bridge_required=False) == []

    def test_no_bridge_4_paragraphs_ok(self):
        paras = ["P1", "P2", "P3", "P4"]
        assert _validate_cl_paragraph_count(paras, bridge_required=False) == []

    def test_no_bridge_2_paragraphs_fails(self):
        paras = ["P1", "P2"]
        errs = _validate_cl_paragraph_count(paras, bridge_required=False)
        assert len(errs) == 1
        assert CL_PARAGRAPH_COUNT_INVALID in errs[0]

    def test_no_bridge_5_paragraphs_fails(self):
        paras = ["P1", "P2", "P3", "P4", "P5"]
        errs = _validate_cl_paragraph_count(paras, bridge_required=False)
        assert len(errs) == 1
        assert CL_PARAGRAPH_COUNT_INVALID in errs[0]


# ---------------------------------------------------------------------------
# Unit tests: V2 – _validate_cl_required_spans
# ---------------------------------------------------------------------------

class TestV2RequiredSpans:
    def _cl_plan(self, p1: str, p2: str, bridge: bool = False) -> dict:
        return {
            "proof_points": [
                {"proof_id": "P1", "required_exact_span": p1},
                {"proof_id": "P2", "required_exact_span": p2},
            ],
            "bridge_sentence_required": bridge,
        }

    def test_valid_spans_no_errors(self):
        paras = [
            "Hook paragraph.",
            "First proof: reduced deployment cycle by 40%.",
            "Second proof: scaled platform to 1M+ users.",
            "Bridge and closing.",
        ]
        plan = self._cl_plan("reduced deployment cycle by 40%", "scaled platform to 1M+ users")
        assert _validate_cl_required_spans(paras, plan) == []

    def test_p1_span_missing_gives_error(self):
        paras = [
            "Hook.",
            "First proof: different text here.",
            "Second proof: scaled platform to 1M+ users.",
            "Bridge.",
        ]
        plan = self._cl_plan("reduced deployment cycle by 40%", "scaled platform to 1M+ users")
        errs = _validate_cl_required_spans(paras, plan)
        assert any(CL_P1_SPAN_MISSING in e for e in errs)
        assert not any(CL_P2_SPAN_MISSING in e for e in errs)

    def test_p2_span_missing_gives_error(self):
        paras = [
            "Hook.",
            "First proof: reduced deployment cycle by 40%.",
            "Second proof: wrong content.",
            "Bridge.",
        ]
        plan = self._cl_plan("reduced deployment cycle by 40%", "scaled platform to 1M+ users")
        errs = _validate_cl_required_spans(paras, plan)
        assert any(CL_P2_SPAN_MISSING in e for e in errs)
        assert not any(CL_P1_SPAN_MISSING in e for e in errs)

    def test_bridge_missing_when_required_and_only_3_paras(self):
        paras = [
            "Hook.",
            "P1 proof: reduced deployment cycle by 40%.",
            "P2 proof: scaled platform to 1M+ users.",
        ]
        plan = self._cl_plan(
            "reduced deployment cycle by 40%",
            "scaled platform to 1M+ users",
            bridge=True,
        )
        errs = _validate_cl_required_spans(paras, plan)
        assert any(CL_BRIDGE_MISSING in e for e in errs)

    def test_bridge_not_required_3_paras_ok(self):
        paras = [
            "Hook.",
            "First proof: reduced deployment cycle by 40%.",
            "Second proof: scaled platform to 1M+ users.",
        ]
        plan = self._cl_plan(
            "reduced deployment cycle by 40%",
            "scaled platform to 1M+ users",
            bridge=False,
        )
        assert _validate_cl_required_spans(paras, plan) == []

    def test_not_enough_paragraphs_for_p1_index(self):
        # Only 1 paragraph, P1 needs index 1 (second paragraph)
        paras = ["Only one paragraph."]
        plan = self._cl_plan("reduced deployment cycle by 40%", "scaled platform to 1M+ users")
        errs = _validate_cl_required_spans(paras, plan)
        assert any(CL_P1_SPAN_MISSING in e for e in errs)


# ---------------------------------------------------------------------------
# Unit tests: V3 – _validate_cl_ledger
# ---------------------------------------------------------------------------

class TestV3Ledger:
    def _plan(self, bridge: bool = False) -> dict:
        return {"bridge_sentence_required": bridge}

    def test_valid_ledger_no_errors(self):
        cover = "Intro.\n\nP1 proof: the exact span here.\n\nP2 proof: another exact span."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "the exact span here", "location": "paragraph[2]"},
            {"proof_id": "P2", "exact_span": "another exact span", "location": "paragraph[3]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert errs == []

    def test_missing_p1_entry(self):
        cover = "Intro.\n\nP1.\n\nP2: exact span."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P2", "exact_span": "exact span", "location": "paragraph[3]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert any(CL_LEDGER_MISSING_ENTRY in e and "P1" in e for e in errs)

    def test_span_not_in_cover_letter(self):
        cover = "Intro.\n\nSome P1 text.\n\nSome P2 text."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "DOES NOT EXIST", "location": "paragraph[2]"},
            {"proof_id": "P2", "exact_span": "Some P2 text", "location": "paragraph[3]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert any(CL_LEDGER_SPAN_NOT_FOUND in e and "P1" in e for e in errs)

    def test_invalid_location_format(self):
        cover = "Intro.\n\nThe span here.\n\nAnother span."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "The span here", "location": "para_2"},
            {"proof_id": "P2", "exact_span": "Another span", "location": "paragraph[3]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert any(CL_LEDGER_LOCATION_INVALID in e and "P1" in e for e in errs)

    def test_location_out_of_range(self):
        cover = "Intro.\n\nThe span here."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "The span here", "location": "paragraph[5]"},
            {"proof_id": "P2", "exact_span": "intro", "location": "paragraph[1]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert any(CL_LEDGER_LOCATION_INVALID in e and "out of range" in e for e in errs)

    def test_span_in_wrong_paragraph(self):
        # Span exists in the cover letter but is attributed to wrong paragraph
        cover = "The span is here.\n\nSecond paragraph.\n\nThird paragraph."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            # span is in paragraph 1, ledger claims paragraph 2
            {"proof_id": "P1", "exact_span": "The span is here", "location": "paragraph[2]"},
            {"proof_id": "P2", "exact_span": "Second paragraph", "location": "paragraph[2]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan())
        assert any(CL_LEDGER_LOCATION_INVALID in e and "P1" in e for e in errs)

    def test_bridge_entry_required_when_flag_set(self):
        cover = "Intro.\n\nP1 span here.\n\nP2 span here.\n\nBridge sentence here."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "P1 span here", "location": "paragraph[2]"},
            {"proof_id": "P2", "exact_span": "P2 span here", "location": "paragraph[3]"},
            # BRIDGE missing
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan(bridge=True))
        assert any(CL_LEDGER_MISSING_ENTRY in e and "BRIDGE" in e for e in errs)

    def test_bridge_entry_valid(self):
        cover = "Intro.\n\nP1 span here.\n\nP2 span here.\n\nBridge sentence here."
        paras = _split_cover_letter_paragraphs(cover)
        ledger = [
            {"proof_id": "P1", "exact_span": "P1 span here", "location": "paragraph[2]"},
            {"proof_id": "P2", "exact_span": "P2 span here", "location": "paragraph[3]"},
            {"proof_id": "BRIDGE", "exact_span": "Bridge sentence here", "location": "paragraph[4]"},
        ]
        errs = _validate_cl_ledger(cover, paras, ledger, self._plan(bridge=True))
        assert errs == []


# ---------------------------------------------------------------------------
# Unit tests: V4 – _validate_cl_tool_allowlist
# ---------------------------------------------------------------------------

class TestV4ToolAllowlist:
    def _cl_plan(self, p1_allowed: list[str], p2_allowed: list[str]) -> dict:
        return {
            "proof_points": [
                {
                    "proof_id": "P1",
                    "required_exact_span": "span",
                    "allowed_tool_mentions": p1_allowed,
                },
                {
                    "proof_id": "P2",
                    "required_exact_span": "span",
                    "allowed_tool_mentions": p2_allowed,
                },
            ],
        }

    def test_no_high_risk_tokens_ok(self):
        paras = [
            "Hook paragraph.",
            "I built distributed systems using proven patterns.",
            "We scaled using sound principles.",
        ]
        plan = self._cl_plan([], [])
        errs = _validate_cl_tool_allowlist(paras, plan, set())
        assert errs == []

    def test_allowed_tool_not_flagged(self):
        paras = [
            "Hook.",
            "I used Docker to containerize workloads.",
            "We scaled with AWS services.",
        ]
        plan = self._cl_plan(["docker"], ["aws"])
        errs = _validate_cl_tool_allowlist(paras, plan, set())
        assert errs == []

    def test_unlisted_azure_in_p1_flagged(self):
        paras = [
            "Hook.",
            "I built a platform on Azure for high availability.",
            "We used native cloud tooling.",
        ]
        plan = self._cl_plan([], [])  # Azure not in allowed
        errs = _validate_cl_tool_allowlist(paras, plan, set())
        assert any(CL_TOOL_ALLOWLIST_VIOLATION in e and "azure" in e for e in errs)

    def test_unlisted_terraform_in_p2_flagged(self):
        paras = [
            "Hook.",
            "We improved build times significantly.",
            "Infrastructure managed via Terraform across regions.",
        ]
        plan = self._cl_plan([], [])
        errs = _validate_cl_tool_allowlist(paras, plan, set())
        assert any(CL_TOOL_ALLOWLIST_VIOLATION in e and "terraform" in e for e in errs)

    def test_tool_in_direct_skills_set_not_flagged(self):
        paras = [
            "Hook.",
            "We deployed using Azure managed services.",
            "Cloud cost optimized.",
        ]
        plan = self._cl_plan([], [])
        # Azure in direct_skills_set overrides the restriction
        errs = _validate_cl_tool_allowlist(paras, plan, {"azure"})
        assert errs == []

    def test_tool_in_hook_paragraph_not_flagged(self):
        # Hook paragraph (index 0) is not checked
        paras = [
            "We use Azure and Terraform widely.",
            "P1 paragraph is clean.",
            "P2 paragraph is also clean.",
        ]
        plan = self._cl_plan([], [])
        errs = _validate_cl_tool_allowlist(paras, plan, set())
        assert errs == []


# ---------------------------------------------------------------------------
# Integration tests: validate_phase2_output with cover_letter_plan
# ---------------------------------------------------------------------------

class TestValidateCLIntegration:
    """End-to-end tests using validate_phase2_output with a writer_packet
    that contains cover_letter_plan."""

    def _make_packet(self, plan: dict | None = None) -> dict:
        if plan is None:
            plan = _minimal_plan()
        return build_writer_packet(
            plan,
            _candidate_profile_str(),
            _minimal_resume(),
            "We need a senior engineer for distributed systems work.",
        )

    def test_valid_four_para_cover_passes(self):
        packet = self._make_packet()
        cover = _four_para_cover()
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        cl_errs = [
            e for e in report["errors"]
            if e.startswith("CL_")
        ]
        assert cl_errs == [], f"Unexpected CL errors: {cl_errs}"

    def test_wrong_paragraph_count_gives_cl_error(self):
        packet = self._make_packet()
        # Only 2 paragraphs (bridge_required=True expects 4)
        cover = "Opening paragraph.\n\nOnly two paragraphs total."
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        assert any(CL_PARAGRAPH_COUNT_INVALID in e for e in report["errors"])

    def test_missing_p1_span_gives_cl_error(self):
        packet = self._make_packet()
        # P1 required span is "reduced deployment cycle by 40%" but we omit it
        cover = (
            "Opening paragraph.\n\n"
            "First proof paragraph without the required span.\n\n"
            "Second proof: scaled platform to 1M+ users.\n\n"
            "I am excited to bring my expertise to your team. Thank you."
        )
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        assert any(CL_P1_SPAN_MISSING in e for e in report["errors"])

    def test_missing_p2_span_gives_cl_error(self):
        packet = self._make_packet()
        cover = (
            "Opening paragraph.\n\n"
            "First proof: reduced deployment cycle by 40%.\n\n"
            "Second proof without required span.\n\n"
            "I am excited to bring my expertise to your team. Thank you."
        )
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        assert any(CL_P2_SPAN_MISSING in e for e in report["errors"])

    def test_unlisted_tool_in_p1_gives_cl_error(self):
        packet = self._make_packet()
        # "react" is not in allowed_tool_mentions for P1
        cover = (
            "Opening paragraph.\n\n"
            "First proof using React and reduced deployment cycle by 40%.\n\n"
            "Second proof: scaled platform to 1M+ users.\n\n"
            "I am excited to bring my expertise to your team. Thank you."
        )
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        assert any(CL_TOOL_ALLOWLIST_VIOLATION in e for e in report["errors"])

    def test_valid_cover_letter_ledger_passes(self):
        packet = self._make_packet()
        cover = _four_para_cover()
        ledger = [
            {
                "proof_id": "P1",
                "exact_span": "reduced deployment cycle by 40%",
                "location": "paragraph[2]",
            },
            {
                "proof_id": "P2",
                "exact_span": "scaled platform to 1M+ users",
                "location": "paragraph[3]",
            },
            {
                "proof_id": "BRIDGE",
                "exact_span": "I am excited to bring my expertise to your team.",
                "location": "paragraph[4]",
            },
        ]
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date(),
            cover_letter_ledger=ledger,
        )
        cl_errs = [e for e in report["errors"] if e.startswith("CL_")]
        assert cl_errs == [], f"Unexpected CL errors: {cl_errs}"

    def test_missing_ledger_entry_gives_error(self):
        packet = self._make_packet()
        cover = _four_para_cover()
        ledger = [
            # Only P2; P1 and BRIDGE missing
            {
                "proof_id": "P2",
                "exact_span": "scaled platform to 1M+ users",
                "location": "paragraph[3]",
            },
        ]
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date(),
            cover_letter_ledger=ledger,
        )
        assert any(CL_LEDGER_MISSING_ENTRY in e and "P1" in e for e in report["errors"])
        assert any(CL_LEDGER_MISSING_ENTRY in e and "BRIDGE" in e for e in report["errors"])

    def test_cl_errors_in_repair_brief(self):
        packet = self._make_packet()
        # Malformed cover: too few paragraphs
        cover = "One paragraph only, no blank lines."
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        assert "cl_paragraph_errors" in report["repair_brief"]["global_issues"]
        assert len(report["repair_brief"]["global_issues"]["cl_paragraph_errors"]) > 0

    def test_cl_stats_present_in_report(self):
        packet = self._make_packet()
        cover = _four_para_cover()
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        for key in ("cl_paragraph_errors", "cl_span_errors", "cl_ledger_errors", "cl_tool_errors"):
            assert key in report["stats"], f"Missing stats key: {key}"

    def test_no_cl_plan_in_packet_skips_validators(self):
        """When cover_letter_plan is absent, CL validators must not run."""
        plan = _minimal_plan()
        plan.pop("cover_letter_plan")  # remove CL plan
        packet = build_writer_packet(
            plan,
            _candidate_profile_str(),
            _minimal_resume(),
            "Distributed systems engineering role.",
        )
        # Intentionally bad cover letter (1 paragraph)
        cover = "Just one paragraph with no blank lines at all."
        report = validate_phase2_output(
            packet, _minimal_resume(), cover, _make_date()
        )
        cl_errs = [e for e in report["errors"] if e.startswith("CL_")]
        assert cl_errs == [], "CL validators ran without cover_letter_plan"


# ---------------------------------------------------------------------------
# WriterPacket CL fields tests
# ---------------------------------------------------------------------------

class TestWriterPacketCLFields:
    def _build(self, plan: dict | None = None) -> dict:
        if plan is None:
            plan = _minimal_plan()
        return build_writer_packet(
            plan,
            _candidate_profile_str(),
            _minimal_resume(),
            "Distributed systems role.",
        )

    def test_cover_letter_plan_present_in_packet(self):
        wp = self._build()
        assert "cover_letter_plan" in wp
        assert wp["cover_letter_plan"]["structure_version"] == "CL_V1_4PARA_2PROOF"

    def test_direct_skills_set_lowercase(self):
        wp = self._build()
        ds = wp["direct_skills_set"]
        assert "python" in ds
        assert "aws" in ds
        assert all(s == s.lower() for s in ds)

    def test_can_claim_experience_set_contains_fastapi(self):
        wp = self._build()
        assert "fastapi" in wp["can_claim_experience_set"]

    def test_can_claim_experience_set_excludes_skills_section_only(self):
        wp = self._build()
        # ECS has allowed_usage=skills_section_only, should NOT be in can_claim
        assert "ecs" not in wp["can_claim_experience_set"]

    def test_skills_section_allowlist_set_includes_skills_section_only(self):
        wp = self._build()
        # ECS is skills_section_only → should be in skills_section_allowlist_set
        assert "ecs" in wp["skills_section_allowlist_set"]

    def test_forbidden_skill_excluded_from_all_sets(self):
        wp = self._build()
        # "Azure" has allowed_usage=forbidden
        assert "azure" not in wp["direct_skills_set"]
        assert "azure" not in wp["can_claim_experience_set"]
        assert "azure" not in wp["skills_section_allowlist_set"]

    def test_allowed_tool_mentions_sanitized(self):
        """allowed_tool_mentions containing forbidden items must be stripped."""
        plan = _minimal_plan()
        # Inject an illegal entry: "azure" is forbidden in skill_graph
        plan["cover_letter_plan"]["proof_points"][0]["allowed_tool_mentions"] = [
            "Docker", "Azure"
        ]
        wp = build_writer_packet(
            plan,
            _candidate_profile_str(),
            _minimal_resume(),
            "Role.",
        )
        p1 = next(
            p for p in wp["cover_letter_plan"]["proof_points"]
            if p["proof_id"] == "P1"
        )
        mentions_lower = [m.lower() for m in p1["allowed_tool_mentions"]]
        assert "azure" not in mentions_lower
        assert "docker" in mentions_lower

    def test_allowed_tool_mentions_retains_valid_entries(self):
        wp = self._build()
        p1 = next(
            p for p in wp["cover_letter_plan"]["proof_points"]
            if p["proof_id"] == "P1"
        )
        # "Docker" is a direct skill, "FastAPI" is can_claim_experience → both allowed
        mentions_lower = [m.lower() for m in p1["allowed_tool_mentions"]]
        assert "docker" in mentions_lower
        assert "fastapi" in mentions_lower
