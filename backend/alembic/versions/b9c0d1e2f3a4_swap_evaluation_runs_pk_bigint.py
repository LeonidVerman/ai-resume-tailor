"""swap evaluation_runs PK to bigint (swap step)

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-26

Swaps UUID PK to bigint:
  evaluation_runs.id (uuid) → uuid_id (nullable, for rollback)
  evaluation_runs.new_id (bigint) → id (new PK)

API contract: evaluation_runs.id is internal only — not exposed in any
API response. No external contract change.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old UUID primary key
    op.drop_constraint("evaluation_runs_pkey", "evaluation_runs", type_="primary")

    # Rename id → uuid_id (preserve for rollback)
    op.alter_column("evaluation_runs", "id", new_column_name="uuid_id")

    # Rename new_id → id
    op.alter_column("evaluation_runs", "new_id", new_column_name="id")

    # Drop the unique constraint we used as a surrogate (now PK)
    op.drop_constraint("uq_evaluation_runs_new_id", "evaluation_runs", type_="unique")

    # Create bigint primary key
    op.create_primary_key("evaluation_runs_pkey", "evaluation_runs", ["id"])

    # Make uuid_id nullable (already is, but explicit)
    op.alter_column("evaluation_runs", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("evaluation_runs_pkey", "evaluation_runs", type_="primary")
    op.alter_column("evaluation_runs", "id", new_column_name="new_id")
    op.alter_column("evaluation_runs", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_evaluation_runs_new_id", "evaluation_runs", ["new_id"])
    op.create_primary_key("evaluation_runs_pkey", "evaluation_runs", ["id"])
    op.drop_column("evaluation_runs", "new_id")
