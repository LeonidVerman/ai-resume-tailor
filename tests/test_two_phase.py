"""Regression tests for the two-phase tailoring pipeline.

These tests use mocked OpenAI responses so they run without API access.
"""

import json
import sys
import types
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from tailor.config import PHASE1_MODEL

# ---------------------------------------------------------------------------
# Helpers to build minimal valid fixtures
# ---------------------------------------------------------------------------

def _minimal_plan() -> dict:
    """Return a minimal valid TailoringPlan (matches schemas/phase1_output.json)."""
    return {
        "role_level": "senior",
        "resume_mode": "technical_depth",
        "jd_domain": "b2b_saas_platform",
        "candidate_primary_domain": "fintech_trading",
        "domain_mismatch": False,
        "domain_translation_rule_ids": [],
        "jd_top_themes": [
            {"theme": f"Theme {i}", "priority": "primary", "why_important": "important", "keywords": ["kw"]}
            for i in range(5)
        ],
        "vocabulary_anchoring": {
            "must_embed": ["distributed systems", "scalability"],
            "optional_embed": ["cloud-native"],
        },
        "evidence_map": [
            {
                "theme": "Theme 0",
                "evidence": [
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Scaled platform to 1M+ users across distributed services",
                        "allowed_claims": ["scaled to 1M+ users"],
                    },
                    {
                        "source": "candidate_profile",
                        "location": "experience_highlights",
                        "quote": "Built horizontally scaled microservices handling 1M+ concurrent users",
                        "allowed_claims": ["horizontal scaling"],
                    },
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Implemented multi-layer caching reducing latency by 25%",
                        "allowed_claims": ["caching", "25% latency reduction"],
                    },
                ],
                "gaps": [],
                "safe_translation": [],
            },
            {
                "theme": "Theme 1",
                "evidence": [
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Designed async messaging pipeline for order processing",
                        "allowed_claims": ["async messaging"],
                    },
                    {
                        "source": "master_resume",
                        "location": "Acme Corp",
                        "quote": "Led distributed team of 6 engineers across 3 regions",
                        "allowed_claims": ["distributed team leadership"],
                    },
                ],
                "gaps": [],
                "safe_translation": [],
            },
        ],
        "resume_strategy": {
            "summary": {"include_points": ["focus"], "avoid_points": []},
            "experience": [
                {
                    "role_name": "Senior Engineer | Acme Corp",
                    "priority": "high",
                    "keep_metrics": ["1M+ users"],
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
            "company_and_role_mentions": ["Acme Corp", "Senior Engineer"],
            "bullet_overlaps_to_reference": [],
            "structure": ["intro", "body", "close"],
        },
        "risk_checks": {
            "do_not_invent": ["BGP", "OSPF"],
            "likely_hallucination_traps": ["networking protocols"],
            "claims_requiring_strict_grounding": ["compliance statements"],
        },
        "narrative_plan": {
            "anchor_role_id": "Senior Engineer | Acme Corp",
            "theme_ranked": [
                {
                    "theme_id": "T1",
                    "label": "Scalability",
                    "priority": "primary",
                    "signature_terms": ["scalability", "distributed systems"],
                },
                {
                    "theme_id": "T2",
                    "label": "Cloud",
                    "priority": "secondary",
                    "signature_terms": ["cloud", "microservices"],
                },
            ],
            "summary_coverage": {
                "must_cover_theme_ids": ["T1", "T2"],
                "should_cover_theme_ids": [],
            },
            "anchor_role_coverage": {
                "first_k_bullets": 3,
                "top_k_themes_to_cover": 2,
                "min_theme_occurrences": {"T1": 1, "T2": 1},
            },
            "domain_translation_binding": {
                "min_total_rule_instantiations": 0,
                "min_instantiations_in_anchor_role": 0,
                "require_target_frame_in_anchor_role_first_k": False,
            },
        },
        "skill_graph": {
            "direct_skills": ["Python", "Docker", "Kubernetes", "PostgreSQL", "REST APIs"],
            "related_skills": [],
        },
    }


def _make_openai_response(content: str):
    """Build a minimal mock that resembles openai.chat.completions.create return."""
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage.prompt_tokens = 100
    resp.usage.completion_tokens = 200
    resp.usage.total_tokens = 300
    return resp


# ---------------------------------------------------------------------------
# validate_plan tests
# ---------------------------------------------------------------------------

class TestValidatePlan:
    def test_valid_plan_passes(self):
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        assert validate_plan(plan) is plan

    def test_missing_key_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        del plan["role_level"]
        with pytest.raises(PlanValidationError, match="missing required keys"):
            validate_plan(plan)

    def test_unrecognised_role_level_coerced_to_junior(self):
        """Unknown role_level strings are coerced to 'junior' rather than raising."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "wizard"
        result = validate_plan(plan)
        assert result["role_level"] == "junior"

    def test_job_title_role_level_coerced(self):
        """Job-title strings like 'Senior Software Engineer' are coerced to 'senior'."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Senior Software Engineer"
        result = validate_plan(plan)
        assert result["role_level"] == "senior"

    def test_vp_role_level_coerced_to_director(self):
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "VP of Engineering"
        result = validate_plan(plan)
        assert result["role_level"] == "director"

    def test_too_few_themes_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        plan["jd_top_themes"] = plan["jd_top_themes"][:3]  # only 3 — below minimum of 4
        with pytest.raises(PlanValidationError, match="jd_top_themes"):
            validate_plan(plan)

    def test_quote_too_long_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        long_quote = " ".join(["word"] * 26)
        plan["evidence_map"][0]["evidence"][0]["quote"] = long_quote
        with pytest.raises(PlanValidationError, match="25-word limit"):
            validate_plan(plan)

    def test_not_a_dict_raises(self):
        from tailor.llm import PlanValidationError, validate_plan
        with pytest.raises(PlanValidationError, match="JSON object"):
            validate_plan(["not", "a", "dict"])

    # ---- A1: new role levels accepted directly ----

    def test_new_role_levels_accepted(self):
        """manager / principal / staff are valid enum values and pass unchanged."""
        from tailor.llm import validate_plan
        for level in ("manager", "principal", "staff"):
            plan = _minimal_plan()
            plan["role_level"] = level
            assert validate_plan(plan)["role_level"] == level, (
                f"Expected {level!r} to be accepted as-is"
            )

    # ---- A1: coercion of job-title strings for new levels ----

    def test_engineering_manager_coerced_to_manager(self):
        """'Engineering Manager' job title is coerced to 'manager'."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Engineering Manager"
        assert validate_plan(plan)["role_level"] == "manager"

    def test_manager_engineering_coerced_to_manager(self):
        """'Manager, Engineering' coerces to 'manager'."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Manager, Engineering"
        assert validate_plan(plan)["role_level"] == "manager"

    def test_staff_engineer_coerced_to_staff(self):
        """'Staff Engineer' job title is coerced to 'staff' (not 'senior')."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Staff Engineer"
        assert validate_plan(plan)["role_level"] == "staff"

    def test_principal_engineer_coerced_to_principal(self):
        """'Principal Software Engineer' is coerced to 'principal'."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Principal Software Engineer"
        assert validate_plan(plan)["role_level"] == "principal"

    def test_head_of_engineering_coerced_to_director(self):
        """'Head of Engineering' is coerced to 'director'."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["role_level"] = "Head of Engineering"
        assert validate_plan(plan)["role_level"] == "director"


# ---------------------------------------------------------------------------
# Phase 1 — plan_tailoring
# ---------------------------------------------------------------------------

class TestPlanTailoring:
    def test_returns_validated_plan_and_meta(self):
        from tailor.job import JobData
        from tailor.llm import plan_tailoring

        plan = _minimal_plan()
        mock_response = _make_openai_response(json.dumps(plan))

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", return_value="prompt text"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value='{"name": "Test"}'),
        ):
            mock_get_client.return_value.chat.completions.create.return_value = mock_response
            job = JobData(company="Acme", job_title="Senior Engineer", description="Job desc")
            returned_plan, messages, meta = plan_tailoring(job, "resume text", "cover text")

        assert returned_plan["role_level"] == "senior"
        assert len(returned_plan["jd_top_themes"]) == 5
        assert meta["model"] is not None
        assert meta["usage"]["total_tokens"] == 300
        # developer message must be present and API params recorded
        assert messages["messages"][0]["role"] == "developer"
        assert messages["model"] == PHASE1_MODEL
        assert "temperature" in messages
        assert messages.get("response_format", {}).get("type") == "json_schema"

    def test_current_date_injected(self):
        from tailor.job import JobData
        from tailor.llm import plan_tailoring

        plan = _minimal_plan()
        mock_response = _make_openai_response(json.dumps(plan))
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.extend(kwargs["messages"])
            return mock_response

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.side_effect = fake_create
            job = JobData(company="Acme", job_title="Senior Engineer", description="desc")
            plan_tailoring(job, "resume", "cover")

        date_messages = [m for m in captured_messages if "CURRENT_DATE" in m.get("content", "")]
        assert len(date_messages) >= 1
        today = date.today()
        expected_date = f"{today.strftime('%B')} {today.day}, {today.year}"
        assert expected_date in date_messages[0]["content"]


# ---------------------------------------------------------------------------
# Phase 2 — tailor_documents_with_plan
# ---------------------------------------------------------------------------

class TestTailorDocumentsWithPlan:
    def test_writer_packet_is_first_user_message_and_plan_is_second(self):
        """Phase 2 writer: WRITER_PACKET is first user message, TAILORING_PLAN is second."""
        from tailor.job import JobData
        from tailor.llm import tailor_documents_with_plan

        plan = _minimal_plan()
        output = {"resume": "tailored resume with 1M+ users", "cover_letter": "Dear Hiring Manager"}
        mock_response = _make_openai_response(json.dumps(output))
        captured_messages = []

        def fake_create(**kwargs):
            captured_messages.extend(kwargs["messages"])
            return mock_response

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.side_effect = fake_create
            job = JobData(company="Acme", job_title="Senior Engineer", description="desc")
            result, messages, meta = tailor_documents_with_plan(plan, job, "resume", "cover")

        user_messages = [m for m in captured_messages if m["role"] == "user"]
        # WRITER_PACKET is first, TAILORING_PLAN is second
        assert "WRITER_PACKET" in user_messages[0]["content"]
        assert "TAILORING_PLAN" in user_messages[1]["content"]
        assert '"role_level"' in user_messages[1]["content"]

    def test_plan_metric_preserved_in_output(self):
        """If the plan keep_metrics contains '1M+ users', the output should contain it."""
        from tailor.job import JobData
        from tailor.llm import tailor_documents_with_plan

        plan = _minimal_plan()
        metric = "1M+ users"
        output = {
            "resume": f"Senior Engineer at Acme. Scaled platform to {metric}.",
            "cover_letter": "I bring strong backend experience.",
        }
        mock_response = _make_openai_response(json.dumps(output))

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.return_value = mock_response
            job = JobData(company="Acme", job_title="Senior Engineer", description="desc")
            result, _, _ = tailor_documents_with_plan(plan, job, "resume", "cover")

        assert result.resume is not None
        assert metric in result.resume

    def test_date_in_cover_letter(self):
        """Cover letter must contain today's date in 'Month D, YYYY' format."""
        from tailor.job import JobData
        from tailor.llm import tailor_documents_with_plan

        plan = _minimal_plan()
        today = date.today()
        current_date = f"{today.strftime('%B')} {today.day}, {today.year}"
        output = {
            "resume": "Resume text",
            "cover_letter": f"{current_date}\n\nDear Hiring Manager,\n\nI am writing...",
        }
        mock_response = _make_openai_response(json.dumps(output))

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.return_value = mock_response
            job = JobData(company="Acme", job_title="Senior Engineer", description="desc")
            result, _, _ = tailor_documents_with_plan(plan, job, "resume", "cover")

        assert result.cover_letter is not None
        assert current_date in result.cover_letter

    def test_returns_tailor_result_and_meta(self):
        from tailor.job import JobData
        from tailor.llm import TailorResult, tailor_documents_with_plan

        plan = _minimal_plan()
        output = {"resume": "resume", "cover_letter": "cover"}
        mock_response = _make_openai_response(json.dumps(output))

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", return_value=""),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.return_value = mock_response
            job = JobData(company="Acme", job_title="Senior Engineer", description="desc")
            result, messages, meta = tailor_documents_with_plan(plan, job, "resume", "cover")

        assert isinstance(result, TailorResult)
        assert result.resume is not None and "resume" in result.resume  # postprocessors may augment
        assert "cover" in result.cover_letter  # postprocessor may prepend current_date
        assert meta["usage"]["total_tokens"] == 300


# ---------------------------------------------------------------------------
# Phase 2 — prompt selection
# ---------------------------------------------------------------------------

class TestPhase2PromptSelection:
    """Phase 2 selects the correct developer prompt based on file availability."""

    def _run_with_phase2_prompt(self, phase2_present: bool) -> tuple:
        """Helper: run tailor_documents_with_plan and capture developer message + meta."""
        from tailor.job import JobData
        from tailor.llm import tailor_documents_with_plan

        plan = _minimal_plan()
        output = {"resume": "resume text", "cover_letter": "cover text"}
        mock_response = _make_openai_response(json.dumps(output))
        captured_dev: list[str] = []

        def fake_create(**kwargs):
            dev = next((m for m in kwargs["messages"] if m["role"] == "developer"), None)
            if dev:
                captured_dev.append(dev["content"])
            return mock_response

        # _load_prompt_optional returns "[phase2]" when the file is "present",
        # or "" (absent) to force fallback to the legacy tailor prompt.
        def fake_load_optional(name, **kw):
            if name == "phase2":
                return "[phase2]" if phase2_present else ""
            return ""

        with (
            patch("tailor.llm.get_client") as mock_get_client,
            patch("tailor.llm._load_prompt", side_effect=lambda name, **kw: f"[{name}]"),
            patch("tailor.llm._load_prompt_optional", side_effect=fake_load_optional),
            patch("tailor.llm._load_candidate_profile", return_value=""),
        ):
            mock_get_client.return_value.chat.completions.create.side_effect = fake_create
            job = JobData(company="Acme", job_title="SE", description="desc")
            _, _, meta = tailor_documents_with_plan(plan, job, "resume", "cover")

        return captured_dev, meta

    def test_phase2_selected_when_file_present(self):
        """phase2 is used (and recorded in meta) when the file exists."""
        captured_dev, meta = self._run_with_phase2_prompt(phase2_present=True)

        assert meta.get("phase2_prompt_name") == "phase2", meta
        assert captured_dev, "No developer message captured"
        assert "[phase2]" in captured_dev[0], (
            f"Expected '[phase2]' in developer instructions, got: {captured_dev[0][:200]}"
        )

    def test_tailor_fallback_when_phase2_file_absent(self):
        """When phase2.txt is absent, falls back to tailor and records that in meta."""
        captured_dev, meta = self._run_with_phase2_prompt(phase2_present=False)

        assert meta.get("phase2_prompt_name") == "tailor", meta
        assert captured_dev, "No developer message captured"
        assert "[tailor]" in captured_dev[0], (
            f"Expected '[tailor]' in developer instructions, got: {captured_dev[0][:200]}"
        )

    def test_phase2_prompt_file_has_version_header(self):
        """The phase2.txt file on disk contains a recognised version header prefix."""
        from tailor.prompts import _load_prompt
        content = _load_prompt("phase2")
        valid_headers = ("[TAILOR_PHASE2_WRITER v", "[PHASE2_WRITER_LAYER v")
        assert any(h in content for h in valid_headers), (
            f"phase2.txt must contain one of {valid_headers} as a version header"
        )


# ---------------------------------------------------------------------------
# Phase 1 schema unification (spec 1.1–1.3)
# ---------------------------------------------------------------------------

class TestPhase1SchemaEnforcement:
    """Ensure validate_plan enforces schema fields added in the latest schema revision."""

    def test_vocabulary_anchoring_required(self):
        """Plans missing vocabulary_anchoring must fail validation."""
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        del plan["vocabulary_anchoring"]
        with pytest.raises(PlanValidationError, match="missing required keys"):
            validate_plan(plan)

    def test_resume_mode_required(self):
        """Plans missing resume_mode must fail validation."""
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        del plan["resume_mode"]
        with pytest.raises(PlanValidationError, match="missing required keys"):
            validate_plan(plan)

    def test_vocabulary_anchoring_wrong_type_raises(self):
        """vocabulary_anchoring must be a dict, not a list or string."""
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        plan["vocabulary_anchoring"] = ["event-driven", "microservices"]
        with pytest.raises(PlanValidationError, match="vocabulary_anchoring must be a JSON object"):
            validate_plan(plan)

    def test_vocabulary_anchoring_must_embed_wrong_type_raises(self):
        """vocabulary_anchoring.must_embed must be a list."""
        from tailor.llm import PlanValidationError, validate_plan
        plan = _minimal_plan()
        plan["vocabulary_anchoring"] = {"must_embed": "event-driven", "optional_embed": []}
        with pytest.raises(PlanValidationError, match="must_embed must be a list"):
            validate_plan(plan)

    def test_vocabulary_anchoring_empty_lists_passes(self):
        """vocabulary_anchoring with empty lists is valid."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        plan["vocabulary_anchoring"] = {"must_embed": [], "optional_embed": []}
        assert validate_plan(plan) is plan

    def test_repair_output_validates_same_as_fresh(self):
        """A 'repaired' plan dict passes validate_plan identically to a fresh one."""
        from tailor.llm import validate_plan
        plan = _minimal_plan()
        # Simulate repair: validate returns the same dict — no repair-specific branching
        result = validate_plan(plan)
        assert result is plan
        assert result["vocabulary_anchoring"]["must_embed"] == ["distributed systems", "scalability"]


# ---------------------------------------------------------------------------
# Writer packet vocab propagation (spec 2.1–2.2)
# ---------------------------------------------------------------------------

_SAMPLE_MASTER_RESUME_FOR_VOCAB = (
    "Professional Summary\n"
    "Experienced engineer.\n\n"
    "Experience\n"
    "Senior Engineer | Acme Corp | 2020 - Present\n"
    "- Built scalable distributed systems.\n\n"
    "Technical Skills\n"
    "Python, Java, AWS\n"
)


class TestWriterPacketVocabPropagation:
    """Writer packet must contain jd_vocab_must_embed and jd_vocab_optional_embed."""

    def test_vocab_fields_propagated_correctly(self):
        """Vocab anchors from plan flow into writer packet unchanged."""
        from tailor.writer_packet import build_writer_packet

        plan = _minimal_plan()
        plan["vocabulary_anchoring"] = {
            "must_embed": ["event-driven architecture", "microservices"],
            "optional_embed": ["cloud-native", "observability"],
        }
        packet = build_writer_packet(plan, "", _SAMPLE_MASTER_RESUME_FOR_VOCAB, "")

        assert packet["jd_vocab_must_embed"] == ["event-driven architecture", "microservices"]
        assert packet["jd_vocab_optional_embed"] == ["cloud-native", "observability"]

    def test_vocab_fields_always_present_when_empty(self):
        """Both vocab fields are always in the packet even when the lists are empty."""
        from tailor.writer_packet import build_writer_packet

        plan = _minimal_plan()
        plan["vocabulary_anchoring"] = {"must_embed": [], "optional_embed": []}
        packet = build_writer_packet(plan, "", _SAMPLE_MASTER_RESUME_FOR_VOCAB, "")

        assert "jd_vocab_must_embed" in packet
        assert "jd_vocab_optional_embed" in packet
        assert packet["jd_vocab_must_embed"] == []
        assert packet["jd_vocab_optional_embed"] == []

    def test_vocab_fields_default_to_empty_when_anchoring_missing(self):
        """When vocabulary_anchoring is absent from plan, packet fields default to []."""
        from tailor.writer_packet import build_writer_packet

        plan = _minimal_plan()
        del plan["vocabulary_anchoring"]
        packet = build_writer_packet(plan, "", _SAMPLE_MASTER_RESUME_FOR_VOCAB, "")

        assert packet["jd_vocab_must_embed"] == []
        assert packet["jd_vocab_optional_embed"] == []

    def test_ordering_preserved(self):
        """Vocab anchor order must be preserved as provided by Phase 1."""
        from tailor.writer_packet import build_writer_packet

        plan = _minimal_plan()
        anchors = ["alpha", "beta", "gamma", "delta"]
        plan["vocabulary_anchoring"] = {"must_embed": anchors, "optional_embed": []}
        packet = build_writer_packet(plan, "", _SAMPLE_MASTER_RESUME_FOR_VOCAB, "")

        assert packet["jd_vocab_must_embed"] == anchors

    def test_phase2_prompt_references_writer_packet_anchors(self):
        """The phase2.txt prompt must reference WRITER_PACKET for vocab anchors."""
        from tailor.prompts import _load_prompt
        content = _load_prompt("phase2")
        assert "jd_vocab_must_embed" in content, (
            "phase2.txt must reference WRITER_PACKET.jd_vocab_must_embed"
        )
        assert "jd_vocab_optional_embed" in content, (
            "phase2.txt must reference WRITER_PACKET.jd_vocab_optional_embed"
        )
