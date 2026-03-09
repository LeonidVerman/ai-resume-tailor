"""
backend/app/db/repositories/job_description_repository.py

CRUD operations for JobDescription.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.job_description import JobDescription


class JobDescriptionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, jd_id: str) -> JobDescription | None:
        return self._db.get(JobDescription, jd_id)

    def list_by_user_id(self, user_id: str) -> list[JobDescription]:
        return (
            self._db.query(JobDescription)
            .filter(JobDescription.user_id == user_id)
            .order_by(JobDescription.created_at.desc())
            .all()
        )

    def create(self, **kwargs) -> JobDescription:
        jd = JobDescription(**kwargs)
        self._db.add(jd)
        self._db.flush()
        self._db.refresh(jd)
        return jd

    def update(self, jd: JobDescription, **kwargs) -> JobDescription:
        for key, value in kwargs.items():
            setattr(jd, key, value)
        self._db.flush()
        return jd

    def delete(self, jd: JobDescription) -> None:
        self._db.delete(jd)
        self._db.flush()
