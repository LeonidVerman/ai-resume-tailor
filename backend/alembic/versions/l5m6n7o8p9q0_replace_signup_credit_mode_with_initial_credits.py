"""replace signup_credit_mode with initial_credits on admin_config

Revision ID: l5m6n7o8p9q0
Revises: k4l5m6n7o8p9
Create Date: 2026-04-12

Replace the two-value enum column `signup_credit_mode` ('normal' / 'beta')
with a plain integer `initial_credits` so the admin can set any number
without a code change.

Data migration:
  normal → 3
  beta   → 10
  (any other value treated as normal → 3)
"""

import sqlalchemy as sa
from alembic import op

revision: str = "l5m6n7o8p9q0"
down_revision = "k4l5m6n7o8p9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # Step 1: add the new integer column (nullable initially for the data copy)
    op.add_column(
        "admin_config",
        sa.Column("initial_credits", sa.Integer, nullable=True),
    )

    # Step 2: migrate existing mode value → integer
    conn.execute(sa.text(
        "UPDATE admin_config "
        "SET initial_credits = CASE "
        "  WHEN signup_credit_mode = 'beta' THEN 10 "
        "  ELSE 3 "
        "END"
    ))

    # Step 3: make non-nullable with server default 3
    op.alter_column("admin_config", "initial_credits", nullable=False,
                    server_default="3")

    # Step 4: drop the old string column
    op.drop_column("admin_config", "signup_credit_mode")


def downgrade() -> None:
    op.add_column(
        "admin_config",
        sa.Column("signup_credit_mode", sa.String(32), nullable=True),
    )
    conn = op.get_bind()
    conn.execute(sa.text(
        "UPDATE admin_config "
        "SET signup_credit_mode = CASE "
        "  WHEN initial_credits >= 10 THEN 'beta' "
        "  ELSE 'normal' "
        "END"
    ))
    op.alter_column("admin_config", "signup_credit_mode", nullable=False,
                    server_default="normal")
    op.drop_column("admin_config", "initial_credits")
