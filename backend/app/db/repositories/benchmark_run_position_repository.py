"""
backend/app/db/repositories/benchmark_run_position_repository.py

CRUD operations for BenchmarkRunPosition.
"""

from sqlalchemy.orm import Session

from backend.app.db.models.benchmark_run_position import BenchmarkRunPosition


class BenchmarkRunPositionRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_benchmark_run_id(self, benchmark_run_id: str) -> list[BenchmarkRunPosition]:
        return (
            self._db.query(BenchmarkRunPosition)
            .filter(BenchmarkRunPosition.benchmark_run_id == benchmark_run_id)
            .all()
        )

    def create(self, **kwargs) -> BenchmarkRunPosition:
        pos = BenchmarkRunPosition(**kwargs)
        self._db.add(pos)
        self._db.flush()
        return pos

    def bulk_create(self, items: list[dict]) -> None:
        for item in items:
            self._db.add(BenchmarkRunPosition(**item))
        self._db.flush()
