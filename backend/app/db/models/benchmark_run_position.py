"""
backend/app/db/models/benchmark_run_position.py

Per-position assessment result for a benchmark run.
"""

from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, CreatedAtMixin, new_uuid


class BenchmarkRunPosition(Base, CreatedAtMixin):
    __tablename__ = "benchmark_run_positions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    benchmark_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    position_url: Mapped[str] = mapped_column(Text, nullable=False)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_title: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Per-position scores (1–10 scale)
    truthfulness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    role_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    seniority_positioning_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    clarity_impact_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    mechanism_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    constraint_compliance_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cover_letter_effectiveness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overall_readiness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    integrated_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Raw assessment artifact path and full JSON
    raw_assessment_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    assessment_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationship
    benchmark_run: Mapped["BenchmarkRun"] = relationship(  # noqa: F821
        "BenchmarkRun", back_populates="positions"
    )

    def __repr__(self) -> str:
        return (
            f"<BenchmarkRunPosition id={self.id!r} "
            f"benchmark_run_id={self.benchmark_run_id!r} "
            f"company={self.company!r} integrated={self.integrated_score!r}>"
        )
