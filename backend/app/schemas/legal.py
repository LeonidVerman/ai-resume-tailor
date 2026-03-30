"""
backend/app/schemas/legal.py

Request / response schemas for the legal consent system.
"""

from datetime import datetime

from pydantic import Field

from backend.app.schemas.common import APIModel


# ── Document metadata ─────────────────────────────────────────────────────────

class LegalDocumentInfo(APIModel):
    """Minimal public metadata for a legal document version."""
    id: int
    doc_type: str
    version: str
    title: str
    file_path: str
    content_sha256: str
    effective_at: datetime
    requires_reaccept: bool


class LegalCurrentResponse(APIModel):
    """Response for GET /legal/current — the two currently active documents."""
    terms: LegalDocumentInfo
    privacy: LegalDocumentInfo


# ── User status ───────────────────────────────────────────────────────────────

class LegalDocumentAcceptanceInfo(APIModel):
    """User's acceptance state for one document type."""
    accepted: bool
    version: str | None = None
    accepted_at: datetime | None = None
    is_current: bool  # True if the accepted version is still the active one


class LegalStatusResponse(APIModel):
    """Response for GET /legal/status — user's current compliance state."""
    compliant: bool  # True if user has accepted current required versions
    terms: LegalDocumentAcceptanceInfo
    privacy: LegalDocumentAcceptanceInfo


# ── Acceptance request ────────────────────────────────────────────────────────

class LegalAcceptRequest(APIModel):
    """Body for POST /legal/accept."""
    terms_document_id: int
    privacy_document_id: int
    acceptance_method: str = Field(default="checkbox", pattern=r"^(checkbox|api)$")
    source_surface: str = Field(default="register", max_length=50)
