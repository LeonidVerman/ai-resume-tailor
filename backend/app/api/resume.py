"""
backend/app/api/resume.py

Structured resume endpoints.

Endpoints
---------
POST /resumes/upload     — upload a DOCX or PDF resume; parse and store it
GET  /resumes            — list the authenticated user's stored resumes
GET  /resumes/{id}       — return a specific resume record
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.dependencies import CurrentUserDep, DbDep

logger = logging.getLogger(__name__)
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.schemas.structured_resume import StructuredResumeResponse, StructuredResumeSummary
from backend.app.services.document_normalization_service import normalize_input_document
from backend.app.services.resume_parser_service import ResumeParserService

router = APIRouter()

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


def _repo(db: Session) -> StructuredResumeRepository:
    return StructuredResumeRepository(db)


def _to_response(resume) -> StructuredResumeResponse:
    from backend.app.schemas.structured_resume import StructuredResumeDocument
    doc = StructuredResumeDocument.model_validate(resume.resume_jsonb)
    return StructuredResumeResponse(
        id=resume.id,
        user_id=resume.user_id,
        resume=doc,
        source_file_url=resume.source_file_url,
        input_conversion_warning=resume.input_conversion_warning,
        created_at=resume.created_at,
    )


def _to_summary(resume) -> StructuredResumeSummary:
    jsonb = resume.resume_jsonb or {}
    # Prefer the parsed name; fall back to original_filename if parsing produced
    # nothing (e.g. an empty DOCX or a LibreOffice-converted PDF with no text).
    name = jsonb.get("name") or jsonb.get("original_filename") or "Unknown"
    return StructuredResumeSummary(
        id=resume.id,
        name=name,
        created_at=resume.created_at,
        source_file_url=resume.source_file_url,
    )


def _run_classification_background(resume_id: int, norm_data: bytes, template_ir: dict | None) -> None:
    """Background task: classify the uploaded resume template via LLM.

    Opens its own DB session so the upload session can be closed first.
    Failures are caught and logged inside ResumeClassificationService.
    """
    from backend.app.config import get_settings
    from backend.app.db.session import get_session_factory
    from backend.app.services.resume_classification_service import ResumeClassificationService

    settings = get_settings()
    factory = get_session_factory(settings.database_url)
    db = factory()
    try:
        ResumeClassificationService(db).classify_and_store(resume_id, norm_data, template_ir)
    except Exception as exc:
        logger.error("Classification background task failed resume=%s: %s", resume_id, exc)
    finally:
        db.close()


@router.post("/upload", response_model=StructuredResumeResponse, status_code=201)
async def upload_resume(file: UploadFile, user: CurrentUserDep, db: DbDep, background_tasks: BackgroundTasks):
    """
    Upload a DOCX or PDF resume.

    The file is parsed into a StructuredResumeDocument and stored in the DB.
    The normalized DOCX is uploaded to object storage and the key is stored
    in source_file_url.
    """
    from backend.app.clients.storage_client import make_storage_client_from_settings
    from backend.app.services.storage_service import StorageService

    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {_MAX_UPLOAD_BYTES // 1024 // 1024} MB)",
        )

    filename = file.filename or "resume.docx"

    # Normalize to DOCX (converts PDF via LibreOffice if needed).
    try:
        norm = normalize_input_document(data, filename)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Parse from the normalized DOCX bytes (or original bytes for other types).
    svc = ResumeParserService()
    # Treat the normalized file as DOCX if conversion was performed.
    parse_filename = "resume.docx" if norm.conversion_performed else filename
    doc = svc.parse(norm.normalized_data, parse_filename)

    doc_dict = doc.model_dump(mode="json")
    doc_dict["original_filename"] = filename

    conversion_warning = norm.warning_message
    if not doc_dict.get("raw_text", "").strip() and not conversion_warning:
        conversion_warning = (
            "No text could be extracted from this file. "
            "Profile generation will not be available for this resume. "
            "Try saving it as a standard .docx file."
        )

    resume = _repo(db).create(
        user_id=user.id,
        resume_jsonb=doc_dict,
        source_file_url=None,
        input_conversion_warning=conversion_warning,
        template_ir_jsonb=norm.template_ir,
    )

    # Upload the original file bytes to storage using the correct extension so
    # the stored key's extension matches the actual content type.
    storage_svc = StorageService(make_storage_client_from_settings())
    ext = norm.source_type if norm.source_type in ("docx", "pdf") else "docx"
    key = storage_svc.upload_resume_template(user.id, str(resume.id), norm.normalized_data, extension=ext)
    _repo(db).update(resume, source_file_url=key)

    logger.info(
        "Resume uploaded user_id=%s resume_id=%s filename=%s",
        user.id, resume.id, key,
    )

    # Commit before scheduling the background task so its separate session
    # can see the new row (background tasks run before the dependency generator
    # cleanup commits the upload transaction).
    db.commit()

    # Kick off LLM classification in the background.  The upload response is
    # returned immediately; classification persists to classification_jsonb once
    # the LLM call completes.  Failures are caught inside the background task.
    background_tasks.add_task(
        _run_classification_background,
        resume.id,
        norm.normalized_data,
        norm.template_ir,
    )

    return _to_response(resume)


@router.get("", response_model=list[StructuredResumeSummary])
def list_resumes(user: CurrentUserDep, db: DbDep):
    """List all stored resumes for the authenticated user."""
    resumes = _repo(db).list_by_user_id(user.id)
    return [_to_summary(r) for r in resumes]


@router.get("/{resume_id}", response_model=StructuredResumeResponse)
def get_resume(resume_id: int, user: CurrentUserDep, db: DbDep):
    """Return a specific resume record."""
    resume = _repo(db).get_by_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    if resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _to_response(resume)


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: int, user: CurrentUserDep, db: DbDep):
    """Soft-delete a stored resume (sets delete_flg=true; row and files are preserved)."""
    resume = _repo(db).get_by_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    if resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    logger.info(
        "Resume deleted user_id=%s resume_id=%s filename=%s",
        user.id, resume_id, resume.source_file_url,
    )
    _repo(db).delete(resume)
