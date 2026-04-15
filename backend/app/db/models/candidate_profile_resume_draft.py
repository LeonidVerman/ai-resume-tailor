"""
backend/app/db/models/candidate_profile_resume_draft.py

Cached LLM-generated candidate profile draft derived from a specific resume.
One row per (user_id, resume_id) pair — upserted on each generate call.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, CreatedAtMixin


class CandidateProfileResumeDraft(Base, CreatedAtMixin):
    __tablename__ = "candidate_profile_resume_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("structured_resumes.id", ondelete="CASCADE"),
        nullable=False,
    )
    draft_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # SHA-256 hex digest of the resume JSONB at generation time — used for staleness detection
    resume_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # "ready" or "failed"
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ready")
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<CandidateProfileResumeDraft id={self.id!r} "
            f"user_id={self.user_id!r} resume_id={self.resume_id!r} "
            f"status={self.status!r}>"
        )
