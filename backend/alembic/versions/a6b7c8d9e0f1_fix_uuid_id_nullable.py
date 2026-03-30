"""fix uuid_id NOT NULL on job_descriptions and structured_resumes

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-03-26

job_descriptions.uuid_id and structured_resumes.uuid_id were renamed from the
original PK column (which was NOT NULL) but never had SET NULL applied.  Any
INSERT after migration fails with a NOT NULL violation.

Also patch the two swap migrations so future fresh installs are correct.
"""

from alembic import op

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("job_descriptions", "uuid_id", nullable=True)
    op.alter_column("structured_resumes", "uuid_id", nullable=True)


def downgrade() -> None:
    # Rows created after migration have uuid_id=NULL; downgrade would fail if
    # any such rows exist, so this is intentionally a no-op.
    pass
