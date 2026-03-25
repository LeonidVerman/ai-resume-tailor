"""add monthly_usage table

Revision ID: a2b3c4d5e6f7
Revises: f2a3b4c5d6e7
Create Date: 2026-03-25

Adds per-user monthly generation counter used for quota enforcement.
One row per (user_id, year, month); incremented atomically via
INSERT … ON CONFLICT DO UPDATE WHERE count < limit.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "monthly_usage",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("year", sa.Integer, nullable=False),
        sa.Column("month", sa.Integer, nullable=False),
        sa.Column("count", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_monthly_usage_user_id", "monthly_usage", ["user_id"])
    op.create_unique_constraint(
        "uq_monthly_usage", "monthly_usage", ["user_id", "year", "month"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_monthly_usage", "monthly_usage", type_="unique")
    op.drop_index("ix_monthly_usage_user_id", table_name="monthly_usage")
    op.drop_table("monthly_usage")
