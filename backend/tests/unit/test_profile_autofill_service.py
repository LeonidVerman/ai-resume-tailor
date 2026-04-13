"""
backend/tests/unit/test_profile_autofill_service.py

Unit tests for ProfileAutofillService.
All DB and LLM interactions are mocked — no real DB or OpenAI calls.
"""

import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.services.profile_autofill_service import ProfileAutofillService


# ── Helpers ────────────────────────────────────────────────────────────────────

_USER_ID = "user-aaa"
_RESUME_ID = 42

_MINIMAL_DRAFT = {
    "candidate_profile_version": "2.0",
    "candidate": {"name": "Jane Smith", "headline": "Software engineer", "summary": "Summary."},
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

_RESUME_JSONB = {"raw_text": "Jane Smith\nSoftware Engineer\n\nSkills: Python, Java"}


def _hash(jsonb: dict) -> str:
    return hashlib.sha256(json.dumps(jsonb, sort_keys=True).encode()).hexdigest()


def _mock_resume(user_id=_USER_ID, jsonb=None):
    r = MagicMock()
    r.id = _RESUME_ID
    r.user_id = user_id
    r.resume_jsonb = jsonb or _RESUME_JSONB
    return r


def _mock_draft_row(draft_dict=None, resume_hash=None):
    row = MagicMock()
    row.id = 1
    row.draft_jsonb = draft_dict or _MINIMAL_DRAFT
    row.resume_hash = resume_hash or _hash(_RESUME_JSONB)
    row.status = "ready"
    row.model = "gpt-4o-2026-01-01"
    row.generated_at = datetime(2026, 4, 1, tzinfo=timezone.utc)
    row.error_message = None
    return row


# ── get_draft ──────────────────────────────────────────────────────────────────

class TestGetDraft:
    def test_returns_none_when_no_row(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume()
        mock_draft_repo = MagicMock()
        mock_draft_repo.get_by_user_and_resume.return_value = None

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            patch("backend.app.services.profile_autofill_service.CandidateProfileResumeDraftRepository", return_value=mock_draft_repo),
        ):
            result = svc.get_draft(_USER_ID, _RESUME_ID)

        assert result is None

    def test_returns_draft_not_stale_when_hash_matches(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        current_hash = _hash(_RESUME_JSONB)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume()
        mock_draft_repo = MagicMock()
        mock_draft_repo.get_by_user_and_resume.return_value = _mock_draft_row(resume_hash=current_hash)

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            patch("backend.app.services.profile_autofill_service.CandidateProfileResumeDraftRepository", return_value=mock_draft_repo),
        ):
            result = svc.get_draft(_USER_ID, _RESUME_ID)

        assert result is not None
        assert result.is_stale is False
        assert result.resume_id == _RESUME_ID

    def test_returns_draft_stale_when_hash_changed(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        old_hash = _hash({"raw_text": "old resume text"})
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume()  # current hash = hash(_RESUME_JSONB)
        mock_draft_repo = MagicMock()
        mock_draft_repo.get_by_user_and_resume.return_value = _mock_draft_row(resume_hash=old_hash)

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            patch("backend.app.services.profile_autofill_service.CandidateProfileResumeDraftRepository", return_value=mock_draft_repo),
        ):
            result = svc.get_draft(_USER_ID, _RESUME_ID)

        assert result is not None
        assert result.is_stale is True

    def test_raises_404_when_resume_not_found(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = None

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.get_draft(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 404

    def test_raises_404_when_resume_belongs_to_other_user(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume(user_id="other-user")

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.get_draft(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 404


# ── generate ───────────────────────────────────────────────────────────────────

class TestGenerate:
    def _run_generate(self, resume_jsonb=None, llm_content=None):
        db = MagicMock()
        svc = ProfileAutofillService(db)

        jsonb = resume_jsonb or _RESUME_JSONB
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume(jsonb=jsonb)

        draft_row = _mock_draft_row()
        mock_draft_repo = MagicMock()
        mock_draft_repo.upsert.return_value = draft_row

        llm_result = MagicMock()
        llm_result.content = json.dumps(llm_content or _MINIMAL_DRAFT)
        llm_result.model = "gpt-4o-2026-01-01"

        mock_client = MagicMock()
        mock_client.complete_json.return_value = llm_result

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            patch("backend.app.services.profile_autofill_service.CandidateProfileResumeDraftRepository", return_value=mock_draft_repo),
            patch("backend.app.services.profile_autofill_service.make_openai_client_from_settings", return_value=mock_client),
        ):
            result = svc.generate(_USER_ID, _RESUME_ID)

        return result, mock_client, mock_draft_repo, db

    def test_calls_llm_and_persists_draft(self):
        result, mock_client, mock_draft_repo, db = self._run_generate()

        mock_client.complete_json.assert_called_once()
        mock_draft_repo.upsert.assert_called_once()
        db.commit.assert_called_once()
        assert result.status == "ready"
        assert result.is_stale is False
        assert result.resume_id == _RESUME_ID

    def test_returns_correct_draft_content(self):
        result, _, _, _ = self._run_generate()
        assert result.draft.candidate.name == "Jane Smith"

    def test_raises_404_when_resume_not_found(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = None

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.generate(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 404

    def test_raises_404_when_resume_belongs_to_other_user(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume(user_id="other-user")

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.generate(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 404

    def test_raises_422_when_resume_has_no_text(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume(jsonb={"raw_text": "   "})

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.generate(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 422

    def test_raises_422_when_resume_has_missing_raw_text_key(self):
        db = MagicMock()
        svc = ProfileAutofillService(db)
        mock_resume_repo = MagicMock()
        mock_resume_repo.get_by_id.return_value = _mock_resume(jsonb={"name": "Jane"})

        with (
            patch("backend.app.services.profile_autofill_service.StructuredResumeRepository", return_value=mock_resume_repo),
            pytest.raises(HTTPException) as exc_info,
        ):
            svc.generate(_USER_ID, _RESUME_ID)

        assert exc_info.value.status_code == 422
