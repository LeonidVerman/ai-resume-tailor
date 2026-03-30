"""swap admin_config PK to bigint (swap step)

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-03-26

Swaps String(36) PK to bigint:
  admin_config.id (string/uuid) → uuid_id (nullable, for rollback)
  admin_config.new_id (bigint) → id (new PK)

API contract: admin_config.id is internal only — not exposed in any API.
"""

from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("admin_config_pkey", "admin_config", type_="primary")
    op.alter_column("admin_config", "id", new_column_name="uuid_id")
    op.alter_column("admin_config", "new_id", new_column_name="id")
    op.drop_constraint("uq_admin_config_new_id", "admin_config", type_="unique")
    op.create_primary_key("admin_config_pkey", "admin_config", ["id"])
    op.alter_column("admin_config", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("admin_config_pkey", "admin_config", type_="primary")
    op.alter_column("admin_config", "id", new_column_name="new_id")
    op.alter_column("admin_config", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_admin_config_new_id", "admin_config", ["new_id"])
    op.create_primary_key("admin_config_pkey", "admin_config", ["id"])
    op.drop_column("admin_config", "new_id")
