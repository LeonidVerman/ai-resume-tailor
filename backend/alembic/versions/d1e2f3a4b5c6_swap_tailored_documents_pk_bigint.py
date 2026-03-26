"""swap tailored_documents PK to bigint (swap step)

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-03-26

Swaps UUID PK to bigint:
  tailored_documents.id (uuid) → uuid_id (nullable, for rollback)
  tailored_documents.new_id (bigint) → id (new PK)

API contract changes:
  - GET /documents/{doc_id}: doc_id is now an integer path param
  - TailoredDocumentSummary.id: number (was string UUID)
  - TailoredDocumentDetail.id: number (was string UUID)
  - GenerationRunSummary.tailored_document_id: number | null (was string UUID)
  - GenerationResponse.tailored_document_id: number | null (was string UUID)
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop old UUID primary key
    op.drop_constraint("tailored_documents_pkey", "tailored_documents", type_="primary")

    # Rename id → uuid_id
    op.alter_column("tailored_documents", "id", new_column_name="uuid_id")

    # Rename new_id → id
    op.alter_column("tailored_documents", "new_id", new_column_name="id")

    # Drop the unique constraint (now PK)
    op.drop_constraint("uq_tailored_documents_new_id", "tailored_documents", type_="unique")

    # Create bigint primary key
    op.create_primary_key("tailored_documents_pkey", "tailored_documents", ["id"])

    # Make uuid_id nullable
    op.alter_column("tailored_documents", "uuid_id", nullable=True)


def downgrade() -> None:
    op.drop_constraint("tailored_documents_pkey", "tailored_documents", type_="primary")
    op.alter_column("tailored_documents", "id", new_column_name="new_id")
    op.alter_column("tailored_documents", "uuid_id", new_column_name="id")
    op.create_unique_constraint("uq_tailored_documents_new_id", "tailored_documents", ["new_id"])
    op.create_primary_key("tailored_documents_pkey", "tailored_documents", ["id"])
    op.drop_column("tailored_documents", "new_id")
