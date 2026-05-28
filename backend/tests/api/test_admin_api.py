"""
backend/tests/api/test_admin_api.py

API tests for admin endpoints.
"""

import io
import uuid
import zipfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.db.models.generation_run import GenerationRun
from backend.app.db.models.tailored_document import TailoredDocument
from backend.app.schemas.evaluation import EvaluationResponse, EvaluationScores

API_STATS = "/api/v1/admin/system-stats"
API_EVAL = "/api/v1/admin/evaluate-run"
API_RUN_DATA = "/api/v1/admin/run-data/download"


def _make_run(db, user, *, status="succeeded", started_at=None) -> GenerationRun:
    run = GenerationRun(
        user_id=user.id,
        run_type="single_pass",
        status=status,
        model_name="claude-3-5-sonnet",
        prompt_version="v1",
        started_at=started_at or datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    db.add(run)
    db.flush()
    return run


def _make_doc(db, run, user, *, resume_jsonb=None, cover_letter_jsonb=None,
              resume_docx_url=None, resume_pdf_url=None,
              cover_letter_docx_url=None, cover_letter_pdf_url=None) -> TailoredDocument:
    doc = TailoredDocument(
        user_id=user.id,
        generation_run_id=run.id,
        company_name="Acme Corp",
        role_title="Engineer",
        resume_jsonb=resume_jsonb if resume_jsonb is not None else {"text": "resume text", "candidate_name": "Jane Smith"},
        cover_letter_jsonb=cover_letter_jsonb if cover_letter_jsonb is not None else {"text": "cover letter text", "candidate_name": "Jane Smith"},
        resume_docx_url=resume_docx_url,
        resume_pdf_url=resume_pdf_url,
        cover_letter_docx_url=cover_letter_docx_url,
        cover_letter_pdf_url=cover_letter_pdf_url,
    )
    db.add(doc)
    db.flush()
    return doc


def _mock_storage(*, debug_bytes=b'{"generation_run_id":"1"}', file_bytes=b"fake-artifact") -> MagicMock:
    storage = MagicMock()
    storage.get_debug_json_bytes.return_value = debug_bytes
    storage.get_bytes.return_value = file_bytes
    return storage


def _fake_eval_response(run_id: int) -> EvaluationResponse:
    return EvaluationResponse(
        id=1,
        generation_run_id=run_id,
        scores=EvaluationScores(
            truthfulness_score=0.9,
            role_fit_score=0.85,
            clarity_score=0.8,
            seniority_score=0.75,
            integrated_score=0.825,
        ),
        created_at=datetime.now(tz=timezone.utc),
    )


class TestSystemStats:
    def test_stats_returns_200(self, admin_client):
        resp = admin_client.get(API_STATS)
        assert resp.status_code == 200

    def test_stats_schema(self, admin_client):
        data = admin_client.get(API_STATS).json()
        assert "total_users" in data
        assert "total_generation_runs" in data
        assert "total_succeeded_runs" in data
        assert "total_failed_runs" in data
        assert "total_tailored_documents" in data
        assert "total_evaluation_runs" in data

    def test_stats_counts_are_non_negative(self, admin_client):
        data = admin_client.get(API_STATS).json()
        for key, value in data.items():
            if isinstance(value, int):
                assert value >= 0, f"{key} should be non-negative"

    def test_stats_includes_version(self, admin_client):
        data = admin_client.get(API_STATS).json()
        assert "app_version" in data
        assert isinstance(data["app_version"], str)
        assert "build_date" in data
        assert isinstance(data["build_date"], str)

    def test_non_admin_returns_403(self, client):
        """Regular user client should be refused."""
        resp = client.get(API_STATS)
        assert resp.status_code == 403


class TestEvaluateRun:
    def test_evaluate_run_returns_201(self, admin_client):
        run_id = 42
        fake_resp = _fake_eval_response(run_id)

        with patch(
            "backend.app.api.admin.EvaluationService.evaluate",
            return_value=fake_resp,
        ):
            resp = admin_client.post(API_EVAL, json={"generation_run_id": run_id})

        assert resp.status_code == 201

    def test_evaluate_run_response_schema(self, admin_client):
        run_id = 42
        fake_resp = _fake_eval_response(run_id)

        with patch(
            "backend.app.api.admin.EvaluationService.evaluate",
            return_value=fake_resp,
        ):
            data = admin_client.post(API_EVAL, json={"generation_run_id": run_id}).json()

        assert "id" in data
        assert "generation_run_id" in data
        assert "scores" in data
        assert data["scores"]["truthfulness_score"] == pytest.approx(0.9)

    def test_evaluate_run_not_found_returns_404(self, admin_client):
        from fastapi import HTTPException
        with patch(
            "backend.app.api.admin.EvaluationService.evaluate",
            side_effect=HTTPException(status_code=404, detail="Generation run not found"),
        ):
            resp = admin_client.post(
                API_EVAL,
                json={"generation_run_id": 99999},
            )
        assert resp.status_code == 404

    def test_evaluate_run_missing_id_returns_422(self, admin_client):
        resp = admin_client.post(API_EVAL, json={})
        assert resp.status_code == 422

    def test_non_admin_evaluate_returns_403(self, client):
        resp = client.post(API_EVAL, json={"generation_run_id": 1})
        assert resp.status_code == 403


class TestDownloadRunDataById:
    def test_404_when_run_not_found(self, admin_client):
        resp = admin_client.get(f"{API_RUN_DATA}/99999")
        assert resp.status_code == 404

    def test_400_for_non_integer_run_id(self, admin_client):
        resp = admin_client.get(f"{API_RUN_DATA}/abc")
        assert resp.status_code == 400

    def test_non_admin_returns_403(self, client):
        resp = client.get(f"{API_RUN_DATA}/1")
        assert resp.status_code == 403

    def test_returns_zip_with_all_files(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user)
        _make_doc(db, run, admin_user,
                  resume_docx_url="resume/docx/key",
                  resume_pdf_url="resume/pdf/key",
                  cover_letter_docx_url="cl/docx/key",
                  cover_letter_pdf_url="cl/pdf/key")

        with patch("backend.app.api.admin._storage_service", return_value=_mock_storage()):
            resp = admin_client.get(f"{API_RUN_DATA}/{run.id}")

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        # debug JSON + resume (txt/docx/pdf) + cover letter (txt/docx/pdf) = 7
        assert len(names) == 7
        assert any(n.endswith(".json") for n in names)
        assert any(n.endswith(".txt") for n in names)
        assert any(n.endswith(".docx") for n in names)
        assert any(n.endswith(".pdf") for n in names)
        # no subfolder prefix for single-run download
        assert all("/" not in n for n in names)

    def test_skips_missing_storage_urls(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user)
        _make_doc(db, run, admin_user,
                  resume_docx_url=None,
                  resume_pdf_url=None,
                  cover_letter_docx_url=None,
                  cover_letter_pdf_url=None)

        with patch("backend.app.api.admin._storage_service", return_value=_mock_storage()):
            resp = admin_client.get(f"{API_RUN_DATA}/{run.id}")

        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        # debug JSON + resume.txt + cover_letter.txt = 3 (no DOCX/PDF URLs)
        assert len(names) == 3

    def test_404_when_all_fetches_fail(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user)
        _make_doc(db, run, admin_user,
                  resume_jsonb={},
                  cover_letter_jsonb={},
                  resume_docx_url=None, resume_pdf_url=None,
                  cover_letter_docx_url=None, cover_letter_pdf_url=None)

        failing_storage = MagicMock()
        failing_storage.get_debug_json_bytes.side_effect = Exception("not found")

        with patch("backend.app.api.admin._storage_service", return_value=failing_storage):
            resp = admin_client.get(f"{API_RUN_DATA}/{run.id}")

        assert resp.status_code == 404

    def test_filename_is_zip(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user)
        _make_doc(db, run, admin_user)

        with patch("backend.app.api.admin._storage_service", return_value=_mock_storage()):
            resp = admin_client.get(f"{API_RUN_DATA}/{run.id}")

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert f"run-data-{run.id}.zip" in cd


class TestDownloadRunDataByDateRange:
    def test_404_when_no_runs_in_range(self, admin_client):
        resp = admin_client.get(API_RUN_DATA, params={"from_date": "2020-01-01", "to_date": "2020-01-02"})
        assert resp.status_code == 404

    def test_non_admin_returns_403(self, client):
        resp = client.get(API_RUN_DATA, params={"from_date": "2026-05-25"})
        assert resp.status_code == 403

    def test_returns_zip_with_subfolders(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user,
                        started_at=datetime(2026, 5, 25, 10, 0, 0, tzinfo=timezone.utc))
        _make_doc(db, run, admin_user)

        with patch("backend.app.api.admin._storage_service", return_value=_mock_storage()):
            resp = admin_client.get(API_RUN_DATA, params={"from_date": "2026-05-25", "to_date": "2026-05-25"})

        assert resp.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        names = zf.namelist()
        assert all(n.startswith(f"{run.id}/") for n in names)

    def test_zip_filename_contains_date_range(self, admin_client, db, admin_user):
        run = _make_run(db, admin_user,
                        started_at=datetime(2026, 5, 25, 10, 0, 0, tzinfo=timezone.utc))
        _make_doc(db, run, admin_user)

        with patch("backend.app.api.admin._storage_service", return_value=_mock_storage()):
            resp = admin_client.get(API_RUN_DATA, params={"from_date": "2026-05-25", "to_date": "2026-05-25"})

        assert resp.status_code == 200
        cd = resp.headers.get("content-disposition", "")
        assert "run-data-2026-05-25-to-2026-05-25.zip" in cd
