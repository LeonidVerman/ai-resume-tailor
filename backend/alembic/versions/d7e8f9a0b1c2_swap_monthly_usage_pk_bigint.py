"""swap monthly_usage PK to bigint (swap step)

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-03-26

Swaps UUID PK to bigint:
  monthly_usage.id (uuid) → uuid_id (nullable, for rollback)
  monthly_usage.new_id (bigint) → id (new PK)

API contract: monthly_usage.id is internal only — not exposed in any API.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d7e8f9a0b1c2"
down_revision = "c6d7e8f9a0b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("monthly_usage_pkey", "monthly_usage", type_="primary_key")
    op.alter_column("monthly_usage", "id", new_column_name="uuid_id")
    op.alter_column("monthly_usage", "new_id", new_column_name="id")
    op.drop_constraint("uq_monthly_usage_new_id", "monthly_usage", type_="unique")
    op.create_primary_key("monthly_usage_pkey", "monthly_usage", ["id"])
    op.alter_column("monthly_usage", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("monthly_usage_pkey", "monthly_usage", type_="primary_key")
    op.alter_column("monthly_usage", "id", new_column_name="new_id")
    op.alter_column("monthly_usage", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_monthly_usage_new_id", "monthly_usage", ["new_id"])
    op.create_primary_key("monthly_usage_pkey", "monthly_usage", ["id"])
    op.drop_column("monthly_usage", "new_id")
