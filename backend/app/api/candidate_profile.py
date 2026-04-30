"""
backend/app/api/candidate_profile.py

Candidate profile CRUD + autofill endpoints.

Endpoints
---------
GET  /candidate-profile                        — return the user's current profile
POST /candidate-profile                        — create a new profile version
PUT  /candidate-profile                        — upsert (update or create) the profile
POST /candidate-profile/complete-onboarding    — mark onboarding complete
GET  /candidate-profile/autofill/resumes       — list resumes available for autofill
GET  /candidate-profile/autofill/draft         — get cached autofill draft for a resume
POST /candidate-profile/autofill/generate      — generate (or regenerate) autofill draft via LLM
POST /candidate-profile/autofill/select-source — persist the source resume ID on the profile
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.schemas.autofill import (
    AutofillDraftResponse,
    AutofillGenerateRequest,
    AutofillSelectSourceRequest,
    AutofillSelectSourceResponse,
)
from backend.app.schemas.candidate_profile import (
    CandidateProfileResponse,
    CandidateProfileUpsertRequest,
)
from backend.app.schemas.structured_resume import StructuredResumeSummary
from backend.app.services.candidate_profile_service import CandidateProfileService
from backend.app.services.profile_autofill_service import ProfileAutofillService

router = APIRouter()


def _service(db: Session = Depends(DbDep)) -> CandidateProfileService:
    return CandidateProfileService(CandidateProfileRepository(db))


@router.get("", response_model=CandidateProfileResponse)
def get_candidate_profile(user: CurrentUserDep, db: DbDep):
    """Return the authenticated user's most recent candidate profile."""
    return _service(db).get_current(user.id)


@router.post("", response_model=CandidateProfileResponse, status_code=201)
def create_candidate_profile(
    request: CandidateProfileUpsertRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """Create a new candidate profile version for the authenticated user."""
    _prefill_email(request, user.email)
    return _service(db).create(user.id, request)


@router.put("", response_model=CandidateProfileResponse)
def update_candidate_profile(
    request: CandidateProfileUpsertRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """Update (or create) the candidate profile for the authenticated user."""
    _prefill_email(request, user.email)
    return _service(db).update(user.id, request)


@router.post("/complete-onboarding", response_model=CandidateProfileResponse)
def complete_onboarding(user: CurrentUserDep, db: DbDep):
    """
    Mark the authenticated user's candidate profile as onboarding-complete.

    Sets onboarding_completed=true and invalidates the cached candidate prompt
    so it is regenerated on the next generation run.
    """
    return _service(db).complete_onboarding(user.id)


# ── Autofill endpoints ─────────────────────────────────────────────────────────

@router.get("/autofill/resumes", response_model=list[StructuredResumeSummary])
def list_autofill_resumes(user: CurrentUserDep, db: DbDep):
    """List the authenticated user's resumes available for autofill."""
    resumes = StructuredResumeRepository(db).list_by_user_id(user.id)
    return [_resume_summary(r) for r in resumes]


@router.get("/autofill/draft", response_model=AutofillDraftResponse)
def get_autofill_draft(resume_id: int, user: CurrentUserDep, db: DbDep):
    """Return the cached autofill draft for a resume, including staleness flag."""
    result = ProfileAutofillService(db).get_draft(user.id, resume_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No draft exists for this resume. Call /autofill/generate first.",
        )
    return result


@router.post("/autofill/generate", response_model=AutofillDraftResponse)
def generate_autofill_draft(request: AutofillGenerateRequest, user: CurrentUserDep, db: DbDep):
    """
    Generate (or regenerate) a candidate profile draft from a resume using the LLM.
    Overwrites any existing draft for the same resume.
    """
    return ProfileAutofillService(db).generate(user.id, request.resume_id)


@router.post("/autofill/select-source", response_model=AutofillSelectSourceResponse)
def select_source_resume(request: AutofillSelectSourceRequest, user: CurrentUserDep, db: DbDep):
    """Persist the selected source resume ID on the user's candidate profile."""
    profile_repo = CandidateProfileRepository(db)
    profile = profile_repo.get_by_user_id(user.id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate profile not found. Save profile data first.",
        )
    profile_repo.update(profile, source_resume_id=request.resume_id)
    db.commit()
    return AutofillSelectSourceResponse(source_resume_id=request.resume_id)


# ── Private helpers ────────────────────────────────────────────────────────────

def _prefill_email(request: CandidateProfileUpsertRequest, login_email: str) -> None:
    """Set contacts.email from login email when the user left it blank."""
    if not request.profile.contacts.email and login_email:
        request.profile.contacts.email = login_email


def _resume_summary(resume) -> StructuredResumeSummary:
    jsonb = resume.resume_jsonb or {}
    name = jsonb.get("name") or jsonb.get("original_filename") or "Unknown"
    return StructuredResumeSummary(
        id=resume.id,
        name=name,
        created_at=resume.created_at,
        source_file_url=resume.source_file_url,
    )
