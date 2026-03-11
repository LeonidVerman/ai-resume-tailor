"""
backend/app/db/models/admin_config.py

Admin configuration — single-row settings table for admin-controlled
generation behaviour (mode, model selection).
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, new_uuid


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )

    # "simple" | "two_phase"
    generation_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="simple"
    )
    simple_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-5.2"
    )
    phase1_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-4.1"
    )
    phase2_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-4.1"
    )

    def __repr__(self) -> str:
        return (
            f"<AdminConfig mode={self.generation_mode!r} "
            f"simple={self.simple_model!r} "
            f"p1={self.phase1_model!r} p2={self.phase2_model!r}>"
        )
