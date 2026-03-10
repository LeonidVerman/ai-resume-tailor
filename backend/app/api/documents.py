"""
backend/app/api/documents.py

Tailored document retrieval endpoints.

Endpoints
---------
GET /documents/{id}           — return document metadata + text content
GET /documents/{id}/download  — return rendered DOCX or PDF, or plain text

Download behavior
-----------------
The ``format`` query parameter controls the output:
  format=docx  — render the template DOCX and return as application/vnd.openxmlformats-
                 officedocument.wordprocessingml.document
  format=pdf   — render the template DOCX and convert to PDF; return as application/pdf
  format=txt   — return plain generated text (default; backward compatible)

Rendering is performed on-demand via RenderingService.  If the artifact
URLs in the DB are already populated (S3-backed), the signed URL is
redirected to directly (future enhancement).

Legacy records that have no resume_jsonb / cover_letter_jsonb will return
404 for those parts regardless of format.
"""

import logging

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.tailored_document import ArtifactURLs, TailoredDocumentDetail
from backend.app.services.rendering_service import RenderingService

logger = logging.getLogger(__name__)

router = APIRouter()

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"


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
def download_document(
    doc_id: str,
    user: CurrentUserDep,
    db: DbDep,
    part: str = "resume",
    format: str = "txt",
):
    """
    Download a tailored document part in the requested format.

    Query parameters
    ----------------
    part : "resume" (default) | "cover_letter"
        Which part of the tailored document to download.
    format : "txt" (default) | "docx" | "pdf"
        Output format.
        - txt  — plain generated text (backward compatible)
        - docx — rendered DOCX template (application/vnd.openxmlformats-...)
        - pdf  — rendered PDF (application/pdf)

    Rendering for docx/pdf is performed on-demand via RenderingService.
    """
    doc = _repo(db).get_by_id(doc_id)
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if doc.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if part not in ("resume", "cover_letter"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="part must be 'resume' or 'cover_letter'",
        )
    if format not in ("txt", "docx", "pdf"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="format must be 'txt', 'docx', or 'pdf'",
        )

    jsonb = doc.resume_jsonb if part == "resume" else doc.cover_letter_jsonb
    if not jsonb:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{part} content not available for this document",
        )

    text = jsonb.get("text", "")

    # Plain text — backward compatible default.
    if format == "txt":
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{part}.txt"'},
        )

    # DOCX or PDF — render on-demand.
    svc = RenderingService()
    try:
        if part == "resume":
            docx_bytes = svc.render_resume_docx(text)
        else:
            docx_bytes = svc.render_cover_letter_docx(text)
    except Exception as exc:
        logger.error("DOCX rendering failed for doc=%s part=%s: %s", doc_id, part, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DOCX rendering failed: {exc}",
        )

    if format == "docx":
        filename = f"{part}.docx"
        return Response(
            content=docx_bytes,
            media_type=_DOCX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # PDF — convert DOCX bytes to PDF.
    try:
        if part == "resume":
            pdf_bytes = svc.render_resume_pdf(docx_bytes, method="local")
        else:
            pdf_bytes = svc.render_cover_letter_pdf(docx_bytes, method="local")
    except Exception as exc:
        logger.error("PDF rendering failed for doc=%s part=%s: %s", doc_id, part, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF rendering failed: {exc}",
        )

    filename = f"{part}.pdf"
    return Response(
        content=pdf_bytes,
        media_type=_PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
