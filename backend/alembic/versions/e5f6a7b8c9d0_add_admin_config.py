"""add admin_config table for generation settings

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-10

Single-row settings table storing admin-selected generation mode and
model names.  Defaults: mode=simple, simple_model=gpt-5.2,
phase1_model=gpt-4.1, phase2_model=gpt-4.1.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "admin_config",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "generation_mode",
            sa.String(32),
            nullable=False,
            server_default="simple",
        ),
        sa.Column(
            "simple_model",
            sa.String(128),
            nullable=False,
            server_default="gpt-5.2",
        ),
        sa.Column(
            "phase1_model",
            sa.String(128),
            nullable=False,
            server_default="gpt-4.1",
        ),
        sa.Column(
            "phase2_model",
            sa.String(128),
            nullable=False,
            server_default="gpt-4.1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("admin_config")
