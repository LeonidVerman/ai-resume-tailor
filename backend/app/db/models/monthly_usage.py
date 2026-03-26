"""
backend/app/db/models/monthly_usage.py

Monthly usage counter — one row per (user, year, month).

Written exclusively via atomic INSERT … ON CONFLICT DO UPDATE; never
updated via ORM setattr to avoid races.
"""

from sqlalchemy import BigInteger, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base


class MonthlyUsage(Base):
    __tablename__ = "monthly_usage"
    __table_args__ = (UniqueConstraint("user_id", "year", "month", name="uq_monthly_usage"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, unique=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    month: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped["User"] = relationship("User", back_populates="monthly_usage")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<MonthlyUsage user={self.user_id!r} {self.year}-{self.month:02d} count={self.count}>"
        )
