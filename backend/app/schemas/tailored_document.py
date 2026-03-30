"""
backend/app/schemas/tailored_document.py

Tailored document schemas.

Represents the user-facing saved output of a generation run,
including structured JSON content and artifact download URLs.
"""

from datetime import datetime

from backend.app.schemas.common import APIModel


# ── Artifact URLs ──────────────────────────────────────────────────────────

class ArtifactURLs(APIModel):
    """Download URLs for generated DOCX/PDF artifacts."""
    resume_docx_url: str | None = None
    resume_pdf_url: str | None = None
    cover_letter_docx_url: str | None = None
    cover_letter_pdf_url: str | None = None


# ── Response models ────────────────────────────────────────────────────────

class TailoredDocumentSummary(APIModel):
    """Lightweight listing item for history views."""
    id: int
    company_name: str
    role_title: str
    generation_run_id: int
    artifacts: ArtifactURLs
    created_at: datetime


class TailoredDocumentDetail(APIModel):
    """
    Full tailored document response including structured content.

    resume_text / cover_letter_text are the raw generated strings
    (matching TailorResult.resume and TailorResult.cover_letter from the
    existing generator). resume_json / cover_letter_json are the
    structured representations if parsed.
    """
    id: int
    user_id: str
    generation_run_id: int
    company_name: str
    role_title: str
    # Structured content (may be None if not parsed)
    resume_json: dict | None = None
    cover_letter_json: dict | None = None
    # Section-based diff between master resume and tailored output
    resume_diff: list[dict] | None = None
    artifacts: ArtifactURLs
    created_at: datetime
