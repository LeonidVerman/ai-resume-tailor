"""add classification_jsonb to structured_resumes

Revision ID: n7o8p9q0r1s2
Revises: m6n7o8p9q0r1
Create Date: 2026-04-15
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision: str = "n7o8p9q0r1s2"
down_revision = "m6n7o8p9q0r1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "structured_resumes",
        sa.Column("classification_jsonb", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("structured_resumes", "classification_jsonb")
