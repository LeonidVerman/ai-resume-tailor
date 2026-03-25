"""
backend/app/db/repositories/monthly_usage_repository.py

Atomic operations for the monthly_usage table.

All writes use a single SQL statement to avoid check-then-act races under
concurrent requests from the same user.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.base import new_uuid


class MonthlyUsageRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_count(self, user_id: str, year: int, month: int) -> int:
        """Return the current usage count for (user, year, month). 0 if no row yet."""
        row = self._db.execute(
            text(
                "SELECT count FROM monthly_usage "
                "WHERE user_id = :user_id AND year = :year AND month = :month"
            ),
            {"user_id": user_id, "year": year, "month": month},
        ).first()
        return row[0] if row else 0

    def increment_atomic(self, user_id: str, year: int, month: int, limit: int) -> bool:
        """
        Atomically increment the monthly counter if count < limit.

        Returns True if the increment succeeded (usage allowed).
        Returns False if count >= limit (quota exhausted — caller should try credits).

        Implementation: single INSERT … ON CONFLICT DO UPDATE WHERE count < limit.
        If the WHERE clause fails, zero rows are affected, which we detect via rowcount.
        """
        result = self._db.execute(
            text(
                """
                INSERT INTO monthly_usage (id, user_id, year, month, count)
                VALUES (:id, :user_id, :year, :month, 1)
                ON CONFLICT (user_id, year, month)
                DO UPDATE SET count = monthly_usage.count + 1
                WHERE monthly_usage.count < :limit
                """
            ),
            {
                "id": new_uuid(),
                "user_id": user_id,
                "year": year,
                "month": month,
                "limit": limit,
            },
        )
        return result.rowcount > 0
