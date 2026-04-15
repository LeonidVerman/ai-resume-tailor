"""
backend/tests/api/test_candidate_profile_autofill_api.py

API tests for /api/v1/candidate-profile/autofill/* endpoints.
LLM calls are mocked — no real OpenAI requests.
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.db.models.structured_resume import StructuredResume

BASE = "/api/v1/candidate-profile"
AUTOFILL = f"{BASE}/autofill"

_RESUME_JSONB = {
    "name": "Jane Smith",
    "raw_text": "Jane Smith\nSoftware Engineer\n\nSkills: Python, Java",
}

_MINIMAL_DRAFT = {
    "candidate_profile_version": "2.0",
    "candidate": {"name": "Jane Smith", "headline": "Software engineer", "summary": None},
    "domains": {"primary": [], "secondary": []},
    "experience_highlights": [],
    "technical_skills": {
        "languages": [], "backend_systems": [], "datastores": [], "infra_devops": [],
        "frontend": [], "api_patterns": [], "async_messaging": [], "observability": [],
        "security_auth_patterns": [], "scalability_reliability_patterns": [],
    },
    "leadership": {"scope": {"team_size_max": None, "style_keywords": []}, "practices": [], "risk_management": []},
    "ai_tooling_practice": {"hands_on_tools": [], "usage_patterns": [], "principles": [], "concepts_familiarity": []},
    "role_fit_themes": [],
    "constraints_and_preferences": {"work_context": [], "communication": [], "resume_constraint": []},
    "claim_boundaries": {"security_auth": [], "domain_limits": [], "employment_constraints": []},
}

_PROFILE_BODY = {
    "profile": {"candidate": {"name": "Jane Smith"}},
    "profile_version": "1",
}


def _make_resume(db, user) -> StructuredResume:
    r = StructuredResume(
        user_id=user.id,
        resume_jsonb=_RESUME_JSONB,
        source_file_url=None,
    )
    db.add(r)
    db.flush()
    return r


class TestListAutofillResumes:
    def test_returns_empty_list_when_no_resumes(self, client):
        resp = client.get(f"{AUTOFILL}/resumes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_user_resumes(self, client, db, user):
        _make_resume(db, user)
        resp = client.get(f"{AUTOFILL}/resumes")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Jane Smith"


class TestGetAutofillDraft:
    def test_returns_404_when_no_draft(self, client, db, user):
        resume = _make_resume(db, user)
        resp = client.get(f"{AUTOFILL}/draft?resume_id={resume.id}")
        assert resp.status_code == 404

    def test_returns_404_when_resume_not_found(self, client):
        resp = client.get(f"{AUTOFILL}/draft?resume_id=99999")
        assert resp.status_code == 404


class TestGenerateAutofillDraft:
    def _mock_llm_result(self):
        result = MagicMock()
        result.content = json.dumps(_MINIMAL_DRAFT)
        result.model = "gpt-4o-2026-01-01"
        return result

    def test_generate_returns_draft(self, client, db, user):
        resume = _make_resume(db, user)
        mock_client = MagicMock()
        mock_client.complete_json.return_value = self._mock_llm_result()

        with patch(
            "backend.app.services.profile_autofill_service.make_openai_client_from_settings",
            return_value=mock_client,
        ):
            resp = client.post(f"{AUTOFILL}/generate", json={"resume_id": resume.id})

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ready"
        assert data["is_stale"] is False
        assert data["resume_id"] == resume.id
        assert data["draft"]["candidate"]["name"] == "Jane Smith"

    def test_generate_404_for_unknown_resume(self, client):
        resp = client.post(f"{AUTOFILL}/generate", json={"resume_id": 99999})
        assert resp.status_code == 404

    def test_generate_second_call_returns_updated_draft(self, client, db, user):
        """Calling generate twice for the same resume upserts (no unique error)."""
        resume = _make_resume(db, user)
        mock_client = MagicMock()
        mock_client.complete_json.return_value = self._mock_llm_result()

        with patch(
            "backend.app.services.profile_autofill_service.make_openai_client_from_settings",
            return_value=mock_client,
        ):
            resp1 = client.post(f"{AUTOFILL}/generate", json={"resume_id": resume.id})
            resp2 = client.post(f"{AUTOFILL}/generate", json={"resume_id": resume.id})

        assert resp1.status_code == 200
        assert resp2.status_code == 200

    def test_get_draft_returns_not_stale_after_generate(self, client, db, user):
        resume = _make_resume(db, user)
        mock_client = MagicMock()
        mock_client.complete_json.return_value = self._mock_llm_result()

        with patch(
            "backend.app.services.profile_autofill_service.make_openai_client_from_settings",
            return_value=mock_client,
        ):
            client.post(f"{AUTOFILL}/generate", json={"resume_id": resume.id})

        resp = client.get(f"{AUTOFILL}/draft?resume_id={resume.id}")
        assert resp.status_code == 200
        assert resp.json()["is_stale"] is False


class TestSelectSourceResume:
    def test_select_source_persists_resume_id(self, client, db, user):
        client.post(BASE, json=_PROFILE_BODY)
        resume = _make_resume(db, user)

        resp = client.post(f"{AUTOFILL}/select-source", json={"resume_id": resume.id})
        assert resp.status_code == 200
        assert resp.json()["source_resume_id"] == resume.id

    def test_select_source_reflected_in_profile_response(self, client, db, user):
        client.post(BASE, json=_PROFILE_BODY)
        resume = _make_resume(db, user)

        client.post(f"{AUTOFILL}/select-source", json={"resume_id": resume.id})
        profile_resp = client.get(BASE)
        assert profile_resp.status_code == 200
        assert profile_resp.json()["source_resume_id"] == resume.id

    def test_select_source_404_when_no_profile(self, client, db, user):
        resume = _make_resume(db, user)
        resp = client.post(f"{AUTOFILL}/select-source", json={"resume_id": resume.id})
        assert resp.status_code == 404
