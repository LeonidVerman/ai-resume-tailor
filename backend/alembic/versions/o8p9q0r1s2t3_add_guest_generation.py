"""add guest generation tables and columns (issue #155)

Revision ID: o8p9q0r1s2t3
Revises: n7o8p9q0r1s2
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID
from alembic import op

revision: str = "o8p9q0r1s2t3"
down_revision = "n7o8p9q0r1s2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # users — guest identity flags
    op.add_column(
        "users",
        sa.Column("is_anonymous", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "users",
        sa.Column("guest_claimed_by", UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("guest_purged_at", sa.DateTime(timezone=True), nullable=True),
    )

    # candidate_profiles — auto-accepted, unreviewed guest profile marker
    op.add_column(
        "candidate_profiles",
        sa.Column("is_unreviewed", sa.Boolean(), nullable=False, server_default="false"),
    )

    # structured_resumes — upload checksum (all uploads, not only guests)
    op.add_column(
        "structured_resumes",
        sa.Column("sha256", sa.String(64), nullable=True),
    )

    # admin_config — guest feature controls
    op.add_column(
        "admin_config",
        sa.Column("guest_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "admin_config",
        sa.Column("guest_daily_global_cap", sa.Integer(), nullable=False, server_default="25"),
    )
    op.add_column(
        "admin_config",
        sa.Column("guest_concurrent_cap", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "admin_config",
        sa.Column("guest_ip_daily_limit", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "admin_config",
        sa.Column("guest_retention_days", sa.Integer(), nullable=False, server_default="7"),
    )

    # guest_entitlements — single source of truth for guest usage
    op.create_table(
        "guest_entitlements",
        sa.Column(
            "user_id",
            UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("allowed", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # guest_devices — signed device token hashes
    op.create_table(
        "guest_devices",
        sa.Column("device_token_hash", sa.String(64), primary_key=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("guest_user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    # guest_abuse_events — append-only abuse/limit log (daily HMAC IP hash)
    op.create_table(
        "guest_abuse_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("ip_daily_hash", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("risk_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_guest_abuse_events_ip_created",
        "guest_abuse_events",
        ["ip_daily_hash", "created_at"],
    )

    # funnel_events — /try product funnel
    op.create_table(
        "funnel_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("user_id", UUID(as_uuid=False), nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("meta", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index(
        "ix_funnel_events_type_created",
        "funnel_events",
        ["event_type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_funnel_events_type_created", table_name="funnel_events")
    op.drop_table("funnel_events")
    op.drop_index("ix_guest_abuse_events_ip_created", table_name="guest_abuse_events")
    op.drop_table("guest_abuse_events")
    op.drop_table("guest_devices")
    op.drop_table("guest_entitlements")
    op.drop_column("admin_config", "guest_retention_days")
    op.drop_column("admin_config", "guest_ip_daily_limit")
    op.drop_column("admin_config", "guest_concurrent_cap")
    op.drop_column("admin_config", "guest_daily_global_cap")
    op.drop_column("admin_config", "guest_enabled")
    op.drop_column("structured_resumes", "sha256")
    op.drop_column("candidate_profiles", "is_unreviewed")
    op.drop_column("users", "guest_purged_at")
    op.drop_column("users", "guest_claimed_by")
    op.drop_column("users", "is_anonymous")
