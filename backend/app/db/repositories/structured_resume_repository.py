"""
backend/app/db/repositories/structured_resume_repository.py

CRUD operations for StructuredResume.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.structured_resume import StructuredResume


class StructuredResumeRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, resume_id: str) -> StructuredResume | None:
        return self._db.get(StructuredResume, resume_id)

    def list_by_user_id(self, user_id: str) -> list[StructuredResume]:
        return (
            self._db.query(StructuredResume)
            .filter(StructuredResume.user_id == user_id)
            .order_by(StructuredResume.created_at.desc())
            .all()
        )

    def get_latest_by_user_id(self, user_id: str) -> StructuredResume | None:
        return (
            self._db.query(StructuredResume)
            .filter(StructuredResume.user_id == user_id)
            .order_by(StructuredResume.created_at.desc())
            .first()
        )

    def create(self, **kwargs) -> StructuredResume:
        resume = StructuredResume(**kwargs)
        self._db.add(resume)
        self._db.flush()
        return resume

    def delete(self, resume: StructuredResume) -> None:
        self._db.delete(resume)
        self._db.flush()
