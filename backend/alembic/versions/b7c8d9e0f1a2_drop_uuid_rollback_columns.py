"""drop UUID rollback columns from all migrated tables

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-03-26

Removes all uuid_id / *_uuid_id columns that were kept for rollback
after the UUID→bigint PK migration.  Safe to run once the new bigint
PKs/FKs have been verified in production.

Columns dropped (15 total):
  admin_config            : uuid_id
  benchmark_run_positions : uuid_id, benchmark_run_uuid_id
  benchmark_runs          : uuid_id
  billing                 : uuid_id
  candidate_profiles      : uuid_id
  evaluation_runs         : uuid_id, generation_run_uuid_id
  generation_runs         : uuid_id, job_description_uuid_id
  job_descriptions        : uuid_id
  monthly_usage           : uuid_id
  structured_resumes      : uuid_id
  tailored_documents      : uuid_id, generation_run_uuid_id
"""

from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None

# (table, column) pairs — order doesn't matter, all are plain nullable columns
_DROPS = [
    ("admin_config",            "uuid_id"),
    ("benchmark_run_positions", "uuid_id"),
    ("benchmark_run_positions", "benchmark_run_uuid_id"),
    ("benchmark_runs",          "uuid_id"),
    ("billing",                 "uuid_id"),
    ("candidate_profiles",      "uuid_id"),
    ("evaluation_runs",         "uuid_id"),
    ("evaluation_runs",         "generation_run_uuid_id"),
    ("generation_runs",         "uuid_id"),
    ("generation_runs",         "job_description_uuid_id"),
    ("job_descriptions",        "uuid_id"),
    ("monthly_usage",           "uuid_id"),
    ("structured_resumes",      "uuid_id"),
    ("tailored_documents",      "uuid_id"),
    ("tailored_documents",      "generation_run_uuid_id"),
]


def upgrade() -> None:
    for table, column in _DROPS:
        op.drop_column(table, column)


def downgrade() -> None:
    # Rollback columns are intentionally not restored — the data is gone.
    # To recover: restore from a backup taken before this migration ran.
    pass
