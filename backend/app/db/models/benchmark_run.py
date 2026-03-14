"""
backend/app/db/models/benchmark_run.py

Benchmark run — stores metadata, config, progress, and aggregate scores
for a Web-initiated benchmark execution.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, new_uuid


class BenchmarkRun(Base, TimestampMixin):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)

    # User/client who the benchmark is run against
    client_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)

    # queued | running | completed | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")

    # Progress tracking
    positions_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completed_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Category score averages (1–10 scale)
    truthfulness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    role_fit_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    seniority_positioning_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    clarity_impact_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    mechanism_quality_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    constraint_compliance_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    cover_letter_effectiveness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    overall_readiness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    integrated_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)

    # Full report stored as JSONB for DB-backed UI reads
    weights_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Filesystem paths for the report directory and files
    report_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_json_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_csv_path: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Generation config captured at run time
    generation_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="simple")
    simple_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phase1_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phase2_model: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Error tracking
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Lifecycle timestamps
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship to per-position results
    positions: Mapped[list["BenchmarkRunPosition"]] = relationship(  # noqa: F821
        "BenchmarkRunPosition",
        back_populates="benchmark_run",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<BenchmarkRun id={self.id!r} status={self.status!r} "
            f"integrated={self.integrated_score!r}>"
        )
