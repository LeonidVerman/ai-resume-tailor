"""add candidate_prompt and prompt_synched to candidate_profiles

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-03-11

Adds lazy-cached candidate-layer prompt storage to candidate_profiles:
  - candidate_prompt: generated candidate layer text, nullable (NULL = not yet generated)
  - prompt_synched:   false when profile data has changed and prompt needs regeneration
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_profiles",
        sa.Column("candidate_prompt", sa.Text, nullable=True),
    )
    op.add_column(
        "candidate_profiles",
        sa.Column(
            "prompt_synched",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Existing rows: candidate_prompt stays NULL, prompt_synched defaults to false
    # via server_default — no explicit UPDATE needed.


def downgrade() -> None:
    op.drop_column("candidate_profiles", "prompt_synched")
    op.drop_column("candidate_profiles", "candidate_prompt")
