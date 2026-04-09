"""
backend/app/db/models/stripe_checkout_purchase.py

Records each Stripe checkout session that resulted in a credit grant.
Used as the idempotency guard for webhook credit grants: one row per
checkout session, enforced by a UNIQUE constraint on
stripe_checkout_session_id.
"""

from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base


class StripeCheckoutPurchase(Base):
    __tablename__ = "stripe_checkout_purchases"
    __table_args__ = (
        UniqueConstraint("stripe_checkout_session_id", name="uq_stripe_checkout_session_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    # Primary deduplication key — one row per completed checkout session.
    stripe_checkout_session_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Audit / observability fields
    stripe_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    granted_credits: Mapped[int] = mapped_column(Integer, nullable=False)

    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
