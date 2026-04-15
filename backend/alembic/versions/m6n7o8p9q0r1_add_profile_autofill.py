"""add candidate_profile_resume_drafts table and source_resume_id on candidate_profiles

Revision ID: m6n7o8p9q0r1
Revises: l5m6n7o8p9q0
Create Date: 2026-04-12
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = "m6n7o8p9q0r1"
down_revision = "l5m6n7o8p9q0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_profile_resume_drafts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "resume_id",
            sa.BigInteger,
            sa.ForeignKey("structured_resumes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("draft_jsonb", JSONB, nullable=False),
        sa.Column("resume_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("user_id", "resume_id", name="uq_profile_autofill_user_resume"),
    )

    op.add_column(
        "candidate_profiles",
        sa.Column(
            "source_resume_id",
            sa.BigInteger,
            sa.ForeignKey("structured_resumes.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_profiles", "source_resume_id")
    op.drop_table("candidate_profile_resume_drafts")
