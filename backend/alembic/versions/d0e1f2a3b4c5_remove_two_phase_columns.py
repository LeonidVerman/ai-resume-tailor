"""remove two-phase columns

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-03-17

Drops the two-phase pipeline columns that are no longer used:
  admin_config:   generation_mode, phase1_model, phase2_model
  benchmark_runs: generation_mode, phase1_model, phase2_model
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("admin_config", "generation_mode")
    op.drop_column("admin_config", "phase1_model")
    op.drop_column("admin_config", "phase2_model")

    op.drop_column("benchmark_runs", "generation_mode")
    op.drop_column("benchmark_runs", "phase1_model")
    op.drop_column("benchmark_runs", "phase2_model")


def downgrade() -> None:
    op.add_column(
        "admin_config",
        sa.Column("generation_mode", sa.String(32), nullable=False, server_default="simple"),
    )
    op.add_column(
        "admin_config",
        sa.Column("phase1_model", sa.String(128), nullable=False, server_default="gpt-4.1"),
    )
    op.add_column(
        "admin_config",
        sa.Column("phase2_model", sa.String(128), nullable=False, server_default="gpt-4.1"),
    )

    op.add_column(
        "benchmark_runs",
        sa.Column("generation_mode", sa.String(32), nullable=False, server_default="simple"),
    )
    op.add_column(
        "benchmark_runs",
        sa.Column("phase1_model", sa.String(128), nullable=True),
    )
    op.add_column(
        "benchmark_runs",
        sa.Column("phase2_model", sa.String(128), nullable=True),
    )
