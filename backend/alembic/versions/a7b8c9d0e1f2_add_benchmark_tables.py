"""add benchmark_runs and benchmark_run_positions tables

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-03-13

Adds two tables to support Web-based benchmarking:
  - benchmark_runs: metadata, config, summary scores, and report JSON per run
  - benchmark_run_positions: per-position scores linked to a benchmark run
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("client_id", sa.String(36), nullable=False, index=True),
        # queued | running | completed | failed
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("positions_count", sa.Integer, nullable=True),
        sa.Column("completed_positions", sa.Integer, nullable=False, server_default="0"),
        # Category score averages (1–10 scale, 2 decimal places)
        sa.Column("truthfulness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("role_fit_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("seniority_positioning_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("clarity_impact_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("mechanism_quality_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("constraint_compliance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("cover_letter_effectiveness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("overall_readiness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("integrated_score", sa.Numeric(5, 2), nullable=True),
        # Weights and full report stored as JSONB
        sa.Column("weights_json", JSONB, nullable=True),
        sa.Column("report_json", JSONB, nullable=True),
        # Filesystem paths
        sa.Column("report_dir", sa.Text, nullable=True),
        sa.Column("report_json_path", sa.Text, nullable=True),
        sa.Column("report_csv_path", sa.Text, nullable=True),
        # Generation config used
        sa.Column("generation_mode", sa.String(32), nullable=False, server_default="simple"),
        sa.Column("simple_model", sa.String(128), nullable=True),
        sa.Column("phase1_model", sa.String(128), nullable=True),
        sa.Column("phase2_model", sa.String(128), nullable=True),
        # Error tracking
        sa.Column("error_message", sa.Text, nullable=True),
        # Lifecycle timestamps
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_table(
        "benchmark_run_positions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "benchmark_run_id",
            sa.String(36),
            sa.ForeignKey("benchmark_runs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("position_url", sa.Text, nullable=False),
        sa.Column("company", sa.Text, nullable=True),
        sa.Column("role_title", sa.Text, nullable=True),
        # Per-position scores (1–10 scale)
        sa.Column("truthfulness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("role_fit_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("seniority_positioning_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("clarity_impact_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("mechanism_quality_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("constraint_compliance_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("cover_letter_effectiveness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("overall_readiness_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("integrated_score", sa.Numeric(5, 2), nullable=True),
        # Raw assessment file path and full JSON
        sa.Column("raw_assessment_path", sa.Text, nullable=True),
        sa.Column("assessment_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("benchmark_run_positions")
    op.drop_table("benchmark_runs")
