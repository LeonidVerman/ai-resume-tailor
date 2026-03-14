"""add assess_model to benchmark_runs

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-03-14

Adds assess_model column to benchmark_runs so the assessment model
chosen at run-start time is persisted alongside the generation config.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "benchmark_runs",
        sa.Column("assess_model", sa.String(128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "assess_model")
