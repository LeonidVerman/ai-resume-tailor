"""add auth fields to users

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-03-14

Adds Supabase auth fields required for real JWT authentication:
  - supabase_user_id  — unique Supabase Auth UUID, links identity to local row
  - is_active         — soft-disable accounts without deletion
  - last_login_at     — timestamp of most recent successful login
  - updated_at        — timestamp of last row mutation
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("supabase_user_id", sa.String(255), nullable=True),
    )
    op.create_unique_constraint("uq_users_supabase_user_id", "users", ["supabase_user_id"])
    op.create_index("ix_users_supabase_user_id", "users", ["supabase_user_id"])

    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
    )
    op.add_column(
        "users",
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "updated_at")
    op.drop_column("users", "last_login_at")
    op.drop_column("users", "is_active")
    op.drop_index("ix_users_supabase_user_id", table_name="users")
    op.drop_constraint("uq_users_supabase_user_id", "users")
    op.drop_column("users", "supabase_user_id")
