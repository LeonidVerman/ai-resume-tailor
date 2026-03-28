"""
backend/app/db/models/legal.py

Legal consent tables.

- LegalDocument          — immutable version registry (one row per doc version)
- LegalAcceptanceEvent   — append-only acceptance log
- UserLegalStatus        — convenience cache of each user's current acceptance
"""

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, TimestampMixin


class LegalDocument(Base, CreatedAtMixin):
    """One row per published document version.  Rows are immutable after publish."""

    __tablename__ = "legal_documents"
    __table_args__ = (
        UniqueConstraint("doc_type", "version", name="uq_legal_documents_type_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)        # terms_of_service | privacy_notice
    version: Mapped[str] = mapped_column(String(20), nullable=False)         # v1.0 | v1.1 | v2.0
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)             # repo-relative path
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 of canonical markdown
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | archived
    requires_reaccept: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<LegalDocument id={self.id} type={self.doc_type!r} version={self.version!r} status={self.status!r}>"


class LegalAcceptanceEvent(Base, CreatedAtMixin):
    """Append-only log.  One row per user per document version accepted."""

    __tablename__ = "legal_acceptance_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    doc_type: Mapped[str] = mapped_column(String(50), nullable=False)
    legal_document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("legal_documents.id"),
        nullable=False,
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    acceptance_method: Mapped[str] = mapped_column(String(50), nullable=False)   # checkbox | api
    source_surface: Mapped[str] = mapped_column(String(50), nullable=False)       # register | legal_gate
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)  # IPv4 or IPv6
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    document: Mapped["LegalDocument"] = relationship("LegalDocument", lazy="select")

    def __repr__(self) -> str:
        return (
            f"<LegalAcceptanceEvent id={self.id} user_id={self.user_id!r} "
            f"type={self.doc_type!r} version={self.version!r}>"
        )


class UserLegalStatus(Base, TimestampMixin):
    """Convenience cache — source of truth is legal_acceptance_events."""

    __tablename__ = "user_legal_status"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    accepted_terms_document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("legal_documents.id"), nullable=True
    )
    accepted_privacy_document_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("legal_documents.id"), nullable=True
    )
    accepted_terms_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_privacy_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<UserLegalStatus user_id={self.user_id!r}>"
