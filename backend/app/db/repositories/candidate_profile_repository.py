"""
backend/app/db/repositories/candidate_profile_repository.py

CRUD operations for CandidateProfile.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.candidate_profile import CandidateProfile


class CandidateProfileRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, profile_id: str) -> CandidateProfile | None:
        return self._db.get(CandidateProfile, profile_id)

    def get_by_user_id(self, user_id: str) -> CandidateProfile | None:
        return (
            self._db.query(CandidateProfile)
            .filter(CandidateProfile.user_id == user_id)
            .order_by(CandidateProfile.created_at.desc())
            .first()
        )

    def list_by_user_id(self, user_id: str) -> list[CandidateProfile]:
        return (
            self._db.query(CandidateProfile)
            .filter(CandidateProfile.user_id == user_id)
            .order_by(CandidateProfile.created_at.desc())
            .all()
        )

    def list_all(self) -> list[CandidateProfile]:
        return self._db.query(CandidateProfile).all()

    def create(self, **kwargs) -> CandidateProfile:
        profile = CandidateProfile(**kwargs)
        self._db.add(profile)
        self._db.flush()
        return profile

    def update(self, profile: CandidateProfile, **kwargs) -> CandidateProfile:
        for key, value in kwargs.items():
            setattr(profile, key, value)
        self._db.flush()
        return profile

    def delete(self, profile: CandidateProfile) -> None:
        self._db.delete(profile)
        self._db.flush()
