"""
backend/tests/api/test_admin_api.py

API tests for GET /admin/system-stats and POST /admin/evaluate-run.

StatsService is exercised against real DB data (via the admin_client fixture).
EvaluationService is mocked because it calls into tailor.assess.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from backend.app.schemas.evaluation import EvaluationResponse, EvaluationScores

API_STATS = "/api/v1/admin/system-stats"
API_EVAL = "/api/v1/admin/evaluate-run"


def _fake_eval_response(run_id: str) -> EvaluationResponse:
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
            assert value >= 0, f"{key} should be non-negative"

    def test_non_admin_returns_403(self, client):
        """Regular user client should be refused."""
        resp = client.get(API_STATS)
        assert resp.status_code == 403


class TestEvaluateRun:
    def test_evaluate_run_returns_201(self, admin_client):
        run_id = str(uuid.uuid4())
        fake_resp = _fake_eval_response(run_id)

        with patch(
            "backend.app.api.admin.EvaluationService.evaluate",
            return_value=fake_resp,
        ):
            resp = admin_client.post(API_EVAL, json={"generation_run_id": run_id})

        assert resp.status_code == 201

    def test_evaluate_run_response_schema(self, admin_client):
        run_id = str(uuid.uuid4())
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
                json={"generation_run_id": str(uuid.uuid4())},
            )
        assert resp.status_code == 404

    def test_evaluate_run_missing_id_returns_422(self, admin_client):
        resp = admin_client.post(API_EVAL, json={})
        assert resp.status_code == 422

    def test_non_admin_evaluate_returns_403(self, client):
        resp = client.post(API_EVAL, json={"generation_run_id": str(uuid.uuid4())})
        assert resp.status_code == 403
