"""
backend/app/db/repositories/generation_run_repository.py

CRUD operations for GenerationRun.
"""

from datetime import date, datetime, timezone

from sqlalchemy.orm import Session, joinedload

from backend.app.db.models.generation_run import GenerationRun


class GenerationRunRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, run_id: int) -> GenerationRun | None:
        return self._db.get(GenerationRun, run_id)

    def list_by_user_id(
        self, user_id: str, limit: int = 50, offset: int = 0
    ) -> list[GenerationRun]:
        return (
            self._db.query(GenerationRun)
            .options(joinedload(GenerationRun.tailored_documents))
            .filter(GenerationRun.user_id == user_id)
            .order_by(GenerationRun.started_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    def count_succeeded_by_user_id(self, user_id: str) -> int:
        return (
            self._db.query(GenerationRun)
            .filter(
                GenerationRun.user_id == user_id,
                GenerationRun.status == "succeeded",
            )
            .count()
        )

    def list_by_date_range(
        self, from_date: date, to_date: date
    ) -> list[GenerationRun]:
        """Return succeeded runs whose started_at falls within [from_date, to_date]."""
        start = datetime(from_date.year, from_date.month, from_date.day, tzinfo=timezone.utc)
        end = datetime(to_date.year, to_date.month, to_date.day, 23, 59, 59, tzinfo=timezone.utc)
        return (
            self._db.query(GenerationRun)
            .filter(
                GenerationRun.started_at >= start,
                GenerationRun.started_at <= end,
                GenerationRun.status == "succeeded",
            )
            .order_by(GenerationRun.started_at)
            .all()
        )

    def create(self, **kwargs) -> GenerationRun:
        run = GenerationRun(**kwargs)
        self._db.add(run)
        self._db.flush()
        return run

    def update(self, run: GenerationRun, **kwargs) -> GenerationRun:
        for key, value in kwargs.items():
            setattr(run, key, value)
        self._db.flush()
        return run

    def delete(self, run: GenerationRun) -> None:
        self._db.delete(run)
        self._db.flush()
