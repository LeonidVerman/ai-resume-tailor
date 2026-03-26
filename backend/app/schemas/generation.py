"""
backend/app/schemas/generation.py

Generation request/response schemas.
"""

from datetime import datetime
from typing import Literal

from backend.app.schemas.common import APIModel


# ── Request ────────────────────────────────────────────────────────────────

class GenerationRequest(APIModel):
    """
    Request body for POST /generate.

    The user selects which stored resume and job description to use.
    Candidate profile is always loaded from the user's current profile.
    """
    job_description_id: int
    structured_resume_id: int


# ── Status / result ────────────────────────────────────────────────────────

GenerationStatus = Literal["pending", "running", "succeeded", "failed"]


class GenerationRunSummary(APIModel):
    """Lightweight run listing item."""
    id: str
    status: GenerationStatus
    run_type: str
    model_name: str
    started_at: datetime
    completed_at: datetime | None = None
    cost_estimate: float | None = None
    tailored_document_id: str | None = None
    # Company / role from the linked TailoredDocument (None for older records).
    company_name: str | None = None
    role_title: str | None = None


class GenerationRunDetail(APIModel):
    """Full run detail including token usage and error info."""
    id: str
    user_id: str
    job_description_id: int | None = None
    status: GenerationStatus
    run_type: str
    model_name: str
    prompt_version: str
    token_input: int | None = None
    token_output: int | None = None
    cost_estimate: float | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None


class GenerationResponse(APIModel):
    """
    Response body for POST /generate.

    On success: run_id links to the GenerationRun record.
    Tailored document is separately retrievable via GET /documents/{id}.
    """
    run_id: str
    status: GenerationStatus
    tailored_document_id: str | None = None
    message: str | None = None
