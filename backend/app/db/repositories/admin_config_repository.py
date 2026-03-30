"""
backend/app/db/repositories/admin_config_repository.py

CRUD for AdminConfig (single-row settings table).
"""

from sqlalchemy.orm import Session

from backend.app.db.models.admin_config import AdminConfig


_DEFAULTS = {
    "simple_model": "gpt-5.2",
}


class AdminConfigRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self) -> AdminConfig:
        """Return the single config row, creating it with defaults if absent."""
        row = self._db.query(AdminConfig).first()
        if row is None:
            row = AdminConfig(**_DEFAULTS)
            self._db.add(row)
            self._db.flush()
        return row

    def upsert(self, **kwargs) -> AdminConfig:
        """Update the config row (creates it first if absent)."""
        row = self.get()
        for key, value in kwargs.items():
            setattr(row, key, value)
        self._db.flush()
        return row
