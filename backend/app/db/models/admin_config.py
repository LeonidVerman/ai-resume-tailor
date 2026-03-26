"""
backend/app/db/models/admin_config.py

Admin configuration — single-row settings table for admin-controlled
generation behaviour (model selection).
"""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    simple_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-5.2"
    )

    def __repr__(self) -> str:
        return f"<AdminConfig simple={self.simple_model!r}>"
