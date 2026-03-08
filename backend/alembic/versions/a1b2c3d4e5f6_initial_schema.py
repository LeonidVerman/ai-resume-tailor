"""initial schema

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-03-07

Creates all MVP tables:
  users, candidate_profiles, structured_resumes, job_descriptions,
  generation_runs, tailored_documents, evaluation_runs, billing
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("plan_type", sa.String(32), nullable=False, server_default="free"),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("free_generations_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── candidate_profiles ────────────────────────────────────────────────
    op.create_table(
        "candidate_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_version", sa.String(32), nullable=False, server_default="1"),
        sa.Column(
            "profile_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
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
    op.create_index("ix_candidate_profiles_user_id", "candidate_profiles", ["user_id"])

    # ── structured_resumes ────────────────────────────────────────────────
    op.create_table(
        "structured_resumes",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "resume_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("source_file_url", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_structured_resumes_user_id", "structured_resumes", ["user_id"])

    # ── job_descriptions ──────────────────────────────────────────────────
    op.create_table(
        "job_descriptions",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column(
            "parsed_metadata_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_job_descriptions_user_id", "job_descriptions", ["user_id"])

    # ── generation_runs ───────────────────────────────────────────────────
    op.create_table(
        "generation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_description_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("job_descriptions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("run_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("model_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "input_snapshot_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("raw_response", sa.Text, nullable=True),
        sa.Column(
            "parsed_output_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("token_input", sa.Integer, nullable=True),
        sa.Column("token_output", sa.Integer, nullable=True),
        sa.Column("cost_estimate", sa.Numeric(10, 6), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text, nullable=True),
    )
    op.create_index("ix_generation_runs_user_id", "generation_runs", ["user_id"])
    op.create_index(
        "ix_generation_runs_job_description_id",
        "generation_runs",
        ["job_description_id"],
    )

    # ── tailored_documents ────────────────────────────────────────────────
    op.create_table(
        "tailored_documents",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("company_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("role_title", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "resume_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "cover_letter_jsonb",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("resume_docx_url", sa.Text, nullable=True),
        sa.Column("resume_pdf_url", sa.Text, nullable=True),
        sa.Column("cover_letter_docx_url", sa.Text, nullable=True),
        sa.Column("cover_letter_pdf_url", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_tailored_documents_user_id", "tailored_documents", ["user_id"])
    op.create_index(
        "ix_tailored_documents_generation_run_id",
        "tailored_documents",
        ["generation_run_id"],
    )

    # ── evaluation_runs ───────────────────────────────────────────────────
    op.create_table(
        "evaluation_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "generation_run_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("generation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("truthfulness_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("role_fit_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("clarity_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("seniority_score", sa.Numeric(5, 4), nullable=True),
        sa.Column("integrated_score", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_evaluation_runs_generation_run_id",
        "evaluation_runs",
        ["generation_run_id"],
    )

    # ── billing ───────────────────────────────────────────────────────────
    op.create_table(
        "billing",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("stripe_customer_id", sa.String(255), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(255), nullable=True),
        sa.Column("subscription_status", sa.String(64), nullable=True),
        sa.Column("plan_type", sa.String(32), nullable=False, server_default="free"),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_billing_user_id", "billing", ["user_id"], unique=True)


def downgrade() -> None:
    op.drop_table("billing")
    op.drop_table("evaluation_runs")
    op.drop_table("tailored_documents")
    op.drop_table("generation_runs")
    op.drop_table("job_descriptions")
    op.drop_table("structured_resumes")
    op.drop_table("candidate_profiles")
    op.drop_table("users")
