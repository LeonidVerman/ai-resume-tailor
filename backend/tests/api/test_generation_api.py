"""
backend/tests/api/test_generation_api.py

API tests for POST /generations, GET /generations, GET /generations/{id}.
GenerationService._run_pipeline is mocked so no LLM calls occur.
"""

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

API = "/api/v1/generations"
RESUME_API = "/api/v1/resumes"
JD_API = "/api/v1/job-descriptions"

FAKE_RESUME = b"John Smith\njohn@example.com\nExperienced engineer."
JD_BODY = {"raw_text": "Senior software engineer needed with Python skills."}


def _make_tailor_result():
    r = MagicMock()
    r.resume = "Tailored resume text"
    r.cover_letter = "Cover letter text"
    return r


def _upload_resume(client):
    resp = client.post(
        f"{RESUME_API}/upload",
        files={"file": ("resume.txt", io.BytesIO(FAKE_RESUME), "text/plain")},
    )
    return resp.json()["id"]


def _create_jd(client):
    return client.post(f"{JD_API}/manual", json=JD_BODY).json()["id"]


def _gen_request(jd_id, resume_id: str, mode: str = "two_phase") -> dict:
    return {
        "job_description_id": jd_id,
        "structured_resume_id": resume_id,
        "options": {"mode": mode},
    }


def _complete_onboarding(client) -> None:
    """Create a minimal profile and mark onboarding complete."""
    client.put(
        "/api/v1/candidate-profile",
        json={"profile": {"candidate": {"name": "Test User"}}, "profile_version": "1"},
    )
    client.post("/api/v1/candidate-profile/complete-onboarding")


from contextlib import contextmanager
from unittest.mock import patch


@contextmanager
def _patch_pipeline():
    with (
        patch(
            "backend.app.services.generation_service.GenerationService._run_pipeline",
            return_value=(_make_tailor_result(), 100, 200, 0.005, {}),
        ),
        patch(
            "backend.app.services.generation_service._ensure_candidate_prompt",
            return_value="test candidate prompt",
        ),
        patch(
            "backend.app.services.usage_policy_service.UsagePolicyService.check_and_consume",
        ),
    ):
        yield


def _patch_config():
    return patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini")


class TestCreateGeneration:
    def test_success_returns_201(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            resp = client.post(API, json=_gen_request(jd_id, resume_id))

        assert resp.status_code == 201

    def test_response_has_run_id_and_doc_id(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            data = client.post(API, json=_gen_request(jd_id, resume_id)).json()

        assert "run_id" in data
        assert "tailored_document_id" in data
        assert data["status"] == "succeeded"

    def test_unknown_jd_returns_404(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)

        with _patch_config():
            resp = client.post(
                API,
                json=_gen_request(99999, resume_id),
            )

        assert resp.status_code == 404

    def test_unknown_resume_returns_404(self, client):
        _complete_onboarding(client)
        jd_id = _create_jd(client)

        with _patch_config():
            resp = client.post(
                API,
                json=_gen_request(jd_id, str(uuid.uuid4())),
            )

        assert resp.status_code == 404

    def test_missing_jd_id_returns_422(self, client):
        resume_id = _upload_resume(client)
        resp = client.post(API, json={"structured_resume_id": resume_id})
        assert resp.status_code == 422

    def test_pipeline_error_returns_500(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with (
            patch(
                "backend.app.services.generation_service.GenerationService._run_pipeline",
                side_effect=RuntimeError("LLM timeout"),
            ),
            patch(
                "backend.app.services.usage_policy_service.UsagePolicyService.check_and_consume",
            ),
            _patch_config(),
        ):
            resp = client.post(API, json=_gen_request(jd_id, resume_id))

        assert resp.status_code == 500


class TestListGenerations:
    def test_empty_list(self, client):
        resp = client.get(API)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_generation(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            client.post(API, json=_gen_request(jd_id, resume_id))

        resp = client.get(API)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_summary_fields(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            client.post(API, json=_gen_request(jd_id, resume_id))

        item = client.get(API).json()[0]
        assert "id" in item
        assert "status" in item
        assert "started_at" in item


class TestGetGeneration:
    def test_get_by_id_returns_200(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            run_id = client.post(API, json=_gen_request(jd_id, resume_id)).json()["run_id"]

        resp = client.get(f"{API}/{run_id}")
        assert resp.status_code == 200

    def test_get_detail_fields(self, client):
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with _patch_pipeline(), _patch_config():
            run_id = client.post(API, json=_gen_request(jd_id, resume_id)).json()["run_id"]

        data = client.get(f"{API}/{run_id}").json()
        assert data["id"] == run_id
        assert data["status"] == "succeeded"
        assert "user_id" in data

    def test_get_unknown_returns_404(self, client):
        resp = client.get(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 404


# ── Quota enforcement ──────────────────────────────────────────────────────

class TestGenerationQuota:
    """Verify that UsagePolicyService.check_and_consume is called and enforced."""

    def test_quota_exceeded_returns_429(self, client):
        """When check_and_consume raises 429, the generation endpoint propagates it."""
        from fastapi import HTTPException

        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        def _raise_quota(*args, **kwargs):
            raise HTTPException(
                status_code=429,
                detail={
                    "message": "Monthly generation limit reached (3/3).",
                    "error_code": "QUOTA_EXCEEDED",
                    "plan": "free",
                    "monthly_used": 3,
                    "monthly_limit": 3,
                    "extra_credits": 0,
                },
            )

        with (
            patch(
                "backend.app.services.usage_policy_service.UsagePolicyService.check_and_consume",
                side_effect=_raise_quota,
            ),
            _patch_config(),
        ):
            resp = client.post(API, json=_gen_request(jd_id, resume_id))

        assert resp.status_code == 429
        detail = resp.json()["detail"]
        assert detail["error_code"] == "QUOTA_EXCEEDED"
        assert detail["monthly_limit"] == 3
        assert detail["monthly_used"] == 3

    def test_quota_check_called_on_valid_request(self, client):
        """check_and_consume is called exactly once on a valid generation request."""
        _complete_onboarding(client)
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with (
            patch(
                "backend.app.services.generation_service.GenerationService._run_pipeline",
                return_value=(_make_tailor_result(), 100, 200, 0.005, {}),
            ),
            patch(
                "backend.app.services.generation_service._ensure_candidate_prompt",
                return_value="test candidate prompt",
            ),
            patch(
                "backend.app.services.usage_policy_service.UsagePolicyService.check_and_consume"
            ) as mock_check,
            _patch_config(),
        ):
            client.post(API, json=_gen_request(jd_id, resume_id))
            assert mock_check.call_count == 1

    def test_quota_check_not_called_before_onboarding(self, client):
        """Onboarding gate fires before quota check — 403, not 429."""
        resume_id = _upload_resume(client)
        jd_id = _create_jd(client)

        with (
            patch(
                "backend.app.services.usage_policy_service.UsagePolicyService.check_and_consume"
            ) as mock_check,
            _patch_config(),
        ):
            resp = client.post(API, json=_gen_request(jd_id, resume_id))
            assert resp.status_code == 403
            mock_check.assert_not_called()
