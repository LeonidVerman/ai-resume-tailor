"""
backend/app/api/resume.py

Structured resume endpoints.

Endpoints
---------
POST /resumes/upload     — upload a DOCX or PDF resume; parse and store it
GET  /resumes            — list the authenticated user's stored resumes
GET  /resumes/{id}       — return a specific resume record

Phase 8 status: FUNCTIONAL (file storage URL not persisted — deferred)
-----------------------------------------------------------------------
The resume is parsed and stored as structured JSONB.  The uploaded file
itself is not currently persisted to object storage (that requires a
configured StorageClient).  source_file_url will be None until storage
is wired in a future phase.
"""

from fastapi import APIRouter, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.schemas.structured_resume import StructuredResumeResponse, StructuredResumeSummary
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
        created_at=resume.created_at,
    )


def _to_summary(resume) -> StructuredResumeSummary:
    name = (resume.resume_jsonb or {}).get("name", "Unknown")
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
    The raw file is not persisted to object storage in this phase.
    """
    data = await file.read()
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large (max {_MAX_UPLOAD_BYTES // 1024 // 1024} MB)",
        )

    filename = file.filename or "resume.docx"
    svc = ResumeParserService()
    doc = svc.parse(data, filename)

    resume = _repo(db).create(
        user_id=user.id,
        resume_jsonb=doc.model_dump(mode="json"),
        source_file_url=None,  # storage upload deferred
    )
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
