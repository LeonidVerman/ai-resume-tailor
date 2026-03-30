"""
backend/app/api/candidate_profile.py

Candidate profile CRUD endpoints.

Endpoints
---------
GET  /candidate-profile       — return the user's current profile
POST /candidate-profile       — create a new profile version
PUT  /candidate-profile       — upsert (update or create) the profile
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.schemas.candidate_profile import (
    CandidateProfileResponse,
    CandidateProfileUpsertRequest,
)
from backend.app.services.candidate_profile_service import CandidateProfileService

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
    return _service(db).create(user.id, request)


@router.put("", response_model=CandidateProfileResponse)
def update_candidate_profile(
    request: CandidateProfileUpsertRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """Update (or create) the candidate profile for the authenticated user."""
    return _service(db).update(user.id, request)


@router.post("/complete-onboarding", response_model=CandidateProfileResponse)
def complete_onboarding(user: CurrentUserDep, db: DbDep):
    """
    Mark the authenticated user's candidate profile as onboarding-complete.

    Sets onboarding_completed=true and invalidates the cached candidate prompt
    so it is regenerated on the next generation run.
    """
    return _service(db).complete_onboarding(user.id)
