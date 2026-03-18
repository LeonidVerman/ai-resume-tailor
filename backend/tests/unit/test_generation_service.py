"""
backend/tests/unit/test_generation_service.py

Unit tests for GenerationService — the _run_pipeline method is mocked
so no LLM calls are made.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from backend.app.schemas.generation import GenerationRequest
from backend.app.services.generation_service import GenerationService, _extract_usage_from_messages


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_jd(user_id: str, jd_id: str | None = None):
    jd = MagicMock()
    jd.id = jd_id or str(uuid.uuid4())
    jd.user_id = user_id
    jd.raw_text = "Looking for a senior engineer."
    jd.metadata_jsonb = {"company": "Acme", "job_title": "Engineer"}
    return jd


def _make_resume(user_id: str, resume_id: str | None = None):
    res = MagicMock()
    res.id = resume_id or str(uuid.uuid4())
    res.user_id = user_id
    res.resume_jsonb = {"raw_text": "My resume text", "name": "Alice"}
    return res


def _make_run(run_id: str | None = None):
    run = MagicMock()
    run.id = run_id or str(uuid.uuid4())
    return run


def _make_doc(doc_id: str | None = None):
    doc = MagicMock()
    doc.id = doc_id or str(uuid.uuid4())
    return doc


def _make_tailor_result(resume: str = "Tailored resume", cover: str = "Cover letter"):
    result = MagicMock()
    result.resume = resume
    result.cover_letter = cover
    return result


def _make_service(user_id: str, jd=None, resume=None, run=None, doc=None):
    """Build a GenerationService with all repos mocked."""
    jd = jd or _make_jd(user_id)
    resume = resume or _make_resume(user_id)
    run = run or _make_run()
    doc = doc or _make_doc()

    jd_repo = MagicMock()
    jd_repo.get_by_id.return_value = jd

    resume_repo = MagicMock()
    resume_repo.get_by_id.return_value = resume

    run_repo = MagicMock()
    run_repo.create.return_value = run

    doc_repo = MagicMock()
    doc_repo.create.return_value = doc

    profile_repo = MagicMock()
    profile_repo.get_by_user_id.return_value = None  # no profile → falls back to file

    storage_service = MagicMock()

    svc = GenerationService(
        run_repo=run_repo,
        doc_repo=doc_repo,
        jd_repo=jd_repo,
        resume_repo=resume_repo,
        profile_repo=profile_repo,
        storage_service=storage_service,
    )
    return svc, jd, resume, run, doc, run_repo, doc_repo


# ── Tests ──────────────────────────────────────────────────────────────────


class TestGenerationServiceGenerate:
    def test_success_returns_response(self):
        user_id = str(uuid.uuid4())
        svc, jd, resume, run, doc, run_repo, doc_repo = _make_service(user_id)
        tailor_result = _make_tailor_result()

        request = GenerationRequest(
            job_description_id=jd.id,
            structured_resume_id=resume.id,
        )

        with (
            patch("backend.app.services.generation_service.GenerationService._run_pipeline",
                  return_value=(tailor_result, 100, 200, 0.005, {})),
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
        ):
            resp = svc.generate(user_id, request)

        assert resp.run_id == run.id
        assert resp.status == "succeeded"
        assert resp.tailored_document_id == doc.id

    def test_jd_not_found_raises_404(self):
        user_id = str(uuid.uuid4())
        svc, jd, resume, run, doc, run_repo, doc_repo = _make_service(user_id)
        svc._jd_repo.get_by_id.return_value = None

        request = GenerationRequest(
            job_description_id=str(uuid.uuid4()),
            structured_resume_id=resume.id,
        )

        with (
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
            
        ):
            with pytest.raises(HTTPException) as exc_info:
                svc.generate(user_id, request)
        assert exc_info.value.status_code == 404

    def test_resume_belongs_to_other_user_raises_404(self):
        user_id = str(uuid.uuid4())
        other_user = str(uuid.uuid4())
        svc, jd, resume, run, doc, run_repo, doc_repo = _make_service(user_id)
        resume.user_id = other_user  # belongs to someone else

        request = GenerationRequest(
            job_description_id=jd.id,
            structured_resume_id=resume.id,
        )

        with (
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
            
        ):
            with pytest.raises(HTTPException) as exc_info:
                svc.generate(user_id, request)
        assert exc_info.value.status_code == 404

    def test_pipeline_failure_updates_run_to_failed(self):
        user_id = str(uuid.uuid4())
        svc, jd, resume, run, doc, run_repo, doc_repo = _make_service(user_id)

        request = GenerationRequest(
            job_description_id=jd.id,
            structured_resume_id=resume.id,
        )

        with (
            patch("backend.app.services.generation_service.GenerationService._run_pipeline",
                  side_effect=RuntimeError("LLM error")),
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
            
        ):
            with pytest.raises(RuntimeError):
                svc.generate(user_id, request)

        # run_repo.update called with status=failed
        run_repo.update.assert_called_once()
        _, kwargs = run_repo.update.call_args
        assert kwargs.get("status") == "failed" or run_repo.update.call_args[0][1] == "failed" \
               or any("failed" in str(a) for a in run_repo.update.call_args.args + tuple(run_repo.update.call_args.kwargs.values()))


    def test_jd_metadata_injected_into_pipeline(self):
        """company and job_title from JD metadata must be forwarded to _run_pipeline."""
        user_id = str(uuid.uuid4())
        jd = _make_jd(user_id)
        jd.metadata_jsonb = {"company": "WidgetCo", "job_title": "Principal Engineer"}
        svc, _, resume, run, doc, run_repo, doc_repo = _make_service(user_id, jd=jd)
        tailor_result = _make_tailor_result()

        request = GenerationRequest(
            job_description_id=jd.id,
            structured_resume_id=resume.id,
        )

        with (
            patch(
                "backend.app.services.generation_service.GenerationService._run_pipeline",
                return_value=(tailor_result, 10, 20, None, {}),
            ) as mock_pipeline,
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
        ):
            svc.generate(user_id, request)

        mock_pipeline.assert_called_once()
        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("jd_company") == "WidgetCo"
        assert kwargs.get("jd_job_title") == "Principal Engineer"

    def test_jd_metadata_empty_when_not_set(self):
        """When metadata_jsonb is None, company and job_title default to empty strings."""
        user_id = str(uuid.uuid4())
        jd = _make_jd(user_id)
        jd.metadata_jsonb = None
        svc, _, resume, run, doc, run_repo, doc_repo = _make_service(user_id, jd=jd)
        tailor_result = _make_tailor_result()

        request = GenerationRequest(
            job_description_id=jd.id,
            structured_resume_id=resume.id,
        )

        with (
            patch(
                "backend.app.services.generation_service.GenerationService._run_pipeline",
                return_value=(tailor_result, 10, 20, None, {}),
            ) as mock_pipeline,
            patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini"),
        ):
            svc.generate(user_id, request)

        _, kwargs = mock_pipeline.call_args
        assert kwargs.get("jd_company") == ""
        assert kwargs.get("jd_job_title") == ""


class TestExtractUsageFromMessages:
    def test_extracts_usage(self):
        msg = MagicMock()
        msg.usage.prompt_tokens = 100
        msg.usage.completion_tokens = 50
        inp, out, cost = _extract_usage_from_messages([msg])
        assert inp == 100
        assert out == 50
        assert cost is None

    def test_multiple_messages_summed(self):
        def _msg(inp, out):
            m = MagicMock()
            m.usage.prompt_tokens = inp
            m.usage.completion_tokens = out
            return m

        inp, out, _ = _extract_usage_from_messages([_msg(100, 50), _msg(200, 75)])
        assert inp == 300
        assert out == 125

    def test_no_usage_returns_none(self):
        msg = MagicMock()
        msg.usage = None
        inp, out, _ = _extract_usage_from_messages([msg])
        assert inp is None
        assert out is None

    def test_empty_messages(self):
        inp, out, cost = _extract_usage_from_messages([])
        assert inp is None
