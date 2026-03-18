"""
backend/app/db/models/admin_config.py

Admin configuration — single-row settings table for admin-controlled
generation behaviour (model selection).
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, new_uuid


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=new_uuid
    )

    simple_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-5.2"
    )

    def __repr__(self) -> str:
        return f"<AdminConfig simple={self.simple_model!r}>"
