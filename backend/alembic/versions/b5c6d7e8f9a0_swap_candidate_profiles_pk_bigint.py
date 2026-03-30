"""swap candidate_profiles PK to bigint (swap step)

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-03-26

Swaps UUID PK to bigint:
  candidate_profiles.id (uuid) → uuid_id (nullable, for rollback)
  candidate_profiles.new_id (bigint) → id (new PK)

API contract changes:
  - CandidateProfileResponse.id: number (was string UUID)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "b5c6d7e8f9a0"
down_revision = "a4b5c6d7e8f9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("candidate_profiles_pkey", "candidate_profiles", type_="primary")
    op.alter_column("candidate_profiles", "id", new_column_name="uuid_id")
    op.alter_column("candidate_profiles", "new_id", new_column_name="id")
    op.drop_constraint("uq_candidate_profiles_new_id", "candidate_profiles", type_="unique")
    op.create_primary_key("candidate_profiles_pkey", "candidate_profiles", ["id"])
    op.alter_column("candidate_profiles", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("candidate_profiles_pkey", "candidate_profiles", type_="primary")
    op.alter_column("candidate_profiles", "id", new_column_name="new_id")
    op.alter_column("candidate_profiles", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_candidate_profiles_new_id", "candidate_profiles", ["new_id"])
    op.create_primary_key("candidate_profiles_pkey", "candidate_profiles", ["id"])
    op.drop_column("candidate_profiles", "new_id")
