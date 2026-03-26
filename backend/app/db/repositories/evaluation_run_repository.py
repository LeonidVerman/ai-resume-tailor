"""
backend/app/db/repositories/evaluation_run_repository.py

CRUD operations for EvaluationRun.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.evaluation_run import EvaluationRun


class EvaluationRunRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, eval_id: int) -> EvaluationRun | None:
        return self._db.get(EvaluationRun, eval_id)

    def get_by_generation_run_id(self, run_id: int) -> EvaluationRun | None:
        return (
            self._db.query(EvaluationRun)
            .filter(EvaluationRun.generation_run_id == run_id)
            .first()
        )

    def create(self, **kwargs) -> EvaluationRun:
        ev = EvaluationRun(**kwargs)
        self._db.add(ev)
        self._db.flush()
        return ev

    def update(self, ev: EvaluationRun, **kwargs) -> EvaluationRun:
        for key, value in kwargs.items():
            setattr(ev, key, value)
        self._db.flush()
        return ev
