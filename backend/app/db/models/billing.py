"""
backend/app/db/models/billing.py

Billing — one record per user tracking Stripe subscription state.
Kept intentionally small; Stripe is the source of truth for billing events.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, new_uuid
from backend.app.constants import PLAN_FREE


class Billing(Base, TimestampMixin):
    __tablename__ = "billing"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one billing record per user
        index=True,
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subscription_status: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # active | canceled | past_due | trialing
    plan_type: Mapped[str] = mapped_column(String(32), nullable=False, default=PLAN_FREE)
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="billing")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<Billing id={self.id!r} user_id={self.user_id!r} "
            f"plan={self.plan_type!r} status={self.subscription_status!r}>"
        )
