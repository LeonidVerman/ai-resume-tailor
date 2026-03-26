"""
backend/app/db/repositories/generation_run_repository.py

CRUD operations for GenerationRun.
"""

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
