"""add signup credit policy

Revision ID: i2j3k4l5m6n7
Revises: h1i2j3k4l5m6
Create Date: 2026-04-09

Changes:
  - admin_config: add signup_credit_mode column (default "normal")
  - billing: add initial_credits_granted, initial_credits_amount,
             initial_credits_mode columns
  - Backfill: mark all existing billing rows as initial_credits_granted=true
    so existing users are never retroactively granted signup credits.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "i2j3k4l5m6n7"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── admin_config ──────────────────────────────────────────────────────────
    op.add_column(
        "admin_config",
        sa.Column(
            "signup_credit_mode",
            sa.String(32),
            nullable=False,
            server_default="normal",
        ),
    )

    # ── billing ───────────────────────────────────────────────────────────────
    op.add_column(
        "billing",
        sa.Column(
            "initial_credits_granted",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "billing",
        sa.Column("initial_credits_amount", sa.Integer, nullable=True),
    )
    op.add_column(
        "billing",
        sa.Column("initial_credits_mode", sa.String(32), nullable=True),
    )

    # Backfill: all existing billing rows belong to pre-feature users.
    # Mark them as already granted so the signup flow never applies to them.
    op.execute(
        "UPDATE billing SET initial_credits_granted = true "
        "WHERE initial_credits_granted = false"
    )


def downgrade() -> None:
    op.drop_column("billing", "initial_credits_mode")
    op.drop_column("billing", "initial_credits_amount")
    op.drop_column("billing", "initial_credits_granted")
    op.drop_column("admin_config", "signup_credit_mode")
