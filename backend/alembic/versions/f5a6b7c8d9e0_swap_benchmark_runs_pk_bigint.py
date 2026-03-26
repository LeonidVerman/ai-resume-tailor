"""swap benchmark_runs PK to bigint (swap step)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-03-26

Swaps String(36) PK to bigint in benchmark_runs and updates the FK
in benchmark_run_positions accordingly.

API contract: BenchmarkRunSummary.id changes from str to int.
GET /admin/benchmark-runs/{run_id} and download endpoint now accept int path param.
"""

from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── benchmark_run_positions: drop old FK, rename columns ─────────────────
    op.drop_constraint(
        "benchmark_run_positions_benchmark_run_id_fkey",
        "benchmark_run_positions",
        type_="foreignkey",
    )
    op.alter_column(
        "benchmark_run_positions", "benchmark_run_id", new_column_name="benchmark_run_uuid_id"
    )
    op.alter_column(
        "benchmark_run_positions", "new_benchmark_run_id", new_column_name="benchmark_run_id"
    )
    op.alter_column("benchmark_run_positions", "benchmark_run_uuid_id", nullable=True)

    # ── benchmark_runs: swap PK ───────────────────────────────────────────────
    op.drop_constraint("benchmark_runs_pkey", "benchmark_runs", type_="primary_key")
    op.alter_column("benchmark_runs", "id", new_column_name="uuid_id")
    op.alter_column("benchmark_runs", "new_id", new_column_name="id")
    op.drop_constraint("uq_benchmark_runs_new_id", "benchmark_runs", type_="unique")
    op.create_primary_key("benchmark_runs_pkey", "benchmark_runs", ["id"])
    op.alter_column("benchmark_runs", "uuid_id", nullable=True)

    # ── benchmark_run_positions: create new FK and index ─────────────────────
    op.create_foreign_key(
        "benchmark_run_positions_benchmark_run_id_fkey",
        "benchmark_run_positions",
        "benchmark_runs",
        ["benchmark_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_benchmark_run_positions_benchmark_run_id",
        "benchmark_run_positions",
        ["benchmark_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_benchmark_run_positions_benchmark_run_id",
        table_name="benchmark_run_positions",
    )
    op.drop_constraint(
        "benchmark_run_positions_benchmark_run_id_fkey",
        "benchmark_run_positions",
        type_="foreignkey",
    )

    # Restore benchmark_runs PK
    op.drop_constraint("benchmark_runs_pkey", "benchmark_runs", type_="primary_key")
    op.alter_column("benchmark_runs", "id", new_column_name="new_id")
    op.alter_column("benchmark_runs", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_benchmark_runs_new_id", "benchmark_runs", ["new_id"])
    op.create_primary_key("benchmark_runs_pkey", "benchmark_runs", ["id"])
    op.drop_column("benchmark_runs", "new_id")

    # Restore benchmark_run_positions FK column
    op.alter_column(
        "benchmark_run_positions", "benchmark_run_id", new_column_name="new_benchmark_run_id"
    )
    op.alter_column(
        "benchmark_run_positions", "benchmark_run_uuid_id", new_column_name="benchmark_run_id"
    )
    op.create_foreign_key(
        "benchmark_run_positions_benchmark_run_id_fkey",
        "benchmark_run_positions",
        "benchmark_runs",
        ["benchmark_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
