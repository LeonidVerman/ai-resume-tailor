"""fix job_descriptions schema: add source_type, rename parsed_metadata_jsonb

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-09

The initial migration was committed with two errors in job_descriptions:
  1. Column named parsed_metadata_jsonb instead of metadata_jsonb
  2. Missing source_type column

This migration corrects both so the table matches the ORM model.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Rename parsed_metadata_jsonb → metadata_jsonb
    op.alter_column(
        "job_descriptions",
        "parsed_metadata_jsonb",
        new_column_name="metadata_jsonb",
    )
    # Add missing source_type column
    op.add_column(
        "job_descriptions",
        sa.Column("source_type", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_descriptions", "source_type")
    op.alter_column(
        "job_descriptions",
        "metadata_jsonb",
        new_column_name="parsed_metadata_jsonb",
    )
