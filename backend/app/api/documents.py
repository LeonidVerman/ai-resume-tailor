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
  format=docx  — serve pre-rendered DOCX bytes from storage (if available),
                 or render on-demand as fallback
  format=pdf   — serve pre-rendered PDF bytes from storage (if available),
                 or render on-demand as fallback
  format=txt   — return plain generated text (default; backward compatible)

Legacy records that have no resume_jsonb / cover_letter_jsonb will return
404 for those parts regardless of format.
"""

import logging

from fastapi import APIRouter, HTTPException, Response, status

from backend.app.clients.storage_client import make_storage_client_from_settings
from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.tailored_document import ArtifactURLs, TailoredDocumentDetail
from backend.app.services.rendering_service import RenderingService
from backend.app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

router = APIRouter()

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PDF_MIME = "application/pdf"


def _artifact_filename(doc, part: str, fmt: str) -> str:
    """Build a download filename for the artifact.

    For resume: uses template_original_filename from resume_jsonb when available,
    deriving the stem from the original upload filename.  Falls back to the CLI
    template basename pattern when not available.

    Examples:
        original_filename="My_Resume.docx", company="Xero", part="resume", fmt="docx"
            → My_Resume_Xero.docx
        fallback: Leonid_Verman_Resume_Xero.docx
    """
    import os
    from tailor.config import RESUME_TEMPLATE, COVER_TEMPLATE

    company_name = doc.company_name or "Unknown"
    safe_company = "".join(
        c if c.isalnum() or c in "_-" else "_"
        for c in company_name
    )

    if part == "resume":
        original_filename = (doc.resume_jsonb or {}).get("template_original_filename", "")
        if original_filename:
            base_stem = os.path.splitext(os.path.basename(original_filename))[0]
            return f"{base_stem}_{safe_company}.{fmt}"
        template = RESUME_TEMPLATE
    else:
        template = COVER_TEMPLATE

    stem = os.path.splitext(os.path.basename(str(template)))[0]
    name = stem.replace("Template", safe_company)
    return f"{name}.{fmt}"


def _repo(db) -> TailoredDocumentRepository:
    return TailoredDocumentRepository(db)


def _storage_svc() -> StorageService:
    return StorageService(make_storage_client_from_settings())


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
def get_document(doc_id: int, user: CurrentUserDep, db: DbDep):
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
    doc_id: int,
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
    filename = _artifact_filename(doc, part, format)

    # Plain text — backward compatible default.
    if format == "txt":
        return Response(
            content=text,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # DOCX — serve from storage if available, else render on-demand.
    if format == "docx":
        storage_key = doc.resume_docx_url if part == "resume" else doc.cover_letter_docx_url
        if storage_key:
            try:
                docx_bytes = _storage_svc().get_bytes(storage_key)
                return Response(
                    content=docx_bytes,
                    media_type=_DOCX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'},
                )
            except Exception as exc:
                logger.warning(
                    "Storage fetch failed for doc=%s part=%s key=%s: %s; falling back to render",
                    doc_id, part, storage_key, exc,
                )
        # Fallback: on-demand render.
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
        return Response(
            content=docx_bytes,
            media_type=_DOCX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # PDF — serve from storage if available, else render on-demand.
    assert format == "pdf"
    storage_key = doc.resume_pdf_url if part == "resume" else doc.cover_letter_pdf_url
    if storage_key:
        try:
            pdf_bytes = _storage_svc().get_bytes(storage_key)
            return Response(
                content=pdf_bytes,
                media_type=_PDF_MIME,
                headers={"Content-Disposition": f'attachment; filename="{filename}"'},
            )
        except Exception as exc:
            logger.warning(
                "Storage fetch failed for doc=%s part=%s key=%s: %s; falling back to render",
                doc_id, part, storage_key, exc,
            )
    # Fallback: on-demand render.
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
    return Response(
        content=pdf_bytes,
        media_type=_PDF_MIME,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
