"""swap generation_runs PK to bigint (swap step)

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-03-26

Swaps UUID PK to bigint for generation_runs; updates FKs in child tables.

  generation_runs.id (uuid) → uuid_id (nullable, for rollback)
  generation_runs.new_id (bigint) → id (new PK)

  tailored_documents.generation_run_id (uuid FK) → generation_run_uuid_id (nullable)
  tailored_documents.new_generation_run_id (bigint) → generation_run_id (new bigint FK)

  evaluation_runs.generation_run_id (uuid FK) → generation_run_uuid_id (nullable)
  evaluation_runs.new_generation_run_id (bigint) → generation_run_id (new bigint FK)

API contract changes:
  - GET/DELETE /generations/{run_id}: run_id is now an integer path param
  - GenerationRunSummary.id: number (was string UUID)
  - GenerationResponse.run_id: number (was string UUID)
  - TailoredDocumentSummary/Detail.generation_run_id: number (was string UUID)
  - EvaluationResponse.generation_run_id: number (was string UUID)
  - POST /admin/evaluate-run body generation_run_id: number (was string UUID)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── tailored_documents: drop UUID FK, rename columns ──────────────────
    op.drop_constraint("tailored_documents_generation_run_id_fkey", "tailored_documents", type_="foreignkey")
    op.alter_column("tailored_documents", "generation_run_id", new_column_name="generation_run_uuid_id")
    op.alter_column("tailored_documents", "new_generation_run_id", new_column_name="generation_run_id")
    op.alter_column("tailored_documents", "generation_run_uuid_id", nullable=True)

    # ── evaluation_runs: drop UUID FK, rename columns ─────────────────────
    op.drop_constraint("evaluation_runs_generation_run_id_fkey", "evaluation_runs", type_="foreignkey")
    op.alter_column("evaluation_runs", "generation_run_id", new_column_name="generation_run_uuid_id")
    op.alter_column("evaluation_runs", "new_generation_run_id", new_column_name="generation_run_id")
    op.alter_column("evaluation_runs", "generation_run_uuid_id", nullable=True)

    # ── generation_runs: drop old UUID PK, swap columns ───────────────────
    op.drop_constraint("generation_runs_pkey", "generation_runs", type_="primary")
    op.alter_column("generation_runs", "id", new_column_name="uuid_id")
    op.alter_column("generation_runs", "new_id", new_column_name="id")
    op.drop_constraint("uq_generation_runs_new_id", "generation_runs", type_="unique")
    op.create_primary_key("generation_runs_pkey", "generation_runs", ["id"])
    op.alter_column("generation_runs", "uuid_id", nullable=True)

    # ── Create new bigint FK constraints ──────────────────────────────────
    op.create_foreign_key(
        "tailored_documents_generation_run_id_fkey",
        "tailored_documents", "generation_runs",
        ["generation_run_id"], ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "evaluation_runs_generation_run_id_fkey",
        "evaluation_runs", "generation_runs",
        ["generation_run_id"], ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint("evaluation_runs_generation_run_id_fkey", "evaluation_runs", type_="foreignkey")
    op.drop_constraint("tailored_documents_generation_run_id_fkey", "tailored_documents", type_="foreignkey")

    op.drop_constraint("generation_runs_pkey", "generation_runs", type_="primary")
    op.alter_column("generation_runs", "id", new_column_name="new_id")
    op.alter_column("generation_runs", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_generation_runs_new_id", "generation_runs", ["new_id"])
    op.create_primary_key("generation_runs_pkey", "generation_runs", ["id"])
    op.drop_column("generation_runs", "new_id")

    op.alter_column("evaluation_runs", "generation_run_id", new_column_name="new_generation_run_id")
    op.alter_column("evaluation_runs", "generation_run_uuid_id", new_column_name="generation_run_id")
    op.drop_column("evaluation_runs", "new_generation_run_id")

    op.alter_column("tailored_documents", "generation_run_id", new_column_name="new_generation_run_id")
    op.alter_column("tailored_documents", "generation_run_uuid_id", new_column_name="generation_run_id")
    op.drop_column("tailored_documents", "new_generation_run_id")
