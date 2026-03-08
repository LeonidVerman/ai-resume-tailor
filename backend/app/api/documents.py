"""
backend/app/api/documents.py

Tailored document retrieval endpoints.

Endpoints
---------
GET /documents/{id}           — return document metadata + text content
GET /documents/{id}/download  — return signed download URL or plain text

Phase 8 status: PARTIALLY FUNCTIONAL (storage URLs deferred)
-------------------------------------------------------------
DOCX/PDF storage upload is not yet wired (requires configured S3 client).
The /download endpoint returns the plain generated text from the DB
instead of signed artifact URLs.  When StorageService is activated,
replace the text response with a signed URL redirect.
"""

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.tailored_document import ArtifactURLs, TailoredDocumentDetail

router = APIRouter()


def _repo(db) -> TailoredDocumentRepository:
    return TailoredDocumentRepository(db)


def _artifacts(doc) -> ArtifactURLs:
    return ArtifactURLs(
        resume_docx_url=doc.resume_docx_url,
        resume_pdf_url=doc.resume_pdf_url,
        cover_letter_docx_url=doc.cover_letter_docx_url,
        cover_letter_pdf_url=doc.cover_letter_pdf_url,
    )


def _to_detail(doc) -> TailoredDocumentDetail:
    return TailoredDocumentDetail(
        id=doc.id,
        user_id=doc.user_id,
        generation_run_id=doc.generation_run_id,
        company_name=doc.company_name,
        role_title=doc.role_title,
        resume_json=doc.resume_jsonb,
        cover_letter_json=doc.cover_letter_jsonb,
        artifacts=_artifacts(doc),
        created_at=doc.created_at,
    )


@router.get("/{doc_id}", response_model=TailoredDocumentDetail)
def get_document(doc_id: str, user: CurrentUserDep, db: DbDep):
    """
    Return the full tailored document record including structured content.

    resume_json and cover_letter_json contain the generated text stored as
    {"text": "..."} until structured parsing is added.
    """
    doc = _repo(db).get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _to_detail(doc)


@router.get("/{doc_id}/download")
def download_document(doc_id: str, user: CurrentUserDep, db: DbDep, part: str = "resume"):
    """
    Return plain-text content for the requested document part.

    Query parameters
    ----------------
    part : "resume" (default) | "cover_letter"
        Which part of the tailored document to download.

    Phase 8 limitation: returns plain text directly from DB.
    When storage is wired, this will return a signed DOCX/PDF URL instead.
    """
    doc = _repo(db).get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if part == "resume":
        jsonb = doc.resume_jsonb
    elif part == "cover_letter":
        jsonb = doc.cover_letter_jsonb
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part must be 'resume' or 'cover_letter'",
        )

    if not jsonb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{part} content not available for this document",
        )

    text = jsonb.get("text", "")
    return Response(
        content=text,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{part}.txt"'},
    )
