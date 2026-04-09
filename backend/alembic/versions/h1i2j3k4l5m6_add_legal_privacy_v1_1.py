"""publish legal documents privacy_notice v1.1

Revision ID: h1i2j3k4l5m6
Revises: g0h1i2j3k4l5
Create Date: 2026-04-09

Changes:
  - Inserts privacy_notice v1.1 (requires re-acceptance)
  - Marks privacy_notice v1.0 as superseded
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

# ── Helpers ───────────────────────────────────────────────────────────────────

def _canonicalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip() + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_legal_file(repo_relative_path: str) -> str:
    repo_root = Path(__file__).parents[3]  # backend/alembic/versions/ → repo root
    content = (repo_root / repo_relative_path).read_text(encoding="utf-8")
    return _sha256(_canonicalize(content))


# ── Revision info ─────────────────────────────────────────────────────────────

revision: str = "h1i2j3k4l5m6"
down_revision = "g0h1i2j3k4l5"
branch_labels = None
depends_on = None

EFFECTIVE_DATE = datetime(2026, 4, 9, 0, 0, 0, tzinfo=timezone.utc)

_legal_documents = sa.table(
    "legal_documents",
    sa.column("doc_type", sa.String),
    sa.column("version", sa.String),
    sa.column("title", sa.String),
    sa.column("file_path", sa.String),
    sa.column("content_sha256", sa.String),
    sa.column("effective_at", sa.DateTime(timezone=True)),
    sa.column("status", sa.String),
    sa.column("requires_reaccept", sa.Boolean),
    sa.column("notes", sa.Text),
)


def upgrade() -> None:
    privacy_v11_hash = _hash_legal_file("legal/privacy/v1.1.md")

    # Insert v1.1 privacy row
    op.bulk_insert(
        _legal_documents,
        [
            {
                "doc_type": "privacy_notice",
                "version": "v1.1",
                "title": "Privacy Notice",
                "file_path": "legal/privacy/v1.1.md",
                "content_sha256": privacy_v11_hash,
                "effective_at": EFFECTIVE_DATE,
                "status": "active",
                "requires_reaccept": True,
                "notes": "Updated Privacy Notice with CVRocket branding and finalized content.",
            },
        ],
    )

    # Supersede v1.0 privacy
    op.execute(
        "UPDATE legal_documents SET status = 'superseded' "
        "WHERE doc_type = 'privacy_notice' AND version = 'v1.0'"
    )

    # Reset user_legal_status so all users must re-accept privacy on next login
    op.execute(
        "UPDATE user_legal_status SET accepted_privacy_document_id = NULL, accepted_privacy_at = NULL"
    )


def downgrade() -> None:
    # Re-activate v1.0
    op.execute(
        "UPDATE legal_documents SET status = 'active' "
        "WHERE doc_type = 'privacy_notice' AND version = 'v1.0'"
    )

    # Remove v1.1
    op.execute(
        "DELETE FROM legal_documents "
        "WHERE doc_type = 'privacy_notice' AND version = 'v1.1'"
    )
