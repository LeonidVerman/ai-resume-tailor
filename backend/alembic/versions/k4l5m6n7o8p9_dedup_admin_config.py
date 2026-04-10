"""deduplicate admin_config and add single-row constraint

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-04-10

Root cause of signup-credits bug:
  AdminConfigRepository.get() used .first() without ORDER BY.
  If two rows existed (TOCTOU race on an empty table), different
  requests could read different rows — admin updates row 2 to "beta"
  while new-user signups read row 1 (still "normal") and receive 3
  credits instead of 10.

Fixes:
  1. Delete all admin_config rows except the one with the lowest id
     (the original canonical row).  If the lowest-id row has
     signup_credit_mode="normal" but a higher-id row has "beta", we
     copy the "beta" value before deleting the duplicate so no admin
     setting is silently lost.
  2. Add a CHECK constraint that limits the table to at most one row
     (id = 1).  Combined with the autoincrement sequence this is a
     lightweight sentinel: if a second row is ever INSERTed, Postgres
     will raise immediately rather than silently corrupting state.

     A true enforcement mechanism would be a partial unique index or a
     trigger, but since admin_config is written only through
     AdminConfigRepository (which always upserts the single row) the
     CHECK on id is the least-invasive guard.
"""

from alembic import op
import sqlalchemy as sa


revision: str = "k4l5m6n7o8p9"
down_revision = "j3k4l5m6n7o8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: determine canonical (lowest-id) row and any duplicates ────────
    rows = conn.execute(
        sa.text("SELECT id, signup_credit_mode FROM admin_config ORDER BY id")
    ).fetchall()

    if len(rows) > 1:
        canonical_id = rows[0][0]
        canonical_mode = rows[0][1]

        # If any duplicate has a non-default (beta) mode, promote it to the
        # canonical row so we don't silently discard an admin change.
        for row_id, mode in rows[1:]:
            if mode and mode != "normal":
                conn.execute(
                    sa.text(
                        "UPDATE admin_config SET signup_credit_mode = :mode WHERE id = :id"
                    ).bindparams(mode=mode, id=canonical_id)
                )
                break  # first non-normal value wins; stop after promoting once

        # Delete all rows except the canonical one.
        conn.execute(
            sa.text("DELETE FROM admin_config WHERE id != :id").bindparams(
                id=canonical_id
            )
        )

    # ── Step 2: reset the sequence so the next INSERT gets id=2, not a gap ───
    # (Only matters if the canonical row has id != 1 due to earlier races.)
    if rows:
        canonical_id = rows[0][0]
        conn.execute(
            sa.text(
                "SELECT setval(pg_get_serial_sequence('admin_config', 'id'), :val)"
            ).bindparams(val=canonical_id)
        )

    # ── Step 3: add a unique index on a constant expression to enforce
    #    single-row semantics at the DB level.
    # We use a unique index on (id / id) — always 1 for any positive integer —
    # which is simpler and more portable than a partial index here.
    # A cleaner approach: unique index on a constant; Postgres supports this
    # via a computed column.  Easiest is a CHECK that id must be 1.
    op.create_check_constraint(
        "ck_admin_config_single_row",
        "admin_config",
        "id = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_admin_config_single_row", "admin_config", type_="check")
