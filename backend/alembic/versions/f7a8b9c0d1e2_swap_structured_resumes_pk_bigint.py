"""swap structured_resumes PK to bigint (swap step)

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-03-26

Prerequisite: e6f7a8b9c0d1 must be applied.
No FK child tables reference structured_resumes.id, so no FK migration needed.

Changes
-------
structured_resumes:
  - drops UUID primary key
  - renames: id → uuid_id  (preserved for rollback)
  - renames: new_id → id
  - adds:    PRIMARY KEY (id)

API contract change
-------------------
structured_resumes.id changes from UUID string to bigint integer.
Affected endpoints:
  GET  /resumes/{id}            — path param now int
  DELETE /resumes/{id}          — path param now int
  GET  /resumes                 — response field id: int
  POST /resumes/upload          — response field id: int
  POST /generations             — request body structured_resume_id: int
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("structured_resumes_pkey", "structured_resumes", type_="primary")
    op.drop_constraint("uq_structured_resumes_new_id", "structured_resumes")
    op.execute("ALTER TABLE structured_resumes RENAME COLUMN id TO uuid_id")
    op.execute("ALTER TABLE structured_resumes RENAME COLUMN new_id TO id")
    op.create_primary_key("structured_resumes_pkey", "structured_resumes", ["id"])
    op.alter_column("structured_resumes", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("structured_resumes_pkey", "structured_resumes", type_="primary")
    op.execute("ALTER TABLE structured_resumes RENAME COLUMN id TO new_id")
    op.execute("ALTER TABLE structured_resumes RENAME COLUMN uuid_id TO id")
    op.create_primary_key("structured_resumes_pkey", "structured_resumes", ["id"])
    op.create_unique_constraint(
        "uq_structured_resumes_new_id", "structured_resumes", ["new_id"]
    )
    op.drop_column("structured_resumes", "new_id")
