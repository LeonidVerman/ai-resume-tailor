"""
backend/app/db/models/tailored_document.py

Tailored document — the user-facing saved output of a generation run.
Stores both structured JSON and URLs to rendered DOCX/PDF artifacts.
"""

from sqlalchemy import BigInteger, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin


class TailoredDocument(Base, CreatedAtMixin):
    __tablename__ = "tailored_documents"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    uuid_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True, unique=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation_run_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation_run_uuid_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), nullable=True, index=True
    )

    company_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    # Structured output
    resume_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    cover_letter_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Artifact storage URLs
    resume_docx_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    resume_pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_docx_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_pdf_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="tailored_documents")  # noqa: F821
    generation_run: Mapped["GenerationRun"] = relationship(  # noqa: F821
        "GenerationRun", back_populates="tailored_documents"
    )

    def __repr__(self) -> str:
        return (
            f"<TailoredDocument id={self.id!r} "
            f"company={self.company_name!r} role={self.role_title!r}>"
        )
