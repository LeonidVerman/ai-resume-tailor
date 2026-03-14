"""
backend/app/db/repositories/benchmark_run_repository.py

CRUD operations for BenchmarkRun.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.benchmark_run import BenchmarkRun


class BenchmarkRunRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_id(self, run_id: str) -> BenchmarkRun | None:
        return self._db.get(BenchmarkRun, run_id)

    def get_active(self) -> BenchmarkRun | None:
        """Return the first run with status queued or running, or None."""
        return (
            self._db.query(BenchmarkRun)
            .filter(BenchmarkRun.status.in_(["queued", "running"]))
            .first()
        )

    def list_recent(self, limit: int = 20) -> list[BenchmarkRun]:
        """Return recent benchmark runs, newest first."""
        return (
            self._db.query(BenchmarkRun)
            .order_by(BenchmarkRun.created_at.desc())
            .limit(limit)
            .all()
        )

    def create(self, **kwargs) -> BenchmarkRun:
        run = BenchmarkRun(**kwargs)
        self._db.add(run)
        self._db.flush()
        return run

    def update(self, run: BenchmarkRun, **kwargs) -> BenchmarkRun:
        for key, value in kwargs.items():
            setattr(run, key, value)
        self._db.flush()
        return run
