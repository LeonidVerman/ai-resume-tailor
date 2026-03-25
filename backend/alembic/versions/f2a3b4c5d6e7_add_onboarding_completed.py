"""add onboarding_completed to candidate_profiles

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-03-24

Adds onboarding state to candidate_profiles:
  - onboarding_completed: tracks whether the user finished the onboarding wizard
  - onboarding_completed_at: timestamp when onboarding was completed (nullable)

Backfill: existing profiles are treated as complete (onboarding_completed = true)
so that existing users are unaffected by the new onboarding gate.
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "onboarding_completed",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Backfill: existing profiles belong to users who signed up before
    # this feature; treat them as already onboarded.
    op.execute(
        "UPDATE candidate_profiles SET onboarding_completed = true, "
        "onboarding_completed_at = NOW()"
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "onboarding_completed_at")
    op.drop_column("candidate_profiles", "onboarding_completed")
