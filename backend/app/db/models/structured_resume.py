"""
backend/app/db/models/structured_resume.py

Structured resume — parsed representation of an uploaded resume.
The source file is stored in object storage; the parsed JSON lives here.
"""

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class StructuredResume(Base, CreatedAtMixin):
    __tablename__ = "structured_resumes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_file_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # SHA-256 of the uploaded file bytes (issue #155).  Persisted for every
    # upload; no behavior attached yet (future: dedup, merge, caching, abuse
    # investigation).
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Set when the uploaded file was converted from PDF to DOCX.
    # Contains the user-visible disclaimer; None for DOCX uploads.
    input_conversion_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Serialized ResumeDocument IR (see compiler/models.py ResumeDocument.to_dict()).
    # Populated for PDF uploads; None for DOCX uploads.
    template_ir_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # LLM classification result produced at upload time (Phase 1).
    # Populated asynchronously after upload; None until classification completes.
    classification_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Soft-delete flag — set to True instead of hard-deleting the row.
    delete_flg: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="structured_resumes")  # noqa: F821

    def __repr__(self) -> str:
        return f"<StructuredResume id={self.id!r} user_id={self.user_id!r}>"
