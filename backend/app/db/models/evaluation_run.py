"""
backend/app/db/models/evaluation_run.py

Evaluation run — stores quality scores for a completed generation run.
"""

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, new_uuid


class EvaluationRun(Base, CreatedAtMixin):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=new_uuid
    )
    generation_run_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("generation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Individual score columns (0.0 – 1.0; null if not evaluated)
    truthfulness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    role_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    clarity_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    seniority_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)
    integrated_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 4), nullable=True)

    # Relationships
    generation_run: Mapped["GenerationRun"] = relationship(  # noqa: F821
        "GenerationRun", back_populates="evaluation_runs"
    )

    def __repr__(self) -> str:
        return (
            f"<EvaluationRun id={self.id!r} "
            f"generation_run_id={self.generation_run_id!r} "
            f"integrated={self.integrated_score!r}>"
        )
