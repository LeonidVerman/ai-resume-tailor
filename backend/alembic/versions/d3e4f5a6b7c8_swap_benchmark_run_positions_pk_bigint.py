"""swap benchmark_run_positions PK to bigint (swap step)

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-03-26

Swaps String(36) PK to bigint:
  benchmark_run_positions.id (string/uuid) → uuid_id (nullable, for rollback)
  benchmark_run_positions.new_id (bigint) → id (new PK)

API contract: benchmark_run_positions.id is not exposed directly in list APIs
(positions are embedded in BenchmarkRunDetail); BenchmarkPositionSummary.id changes
from str to int.
"""

from alembic import op
import sqlalchemy as sa

revision = "d3e4f5a6b7c8"
down_revision = "c2d3e4f5a6b7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "benchmark_run_positions_pkey", "benchmark_run_positions", type_="primary_key"
    )
    op.alter_column(
        "benchmark_run_positions", "id", new_column_name="uuid_id"
    )
    op.alter_column(
        "benchmark_run_positions", "new_id", new_column_name="id"
    )
    op.drop_constraint(
        "uq_benchmark_run_positions_new_id", "benchmark_run_positions", type_="unique"
    )
    op.create_primary_key(
        "benchmark_run_positions_pkey", "benchmark_run_positions", ["id"]
    )
    op.alter_column("benchmark_run_positions", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint(
        "benchmark_run_positions_pkey", "benchmark_run_positions", type_="primary_key"
    )
    op.alter_column(
        "benchmark_run_positions", "id", new_column_name="new_id"
    )
    op.alter_column(
        "benchmark_run_positions", "uuid_id", new_column_name="id"
    )
    op.create_unique_constraint(
        "uq_benchmark_run_positions_new_id", "benchmark_run_positions", ["new_id"]
    )
    op.create_primary_key(
        "benchmark_run_positions_pkey", "benchmark_run_positions", ["id"]
    )
    op.drop_column("benchmark_run_positions", "new_id")
