"""add soft delete flag to structured_resumes and job_descriptions

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-03-10

Adds delete_flg BOOLEAN NOT NULL DEFAULT FALSE to both tables so that
UI-triggered deletions can be implemented as soft deletes.  Existing
rows default to false (not deleted) — no data is hidden after migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "structured_resumes",
        sa.Column(
            "delete_flg",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "job_descriptions",
        sa.Column(
            "delete_flg",
            sa.Boolean,
            nullable=False,
            server_default="false",
        ),
    )


def downgrade() -> None:
    op.drop_column("structured_resumes", "delete_flg")
    op.drop_column("job_descriptions", "delete_flg")
