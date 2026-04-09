"""add stripe_checkout_purchases table

Revision ID: j3k4l5m6n7o8
Revises: i2j3k4l5m6n7
Create Date: 2026-04-09

Changes:
  - New table: stripe_checkout_purchases
    Stores one row per completed Stripe checkout session that granted credits.
    UNIQUE constraint on stripe_checkout_session_id prevents duplicate grants
    when Stripe replays or retries webhook deliveries.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision: str = "j3k4l5m6n7o8"
down_revision = "i2j3k4l5m6n7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stripe_checkout_purchases",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("stripe_checkout_session_id", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            PGUUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_event_id", sa.String(255), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(255), nullable=True),
        sa.Column("granted_credits", sa.Integer, nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_stripe_checkout_purchases_session_id",
        "stripe_checkout_purchases",
        ["stripe_checkout_session_id"],
        unique=True,
    )
    op.create_index(
        "ix_stripe_checkout_purchases_user_id",
        "stripe_checkout_purchases",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stripe_checkout_purchases_user_id", "stripe_checkout_purchases")
    op.drop_index("ix_stripe_checkout_purchases_session_id", "stripe_checkout_purchases")
    op.drop_table("stripe_checkout_purchases")
