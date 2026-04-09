"""
backend/app/db/models/admin_config.py

Admin configuration — single-row settings table for admin-controlled
generation behaviour (model selection) and signup credit policy.
"""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin
from backend.app.constants import SIGNUP_CREDIT_MODE_NORMAL


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    simple_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-5.2"
    )

    # Signup credit policy — controls how many initial credits new users receive.
    # Resolved amounts: normal=3, beta=10 (see constants.SIGNUP_CREDIT_AMOUNTS).
    signup_credit_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default=SIGNUP_CREDIT_MODE_NORMAL
    )

    def __repr__(self) -> str:
        return (
            f"<AdminConfig simple={self.simple_model!r} "
            f"signup_credit_mode={self.signup_credit_mode!r}>"
        )
