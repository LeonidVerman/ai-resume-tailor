"""
backend/tests/unit/test_candidate_meta_service.py

Unit tests for candidate_meta_service.ensure_candidate_prompt_synced().

All LLM calls are mocked — no network access required.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.candidate_meta_service import ensure_candidate_prompt_synced


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_profile(
    synched: bool = False,
    candidate_prompt: str | None = None,
    profile_jsonb: dict | None = None,
) -> MagicMock:
    p = MagicMock()
    p.id = str(uuid.uuid4())
    p.prompt_synched = synched
    p.candidate_prompt = candidate_prompt
    p.profile_jsonb = profile_jsonb or {"candidate": {"name": "Alice"}}
    return p


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.update.return_value = MagicMock()
    return repo


# ── Prompt reuse (synced) ──────────────────────────────────────────────────


class TestPromptAlreadySynced:
    def test_returns_stored_prompt_without_llm_call(self):
        profile = _make_profile(synched=True, candidate_prompt="[CANDIDATE_LAYER v1.0]\nCore anchors...")
        repo = _make_repo()

        with patch("backend.app.services.candidate_meta_service._call_meta_llm") as mock_llm:
            result = ensure_candidate_prompt_synced(profile, "gpt-4o", repo)

        assert result == "[CANDIDATE_LAYER v1.0]\nCore anchors..."
        mock_llm.assert_not_called()
        repo.update.assert_not_called()

    def test_synced_but_empty_prompt_triggers_generation(self):
        """prompt_synched=True but no stored text still regenerates."""
        profile = _make_profile(synched=True, candidate_prompt=None)
        repo = _make_repo()
        generated = "[CANDIDATE_LAYER v1.0]\nGenerated."

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch("backend.app.services.candidate_meta_service._call_meta_llm", return_value=generated),
        ):
            result = ensure_candidate_prompt_synced(profile, "gpt-4o", repo)

        assert result == generated
        repo.update.assert_called_once_with(profile, candidate_prompt=generated, prompt_synched=True)


# ── Lazy generation (not synced) ──────────────────────────────────────────


class TestPromptNotSynced:
    def test_generates_prompt_and_stores_it(self):
        profile = _make_profile(synched=False, candidate_prompt=None)
        repo = _make_repo()
        generated = "[CANDIDATE_LAYER v1.0]\nNew prompt."

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch("backend.app.services.candidate_meta_service._call_meta_llm", return_value=generated),
        ):
            result = ensure_candidate_prompt_synced(profile, "gpt-4o", repo)

        assert result == generated
        repo.update.assert_called_once_with(profile, candidate_prompt=generated, prompt_synched=True)

    def test_replaces_stale_prompt_on_success(self):
        """Stale prompt (synched=False) is replaced after successful generation."""
        profile = _make_profile(synched=False, candidate_prompt="[OLD]\nStale prompt.")
        repo = _make_repo()
        fresh = "[CANDIDATE_LAYER v1.0]\nFresh prompt."

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch("backend.app.services.candidate_meta_service._call_meta_llm", return_value=fresh),
        ):
            result = ensure_candidate_prompt_synced(profile, "gpt-4o", repo)

        assert result == fresh
        repo.update.assert_called_once_with(profile, candidate_prompt=fresh, prompt_synched=True)

    def test_model_forwarded_to_llm(self):
        profile = _make_profile(synched=False)
        repo = _make_repo()

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch(
                "backend.app.services.candidate_meta_service._call_meta_llm",
                return_value="[CANDIDATE_LAYER v1.0]",
            ) as mock_llm,
        ):
            ensure_candidate_prompt_synced(profile, "gpt-4.1", repo)

        args, _ = mock_llm.call_args
        assert args[2] == "gpt-4.1"  # model is the third positional arg


# ── Failure handling ───────────────────────────────────────────────────────


class TestGenerationFailure:
    def test_llm_failure_propagates_without_persisting(self):
        profile = _make_profile(synched=False, candidate_prompt="[OLD]\nPrevious.")
        repo = _make_repo()

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch(
                "backend.app.services.candidate_meta_service._call_meta_llm",
                side_effect=RuntimeError("LLM timeout"),
            ),
            pytest.raises(RuntimeError, match="LLM timeout"),
        ):
            ensure_candidate_prompt_synced(profile, "gpt-4o", repo)

        # prompt_synched must NOT be set to True; repo.update must NOT be called
        repo.update.assert_not_called()

    def test_old_prompt_preserved_on_failure(self):
        """The model object is not mutated when generation fails."""
        profile = _make_profile(synched=False, candidate_prompt="[OLD]\nKept.")

        with (
            patch("backend.app.services.candidate_meta_service._load_meta_prompt", return_value="meta"),
            patch(
                "backend.app.services.candidate_meta_service._call_meta_llm",
                side_effect=RuntimeError("network error"),
            ),
            pytest.raises(RuntimeError),
        ):
            ensure_candidate_prompt_synced(profile, "gpt-4o", MagicMock())

        # The in-memory profile object retains old values
        assert profile.candidate_prompt == "[OLD]\nKept."
        assert profile.prompt_synched is False


# ── Generation service integration ────────────────────────────────────────


class TestEnsureCandidatePromptHelper:
    """Tests for generation_service._ensure_candidate_prompt()."""

    def test_returns_none_when_no_profile(self):
        from backend.app.services.generation_service import _ensure_candidate_prompt
        result = _ensure_candidate_prompt(None, "gpt-4o", MagicMock())
        assert result is None

    def test_returns_none_when_profile_has_no_jsonb(self):
        from backend.app.services.generation_service import _ensure_candidate_prompt
        profile = MagicMock()
        profile.profile_jsonb = None
        result = _ensure_candidate_prompt(profile, "gpt-4o", MagicMock())
        assert result is None

    def test_delegates_to_meta_service(self):
        from backend.app.services.generation_service import _ensure_candidate_prompt
        profile = _make_profile(synched=True, candidate_prompt="Cached prompt.")

        with patch(
            "backend.app.services.generation_service.ensure_candidate_prompt_synced"
            if False  # triggers the import-time patch path
            else "backend.app.services.candidate_meta_service.ensure_candidate_prompt_synced",
            return_value="Cached prompt.",
        ):
            # Call through the helper which internally imports the service
            with patch(
                "backend.app.services.generation_service._ensure_candidate_prompt",
                wraps=_ensure_candidate_prompt,
            ):
                result = _ensure_candidate_prompt(profile, "gpt-4o", MagicMock())

        # The helper resolves to the meta-service result for a synced profile
        assert result == "Cached prompt."

    def test_uses_candidate_prompt_not_raw_json_in_generation(self):
        """
        GenerationService.generate() must forward:
          - candidate_layer  → the generated candidate_prompt text
          - candidate_profile_text → the raw profile JSON (for writer_packet / Phase 1)
        These must be passed as SEPARATE arguments to _run_pipeline, not merged.
        """
        import json
        import uuid
        from unittest.mock import patch
        from backend.app.schemas.generation import GenerationRequest
        from backend.app.services.generation_service import GenerationService

        user_id = str(uuid.uuid4())
        jd = MagicMock()
        jd.id = 1
        jd.user_id = user_id
        jd.raw_text = "JD text"
        jd.metadata_jsonb = {"company": "Acme", "job_title": "SWE"}

        resume = MagicMock()
        resume.id = 1
        resume.user_id = user_id
        resume.resume_jsonb = {"raw_text": "Resume"}

        run = MagicMock()
        run.id = str(uuid.uuid4())
        doc = MagicMock()
        doc.id = str(uuid.uuid4())

        raw_profile_jsonb = {"candidate": {"name": "Alice"}, "domains": {"primary": ["fintech"]}}
        # Profile with synced prompt
        profile = _make_profile(
            synched=True,
            candidate_prompt="[CANDIDATE_LAYER v1.0]\nMy prompt.",
            profile_jsonb=raw_profile_jsonb,
        )

        jd_repo = MagicMock(); jd_repo.get_by_id.return_value = jd
        resume_repo = MagicMock(); resume_repo.get_by_id.return_value = resume
        run_repo = MagicMock(); run_repo.create.return_value = run
        doc_repo = MagicMock(); doc_repo.create.return_value = doc
        profile_repo = MagicMock(); profile_repo.get_by_user_id.return_value = profile

        svc = GenerationService(
            run_repo=run_repo,
            doc_repo=doc_repo,
            jd_repo=jd_repo,
            resume_repo=resume_repo,
            profile_repo=profile_repo,
            storage_service=MagicMock(),
        )

        tailor_result = MagicMock()
        tailor_result.resume = "Tailored resume"
        tailor_result.cover_letter = "Cover"

        request = GenerationRequest(job_description_id=jd.id, structured_resume_id=resume.id)

        with (
            patch(
                "backend.app.services.generation_service.GenerationService._run_pipeline",
                return_value=(tailor_result, 10, 20, None, {}),
            ) as mock_pipeline,
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
        ):
            svc.generate(user_id, request)

        _, kwargs = mock_pipeline.call_args
        # candidate_layer must be the generated prompt text (for developer_instructions)
        assert kwargs.get("candidate_layer") == "[CANDIDATE_LAYER v1.0]\nMy prompt."
        # candidate_profile_text must be the raw profile JSON (for writer_packet / Phase 1)
        assert kwargs.get("candidate_profile_text") == json.dumps(raw_profile_jsonb, ensure_ascii=False, indent=2)


# ── Save flow ──────────────────────────────────────────────────────────────


class TestSaveFlowSetsPromptUnsynced:
    """CandidateProfileService must mark prompt_synched=False on create/update."""

    def test_create_sets_prompt_synched_false(self):
        from backend.app.schemas.candidate_profile import (
            CandidateIdentity,
            CandidateProfileDocument,
            CandidateProfileUpsertRequest,
        )
        from backend.app.services.candidate_profile_service import CandidateProfileService

        repo = MagicMock()
        stored = MagicMock()
        stored.id = str(uuid.uuid4())
        stored.user_id = "u1"
        stored.profile_version = "1"
        stored.profile_jsonb = {"candidate": {"name": "Alice"}}
        stored.candidate_prompt = None
        stored.prompt_synched = False
        stored.created_at = stored.updated_at = MagicMock()
        repo.create.return_value = stored

        svc = CandidateProfileService(repo)
        req = CandidateProfileUpsertRequest(
            profile=CandidateProfileDocument(candidate=CandidateIdentity(name="Alice")),
            profile_version="1",
        )
        svc.create("u1", req)

        _, kwargs = repo.create.call_args
        assert kwargs.get("prompt_synched") is False

    def test_update_sets_prompt_synched_false(self):
        from backend.app.schemas.candidate_profile import (
            CandidateIdentity,
            CandidateProfileDocument,
            CandidateProfileUpsertRequest,
        )
        from backend.app.services.candidate_profile_service import CandidateProfileService

        existing = MagicMock()
        existing.profile_jsonb = {"candidate": {"name": "Old"}}
        existing.candidate_prompt = "[OLD]"
        existing.prompt_synched = True  # was synced before edit

        updated = MagicMock()
        updated.id = str(uuid.uuid4())
        updated.user_id = "u1"
        updated.profile_version = "1"
        updated.profile_jsonb = {"candidate": {"name": "Alice Updated"}}
        updated.candidate_prompt = "[OLD]"  # kept
        updated.prompt_synched = False
        updated.created_at = updated.updated_at = MagicMock()

        repo = MagicMock()
        repo.get_by_user_id.return_value = existing
        repo.update.return_value = updated

        svc = CandidateProfileService(repo)
        req = CandidateProfileUpsertRequest(
            profile=CandidateProfileDocument(candidate=CandidateIdentity(name="Alice Updated")),
            profile_version="1",
        )
        svc.update("u1", req)

        _, kwargs = repo.update.call_args
        assert kwargs.get("prompt_synched") is False
