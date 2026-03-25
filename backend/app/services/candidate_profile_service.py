"""
backend/app/services/candidate_profile_service.py

Candidate profile CRUD and validation.

Validates input through the Phase 4 Pydantic schema
(CandidateProfileDocument) before storing to DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from backend.app.db.models.candidate_profile import CandidateProfile
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.schemas.candidate_profile import (
    CandidateProfileDocument,
    CandidateProfileResponse,
    CandidateProfileUpsertRequest,
)
from backend.app.services.candidate_profile_normalizer import normalize_candidate_profile

logger = logging.getLogger(__name__)


class CandidateProfileService:
    def __init__(self, repo: CandidateProfileRepository) -> None:
        self._repo = repo

    # ── Read ───────────────────────────────────────────────────────────────

    def get_current(self, user_id: str) -> CandidateProfileResponse:
        """Return the user's most recent profile or raise 404."""
        profile = self._repo.get_by_user_id(user_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate profile not found. Create one first.",
            )
        return self._to_response(profile)

    def get_by_id(self, profile_id: str, user_id: str) -> CandidateProfileResponse:
        """Return a specific profile; 404 if missing, 403 if wrong user."""
        profile = self._repo.get_by_id(profile_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Profile not found")
        if profile.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return self._to_response(profile)

    # ── Write ──────────────────────────────────────────────────────────────

    def create(self, user_id: str, request: CandidateProfileUpsertRequest) -> CandidateProfileResponse:
        """Create a new profile version for the user."""
        doc = request.profile
        profile_jsonb = normalize_candidate_profile(doc.model_dump(mode="json"))
        profile = self._repo.create(
            user_id=user_id,
            profile_version=request.profile_version,
            profile_jsonb=profile_jsonb,
            prompt_synched=False,
        )
        logger.info("Created candidate profile id=%s user=%s", profile.id, user_id)
        return self._to_response(profile)

    def update(
        self, user_id: str, request: CandidateProfileUpsertRequest
    ) -> CandidateProfileResponse:
        """
        Update the user's current profile in place, or create if none exists.

        This is an upsert: the profile content is replaced, the version
        string is updated, and updated_at is refreshed automatically.
        """
        profile = self._repo.get_by_user_id(user_id)
        if profile is None:
            return self.create(user_id, request)
        doc = request.profile
        profile_jsonb = normalize_candidate_profile(doc.model_dump(mode="json"))
        updated = self._repo.update(
            profile,
            profile_version=request.profile_version,
            profile_jsonb=profile_jsonb,
            prompt_synched=False,
        )
        logger.info("Updated candidate profile id=%s user=%s", updated.id, user_id)
        return self._to_response(updated)

    def complete_onboarding(self, user_id: str) -> CandidateProfileResponse:
        """Mark the user's candidate profile as onboarding-complete."""
        profile = self._repo.get_by_user_id(user_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Candidate profile not found. Save profile data first.",
            )
        updated = self._repo.update(
            profile,
            onboarding_completed=True,
            onboarding_completed_at=datetime.now(tz=timezone.utc),
            prompt_synched=False,
        )
        logger.info("Onboarding completed for profile id=%s user=%s", updated.id, user_id)
        return self._to_response(updated)

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _to_response(profile: CandidateProfile) -> CandidateProfileResponse:
        doc = CandidateProfileDocument.model_validate(profile.profile_jsonb)
        return CandidateProfileResponse(
            id=profile.id,
            user_id=profile.user_id,
            profile_version=profile.profile_version,
            profile=doc,
            onboarding_completed=profile.onboarding_completed,
            created_at=profile.created_at,
            updated_at=profile.updated_at,
        )
