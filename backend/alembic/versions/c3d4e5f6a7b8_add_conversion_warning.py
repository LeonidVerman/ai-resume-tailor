"""add input_conversion_warning to structured_resumes

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-10

Adds a nullable text column to structured_resumes that stores a user-visible
disclaimer when the uploaded file was converted from PDF to DOCX via
LibreOffice.  Existing rows default to NULL (no conversion performed).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "structured_resumes",
        sa.Column("input_conversion_warning", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("structured_resumes", "input_conversion_warning")
