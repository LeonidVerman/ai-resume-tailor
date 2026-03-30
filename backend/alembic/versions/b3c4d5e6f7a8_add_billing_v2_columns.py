"""add billing v2 columns

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-03-25

Extends the billing table for the v2 quota/credits model:
  - extra_credits          INTEGER NOT NULL DEFAULT 0  (one-time pack balance)
  - monthly_limit_override INTEGER NULLABLE            (grandfathered per-user quota)
  - stripe_price_id        VARCHAR(255) NULLABLE       (active subscription price ID)

No backfill: existing users start with 0 extra_credits.
Use POST /admin/billing/grant-credits to grant credits manually.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "billing",
        sa.Column("extra_credits", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "billing",
        sa.Column("monthly_limit_override", sa.Integer, nullable=True),
    )
    op.add_column(
        "billing",
        sa.Column("stripe_price_id", sa.String(255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("billing", "stripe_price_id")
    op.drop_column("billing", "monthly_limit_override")
    op.drop_column("billing", "extra_credits")
