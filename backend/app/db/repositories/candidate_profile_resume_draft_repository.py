"""
backend/app/db/repositories/candidate_profile_resume_draft_repository.py

CRUD operations for CandidateProfileResumeDraft.
One row per (user_id, resume_id) pair — upserted on generate.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from backend.app.db.models.candidate_profile_resume_draft import CandidateProfileResumeDraft


class CandidateProfileResumeDraftRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_user_and_resume(
        self, user_id: str, resume_id: int
    ) -> CandidateProfileResumeDraft | None:
        return (
            self._db.query(CandidateProfileResumeDraft)
            .filter(
                CandidateProfileResumeDraft.user_id == user_id,
                CandidateProfileResumeDraft.resume_id == resume_id,
            )
            .first()
        )

    def upsert(
        self,
        user_id: str,
        resume_id: int,
        draft_jsonb: dict,
        resume_hash: str,
        status: str,
        model: str | None,
        generated_at: datetime,
        error_message: str | None = None,
    ) -> CandidateProfileResumeDraft:
        """Create or update the draft row for this (user_id, resume_id) pair."""
        row = self.get_by_user_and_resume(user_id, resume_id)
        if row is None:
            row = CandidateProfileResumeDraft(
                user_id=user_id,
                resume_id=resume_id,
            )
            self._db.add(row)
        row.draft_jsonb = draft_jsonb
        row.resume_hash = resume_hash
        row.status = status
        row.model = model
        row.generated_at = generated_at
        row.error_message = error_message
        self._db.flush()
        return row
