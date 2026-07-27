"""
backend/app/db/models/admin_config.py

Admin configuration — single-row settings table for admin-controlled
generation behaviour (model selection) and signup credit policy.
"""

from sqlalchemy import BigInteger, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin


class AdminConfig(Base, TimestampMixin):
    __tablename__ = "admin_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    simple_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default="gpt-5.2"
    )

    # Number of free generation credits granted to newly registered users.
    # Configurable via the Admin panel — no code change needed to adjust.
    initial_credits: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3
    )

    # ── Guest generation (issue #155) ──────────────────────────────────────
    # Kill switch: the public /try flow is fully disabled unless true.
    guest_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # Global cap on guest generations per UTC day (circuit breaker).
    guest_daily_global_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, default=25, server_default="25"
    )
    # Max concurrently running guest generations.
    guest_concurrent_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2, server_default="2"
    )
    # Soft per-IP daily limit (guest sessions per ip_daily_hash per UTC day).
    guest_ip_daily_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3, server_default="3"
    )
    # Days before unclaimed guest content is purged.
    guest_retention_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=7, server_default="7"
    )

    def __repr__(self) -> str:
        return (
            f"<AdminConfig simple={self.simple_model!r} "
            f"initial_credits={self.initial_credits!r}>"
        )
