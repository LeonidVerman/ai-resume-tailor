"""
backend/app/db/models/candidate_profile.py

Candidate profile — structured candidate data injected into every generation.
Stored as JSONB so the schema can evolve without migrations.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, new_uuid


class CandidateProfile(Base, TimestampMixin):
    __tablename__ = "candidate_profiles"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_version: Mapped[str] = mapped_column(String(32), nullable=False, default="1")
    profile_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="candidate_profiles")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<CandidateProfile id={self.id!r} user_id={self.user_id!r} "
            f"version={self.profile_version!r}>"
        )
