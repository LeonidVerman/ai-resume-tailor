"""
backend/app/api/resume.py

Structured resume endpoints.

Endpoints
---------
POST /resumes/upload     — upload a DOCX or PDF resume; parse and store it
GET  /resumes            — list the authenticated user's stored resumes
GET  /resumes/{id}       — return a specific resume record
"""

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.dependencies import CurrentUserDep, DbDep
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
    # Prefer original_filename (the user's actual upload filename) over the
    # heuristic-parsed name, which can be garbled for LibreOffice-converted PDFs.
    name = jsonb.get("original_filename") or jsonb.get("name", "Unknown")
    return StructuredResumeSummary(
        id=resume.id,
        name=name,
        created_at=resume.created_at,
        source_file_url=resume.source_file_url,
    )


@router.post("/upload", response_model=StructuredResumeResponse, status_code=201)
async def upload_resume(file: UploadFile, user: CurrentUserDep, db: DbDep):
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

    resume = _repo(db).create(
        user_id=user.id,
        resume_jsonb=doc_dict,
        source_file_url=None,
        input_conversion_warning=norm.warning_message,
    )

    # Upload the normalized DOCX to storage and record the key.
    storage_svc = StorageService(make_storage_client_from_settings())
    key = storage_svc.upload_resume_template(user.id, str(resume.id), norm.normalized_data)
    _repo(db).update(resume, source_file_url=key)

    return _to_response(resume)


@router.get("", response_model=list[StructuredResumeSummary])
def list_resumes(user: CurrentUserDep, db: DbDep):
    """List all stored resumes for the authenticated user."""
    resumes = _repo(db).list_by_user_id(user.id)
    return [_to_summary(r) for r in resumes]


@router.get("/{resume_id}", response_model=StructuredResumeResponse)
def get_resume(resume_id: str, user: CurrentUserDep, db: DbDep):
    """Return a specific resume record."""
    resume = _repo(db).get_by_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    if resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _to_response(resume)


@router.delete("/{resume_id}", status_code=204)
def delete_resume(resume_id: str, user: CurrentUserDep, db: DbDep):
    """Soft-delete a stored resume (sets delete_flg=true; row and files are preserved)."""
    resume = _repo(db).get_by_id(resume_id)
    if resume is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")
    if resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    _repo(db).delete(resume)
