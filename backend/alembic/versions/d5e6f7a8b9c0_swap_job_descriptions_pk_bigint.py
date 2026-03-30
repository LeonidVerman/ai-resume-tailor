"""swap job_descriptions PK to bigint (swap step)

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-03-26

Prerequisite: c4d5e6f7a8b9 must be applied AND the following verified:
  SELECT COUNT(*) FROM generation_runs WHERE job_description_id IS NOT NULL AND new_jd_id IS NULL;
  -- must return 0

Changes
-------
job_descriptions:
  - drops UUID primary key
  - renames: id → uuid_id  (preserved for rollback)
  - renames: new_id → id
  - adds:    PRIMARY KEY (id)

generation_runs:
  - drops FK constraint on job_description_id (UUID)
  - drops index ix_generation_runs_job_description_id
  - renames: job_description_id → job_description_uuid_id
  - renames: new_jd_id → job_description_id
  - adds:    FK job_description_id → job_descriptions.id (bigint) ON DELETE SET NULL
  - adds:    index ix_generation_runs_job_description_id on new job_description_id

API contract change
-------------------
job_descriptions.id changes from UUID string to bigint integer.
Affected endpoints:
  GET  /job-descriptions/{id}   — path param now int
  DELETE /job-descriptions/{id} — path param now int
  GET  /job-descriptions        — response field id: int
  POST /job-descriptions/*      — response field id: int
  POST /generations             — request body job_description_id: int
  GET  /generations/{id}        — response field job_description_id: int | null
"""

from typing import Sequence, Union

from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── generation_runs: drop old FK + index before renaming columns ────────
    op.drop_constraint(
        "generation_runs_job_description_id_fkey",
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_generation_runs_job_description_id", table_name="generation_runs")

    # ── job_descriptions: drop UUID PK, rename columns, add bigint PK ───────
    op.drop_constraint("job_descriptions_pkey", "job_descriptions", type_="primary")
    op.drop_constraint("uq_job_descriptions_new_id", "job_descriptions")
    op.execute("ALTER TABLE job_descriptions RENAME COLUMN id TO uuid_id")
    op.execute("ALTER TABLE job_descriptions RENAME COLUMN new_id TO id")
    op.create_primary_key("job_descriptions_pkey", "job_descriptions", ["id"])

    # ── generation_runs: rename FK columns, add bigint FK + index ───────────
    op.execute(
        "ALTER TABLE generation_runs RENAME COLUMN job_description_id TO job_description_uuid_id"
    )
    op.execute(
        "ALTER TABLE generation_runs RENAME COLUMN new_jd_id TO job_description_id"
    )
    op.create_foreign_key(
        "generation_runs_job_description_id_fkey",
        "generation_runs",
        "job_descriptions",
        ["job_description_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_runs_job_description_id",
        "generation_runs",
        ["job_description_id"],
    )
    op.alter_column("job_descriptions", "uuid_id", nullable=True)


def downgrade() -> None:
    # Reverse the swap — restores UUID PK on job_descriptions.
    op.drop_constraint(
        "generation_runs_job_description_id_fkey",
        "generation_runs",
        type_="foreignkey",
    )
    op.drop_index("ix_generation_runs_job_description_id", table_name="generation_runs")

    op.execute(
        "ALTER TABLE generation_runs RENAME COLUMN job_description_id TO new_jd_id"
    )
    op.execute(
        "ALTER TABLE generation_runs RENAME COLUMN job_description_uuid_id TO job_description_id"
    )

    op.drop_constraint("job_descriptions_pkey", "job_descriptions", type_="primary")
    op.execute("ALTER TABLE job_descriptions RENAME COLUMN id TO new_id")
    op.execute("ALTER TABLE job_descriptions RENAME COLUMN uuid_id TO id")
    op.create_primary_key("job_descriptions_pkey", "job_descriptions", ["id"])
    op.create_unique_constraint(
        "uq_job_descriptions_new_id", "job_descriptions", ["new_id"]
    )

    op.create_foreign_key(
        "generation_runs_job_description_id_fkey",
        "generation_runs",
        "job_descriptions",
        ["job_description_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_generation_runs_job_description_id",
        "generation_runs",
        ["job_description_id"],
    )
    op.drop_column("generation_runs", "new_jd_id")
    op.drop_column("job_descriptions", "new_id")
