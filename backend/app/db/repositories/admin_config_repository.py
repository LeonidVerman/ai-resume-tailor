"""
backend/app/db/repositories/admin_config_repository.py

CRUD for AdminConfig (single-row settings table).
"""

from sqlalchemy.orm import Session

from backend.app.db.models.admin_config import AdminConfig


_DEFAULTS = {
    "simple_model": "gpt-5.2",
    "initial_credits": 3,
}


class AdminConfigRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self) -> AdminConfig:
        """Return the single config row (always id=1), creating it if absent.

        Pinning to id=1 eliminates the TOCTOU race that could create a second
        row: if two concurrent requests both see an empty table and both try to
        INSERT id=1, only one succeeds — the other gets a PK violation that
        propagates normally.  The migration k4l5m6n7o8p9 ensures id=1 is the
        only row in production.
        """
        row = self._db.query(AdminConfig).filter(AdminConfig.id == 1).first()
        if row is None:
            row = AdminConfig(id=1, **_DEFAULTS)
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
