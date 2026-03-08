"""
backend/app/services/stats_service.py

Stats service — aggregated system statistics for admin dashboard.

Queries are intentionally simple scalar counts; no complex analytics.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.app.schemas.admin import SystemStats

logger = logging.getLogger(__name__)


class StatsService:
    """
    Read-only system-wide statistics for admin use.

    Dependencies
    ------------
    db: SQLAlchemy Session (direct queries for simple aggregate counts).
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def get_system_stats(self) -> SystemStats:
        """Return aggregate counts across all users."""
        from backend.app.db.models.user import User
        from backend.app.db.models.generation_run import GenerationRun
        from backend.app.db.models.tailored_document import TailoredDocument
        from backend.app.db.models.evaluation_run import EvaluationRun

        total_users = self._db.query(User).count()
        total_runs = self._db.query(GenerationRun).count()
        succeeded = (
            self._db.query(GenerationRun)
            .filter(GenerationRun.status == "succeeded")
            .count()
        )
        failed = (
            self._db.query(GenerationRun)
            .filter(GenerationRun.status == "failed")
            .count()
        )
        total_docs = self._db.query(TailoredDocument).count()
        total_evals = self._db.query(EvaluationRun).count()

        logger.debug(
            "System stats: users=%d runs=%d succeeded=%d failed=%d docs=%d evals=%d",
            total_users, total_runs, succeeded, failed, total_docs, total_evals,
        )
        return SystemStats(
            total_users=total_users,
            total_generation_runs=total_runs,
            total_succeeded_runs=succeeded,
            total_failed_runs=failed,
            total_tailored_documents=total_docs,
            total_evaluation_runs=total_evals,
        )
