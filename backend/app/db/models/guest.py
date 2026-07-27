"""
backend/app/db/models/guest.py

Guest generation tables (issue #155).

- GuestEntitlement  — single source of truth for guest generation usage;
                      SELECT ... FOR UPDATE on this row gives the atomic
                      credit reservation from the design doc.
- GuestDevice       — signed first-party device token registry (hash only).
- GuestAbuseEvent   — append-only abuse/limit event log keyed by a daily
                      HMAC-derived IP hash (no clear IPs stored).
- FunnelEvent       — minimal DB-backed product funnel for /try.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin


class GuestEntitlement(Base, CreatedAtMixin):
    __tablename__ = "guest_entitlements"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    allowed: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Set when a reservation is taken, cleared on release; cleanup releases
    # reservations older than the stale-reservation timeout (30 min) so a
    # crashed generation never permanently consumes the entitlement.
    reserved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<GuestEntitlement user={self.user_id!r} "
            f"used={self.used}/{self.allowed} reserved={self.reserved}>"
        )


class GuestDevice(Base, CreatedAtMixin):
    __tablename__ = "guest_devices"

    device_token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    generation_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    guest_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True
    )

    def __repr__(self) -> str:
        return f"<GuestDevice hash={self.device_token_hash[:8]!r} used={self.generation_used_at}>"


class GuestAbuseEvent(Base, CreatedAtMixin):
    __tablename__ = "guest_abuse_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    ip_daily_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    risk_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return f"<GuestAbuseEvent type={self.event_type!r} ip={self.ip_daily_hash[:8]!r}>"


class FunnelEvent(Base, CreatedAtMixin):
    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    def __repr__(self) -> str:
        return f"<FunnelEvent type={self.event_type!r} user={self.user_id!r}>"
