"""
backend/app/db/base.py

SQLAlchemy declarative base and shared column mixins.

All model files import Base from here.
Alembic env.py imports Base.metadata from here after importing all models.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Project-wide SQLAlchemy declarative base."""
    pass


class TimestampMixin:
    """Adds created_at / updated_at columns to a model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class CreatedAtMixin:
    """Adds only created_at (for append-only tables)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def new_uuid() -> str:
    """Generate a new UUID string (used as default for id columns)."""
    return str(uuid.uuid4())
