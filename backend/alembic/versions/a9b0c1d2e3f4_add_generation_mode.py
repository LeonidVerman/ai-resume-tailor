"""add generation_mode to generation_runs and benchmark_runs

Revision ID: a9b0c1d2e3f4
Revises: f3a4b5c6d7e8
Create Date: 2026-04-06

Adds a nullable TEXT column `generation_mode` to both generation_runs and
benchmark_runs. Existing rows default to NULL; application code treats NULL
as "conservative" for display purposes.
"""

from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "generation_runs",
        sa.Column("generation_mode", sa.String(32), nullable=True),
    )
    op.add_column(
        "benchmark_runs",
        sa.Column("generation_mode", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "generation_mode")
    op.drop_column("generation_runs", "generation_mode")
