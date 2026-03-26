"""
backend/app/schemas/evaluation.py

Evaluation schemas.

Evaluation runs score a completed generation for quality dimensions
such as truthfulness, role fit, clarity, and seniority match.
"""

from datetime import datetime

from pydantic import Field

from backend.app.schemas.common import APIModel


class EvaluationScores(APIModel):
    """All five quality scores (0.0 – 1.0; None if not yet evaluated)."""
    truthfulness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    role_fit_score: float | None = Field(default=None, ge=0.0, le=1.0)
    clarity_score: float | None = Field(default=None, ge=0.0, le=1.0)
    seniority_score: float | None = Field(default=None, ge=0.0, le=1.0)
    integrated_score: float | None = Field(default=None, ge=0.0, le=1.0)


class EvaluationRequest(APIModel):
    """Request body for POST /admin/evaluate-run."""
    generation_run_id: int


class EvaluationResponse(APIModel):
    """Full evaluation result returned after scoring."""
    id: int
    generation_run_id: int
    scores: EvaluationScores
    created_at: datetime
