"""Regression tests for the two-phase tailoring pipeline.

These tests use mocked OpenAI responses so they run without API access.
"""

import json
import sys
import types
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers to build minimal valid fixtures
# ---------------------------------------------------------------------------

def _minimal_plan() -> dict:
    """Return a minimal valid TailoringPlan (v2.1)."""
    return {
        "role_level": "senior",
        "jd_top_themes": [
            {"theme": f"Theme {i}", "why_important": "important", "keywords": ["kw"]}
            for i in range(5)
        ],
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
        # developer message must be present
        assert messages[0]["role"] == "developer"

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
        assert result.resume == "resume"
        assert result.cover_letter == "cover"
        assert meta["usage"]["total_tokens"] == 300
