"""
backend/app/db/models/job_description.py

Job description — supports both URL-scraped and manually pasted JDs.
"""

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, new_uuid


class JobDescription(Base, CreatedAtMixin):
    __tablename__ = "job_descriptions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # scraped | manual
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Soft-delete flag — set to True instead of hard-deleting the row.
    delete_flg: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="job_descriptions")  # noqa: F821
    generation_runs: Mapped[list["GenerationRun"]] = relationship(  # noqa: F821
        "GenerationRun", back_populates="job_description"
    )

    def __repr__(self) -> str:
        return f"<JobDescription id={self.id!r} user_id={self.user_id!r}>"
