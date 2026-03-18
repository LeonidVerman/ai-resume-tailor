"""
backend/app/db/models/generation_run.py

Generation run — immutable operational log of every generation attempt.
Tracks inputs, outputs, token usage, cost, and status for traceability.
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, new_uuid


class GenerationRun(Base):
    __tablename__ = "generation_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_description_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("job_descriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Run metadata
    run_type: Mapped[str] = mapped_column(String(32), nullable=False)   # single_pass
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending | running | succeeded | failed
    model_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")

    # Snapshot / payload
    input_snapshot_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_output_jsonb: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Token / cost accounting
    token_input: Mapped[int | None] = mapped_column(Integer, nullable=True)
    token_output: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_estimate: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)

    # Timing
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Error
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="generation_runs")  # noqa: F821
    job_description: Mapped["JobDescription | None"] = relationship(  # noqa: F821
        "JobDescription", back_populates="generation_runs"
    )
    tailored_documents: Mapped[list["TailoredDocument"]] = relationship(  # noqa: F821
        "TailoredDocument", back_populates="generation_run", cascade="all, delete-orphan"
    )
    evaluation_runs: Mapped[list["EvaluationRun"]] = relationship(  # noqa: F821
        "EvaluationRun", back_populates="generation_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<GenerationRun id={self.id!r} status={self.status!r} "
            f"type={self.run_type!r}>"
        )
