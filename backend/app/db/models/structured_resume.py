"""
backend/app/db/models/structured_resume.py

Structured resume — parsed representation of an uploaded resume.
The source file is stored in object storage; the parsed JSON lives here.
"""

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, new_uuid


class StructuredResume(Base, CreatedAtMixin):
    __tablename__ = "structured_resumes"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_jsonb: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    source_file_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Set when the uploaded file was converted from PDF to DOCX.
    # Contains the user-visible disclaimer; None for DOCX uploads.
    input_conversion_warning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Soft-delete flag — set to True instead of hard-deleting the row.
    delete_flg: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="structured_resumes")  # noqa: F821

    def __repr__(self) -> str:
        return f"<StructuredResume id={self.id!r} user_id={self.user_id!r}>"
