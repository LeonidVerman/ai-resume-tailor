"""
backend/app/api/legal.py

Legal consent endpoints.

GET  /legal/current  — active document metadata (auth optional, metadata only)
GET  /legal/status   — user's acceptance compliance state (requires auth)
POST /legal/accept   — record acceptance of both current documents (requires auth)
"""

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.schemas.legal import LegalAcceptRequest, LegalCurrentResponse, LegalStatusResponse
from backend.app.services.legal_service import LegalService

logger = logging.getLogger(__name__)

router = APIRouter()

# Repo root (backend/app/api/ → up 3 levels)
_REPO_ROOT = Path(__file__).parents[3]


@router.get("/current", response_model=LegalCurrentResponse)
def get_current_documents(db: DbDep):
    """Return metadata for the two currently active legal documents."""
    return LegalService(db).get_current_documents()


@router.get("/document/{doc_type}", response_class=PlainTextResponse)
def get_document_content(doc_type: str, db: DbDep):
    """Return the raw markdown text of the current active document.

    doc_type must be 'terms_of_service' or 'privacy_notice'.
    """
    svc = LegalService(db)
    doc = svc._get_active_doc(doc_type)  # raises RuntimeError if not found
    file_path = _REPO_ROOT / doc.file_path
    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="Document file not found.")
    return file_path.read_text(encoding="utf-8")


@router.get("/status", response_model=LegalStatusResponse)
def get_legal_status(user: CurrentUserDep, db: DbDep):
    """Return the authenticated user's legal acceptance compliance state."""
    return LegalService(db).get_user_status(user.id)


@router.post("/accept", status_code=204)
def accept_documents(
    request: Request,
    body: LegalAcceptRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """Record acceptance of the Terms of Service and Privacy Notice.

    Verifies both document IDs are currently active, writes two immutable
    acceptance events, and updates the user_legal_status cache.
    Idempotent — re-accepting the same versions is allowed (new event row).
    """
    LegalService(db).record_acceptance(
        user_id=user.id,
        terms_document_id=body.terms_document_id,
        privacy_document_id=body.privacy_document_id,
        acceptance_method=body.acceptance_method,
        source_surface=body.source_surface,
        request=request,
    )
    db.commit()
