"""add legal consent tables

Revision ID: b0c1d2e3f4a5
Revises: f9a0b1c2d3e4
Create Date: 2026-03-28

Creates:
  - legal_documents         (version registry — immutable after publish)
  - legal_acceptance_events (append-only acceptance log)
  - user_legal_status       (convenience cache)

Seeds v1.0 rows for terms_of_service and privacy_notice.
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

# ── Helpers ──────────────────────────────────────────────────────────────────

def _canonicalize(text: str) -> str:
    """Normalize line endings, trim trailing whitespace per line."""
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

revision: str = "b0c1d2e3f4a5"
down_revision = "f9a0b1c2d3e4"
branch_labels = None
depends_on = None

EFFECTIVE_DATE = datetime(2026, 3, 28, 0, 0, 0, tzinfo=timezone.utc)


def upgrade() -> None:
    # ── legal_documents ───────────────────────────────────────────────────────
    op.create_table(
        "legal_documents",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("file_path", sa.Text, nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("requires_reaccept", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("doc_type", "version", name="uq_legal_documents_type_version"),
    )

    # ── legal_acceptance_events ───────────────────────────────────────────────
    op.create_table(
        "legal_acceptance_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("user_id", PG_UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("doc_type", sa.String(50), nullable=False),
        sa.Column("legal_document_id", sa.BigInteger, sa.ForeignKey("legal_documents.id"), nullable=False),
        sa.Column("version", sa.String(20), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("acceptance_method", sa.String(50), nullable=False),
        sa.Column("source_surface", sa.String(50), nullable=False),
        sa.Column("ip_address", sa.String(45), nullable=True),  # IPv4 or IPv6
        sa.Column("user_agent", sa.Text, nullable=True),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_legal_acceptance_events_user_id", "legal_acceptance_events", ["user_id"])

    # ── user_legal_status ─────────────────────────────────────────────────────
    op.create_table(
        "user_legal_status",
        sa.Column("user_id", PG_UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("accepted_terms_document_id", sa.BigInteger, sa.ForeignKey("legal_documents.id"), nullable=True),
        sa.Column("accepted_privacy_document_id", sa.BigInteger, sa.ForeignKey("legal_documents.id"), nullable=True),
        sa.Column("accepted_terms_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_privacy_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # ── Seed v1.0 documents ───────────────────────────────────────────────────
    terms_hash = _hash_legal_file("legal/terms/v1.0.md")
    privacy_hash = _hash_legal_file("legal/privacy/v1.0.md")

    op.bulk_insert(
        sa.table(
            "legal_documents",
            sa.column("doc_type", sa.String),
            sa.column("version", sa.String),
            sa.column("title", sa.String),
            sa.column("file_path", sa.String),
            sa.column("content_sha256", sa.String),
            sa.column("effective_at", sa.DateTime(timezone=True)),
            sa.column("status", sa.String),
            sa.column("requires_reaccept", sa.Boolean),
        ),
        [
            {
                "doc_type": "terms_of_service",
                "version": "v1.0",
                "title": "Terms of Service",
                "file_path": "legal/terms/v1.0.md",
                "content_sha256": terms_hash,
                "effective_at": EFFECTIVE_DATE,
                "status": "active",
                "requires_reaccept": True,
            },
            {
                "doc_type": "privacy_notice",
                "version": "v1.0",
                "title": "Privacy Notice",
                "file_path": "legal/privacy/v1.0.md",
                "content_sha256": privacy_hash,
                "effective_at": EFFECTIVE_DATE,
                "status": "active",
                "requires_reaccept": True,
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("user_legal_status")
    op.drop_index("ix_legal_acceptance_events_user_id", table_name="legal_acceptance_events")
    op.drop_table("legal_acceptance_events")
    op.drop_table("legal_documents")
