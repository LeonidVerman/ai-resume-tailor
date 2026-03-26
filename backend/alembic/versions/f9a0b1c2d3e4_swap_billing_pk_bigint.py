"""swap billing PK to bigint (swap step)

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-03-26

Swaps UUID PK to bigint:
  billing.id (uuid) → uuid_id (nullable, for rollback)
  billing.new_id (bigint) → id (new PK)

API contract: billing.id is internal only — not exposed in any API response.
"""

from alembic import op
import sqlalchemy as sa

revision = "f9a0b1c2d3e4"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("billing_pkey", "billing", type_="primary_key")
    op.alter_column("billing", "id", new_column_name="uuid_id")
    op.alter_column("billing", "new_id", new_column_name="id")
    op.drop_constraint("uq_billing_new_id", "billing", type_="unique")
    op.create_primary_key("billing_pkey", "billing", ["id"])
    op.alter_column("billing", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("billing_pkey", "billing", type_="primary_key")
    op.alter_column("billing", "id", new_column_name="new_id")
    op.alter_column("billing", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_billing_new_id", "billing", ["new_id"])
    op.create_primary_key("billing_pkey", "billing", ["id"])
    op.drop_column("billing", "new_id")
