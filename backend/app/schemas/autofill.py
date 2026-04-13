"""
backend/app/schemas/autofill.py

Request/response schemas for candidate profile autofill endpoints.
"""

from datetime import datetime

from backend.app.schemas.candidate_profile import CandidateProfileDocument
from backend.app.schemas.common import APIModel


class AutofillGenerateRequest(APIModel):
    resume_id: int


class AutofillDraftResponse(APIModel):
    resume_id: int
    draft: CandidateProfileDocument
    status: str          # "ready" | "failed"
    resume_hash: str
    is_stale: bool       # True when stored hash differs from current resume hash
    model: str | None
    generated_at: datetime


class AutofillSelectSourceRequest(APIModel):
    resume_id: int


class AutofillSelectSourceResponse(APIModel):
    source_resume_id: int | None
