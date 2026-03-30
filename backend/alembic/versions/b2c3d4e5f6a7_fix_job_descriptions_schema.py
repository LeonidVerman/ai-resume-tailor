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
    # The initial migration was later corrected to use the final column names
    # directly, so on a fresh DB both operations below are already satisfied.
    # Guard each one so the migration is idempotent whether run against an old
    # DB (pre-correction) or a fresh one.

    # Rename parsed_metadata_jsonb → metadata_jsonb if the old name still exists.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'job_descriptions'
                  AND column_name = 'parsed_metadata_jsonb'
            ) THEN
                ALTER TABLE job_descriptions
                    RENAME COLUMN parsed_metadata_jsonb TO metadata_jsonb;
            END IF;
        END $$;
    """)

    # Add source_type only if it does not already exist.
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'job_descriptions'
                  AND column_name = 'source_type'
            ) THEN
                ALTER TABLE job_descriptions
                    ADD COLUMN source_type VARCHAR(32);
            END IF;
        END $$;
    """)


def downgrade() -> None:
    op.drop_column("job_descriptions", "source_type")
    op.alter_column(
        "job_descriptions",
        "metadata_jsonb",
        new_column_name="parsed_metadata_jsonb",
    )
