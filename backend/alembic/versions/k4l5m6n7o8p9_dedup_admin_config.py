"""deduplicate admin_config and enforce single-row id=1

Revision ID: k4l5m6n7o8p9
Revises: j3k4l5m6n7o8
Create Date: 2026-04-11

Root cause of signup-credits bug
---------------------------------
AdminConfigRepository.get() used .first() without ORDER BY.
PostgreSQL makes no ordering guarantee for un-ordered queries.
If the admin_config table had more than one row (possible via a
TOCTOU race on an empty table — two concurrent requests both see
NULL and both INSERT a new row with default signup_credit_mode='normal'),
different requests could read different rows:

  Row 1 (id=1): signup_credit_mode='normal'  ← what registration reads
  Row 2 (id=2): signup_credit_mode='beta'    ← what admin's PUT updated

New users received 3 credits (normal) instead of 10 (beta).

Fixes applied in code
---------------------
1. AdminConfigRepository.get() now filters by id=1 so it always reads
   and writes the canonical row — no ORDER BY ambiguity.
2. Creation path now explicitly sets id=1 so the PK acts as a
   deduplication guard for concurrent INSERTs.
3. get_session_factory() is now @lru_cache'd so the engine/pool is
   reused across requests instead of being created fresh each time.

This migration
--------------
1. Reads all existing admin_config rows.
2. Determines the "best" signup_credit_mode across all rows (any
   non-'normal' value wins — the admin's intentional change is preserved).
3. Deletes ALL existing rows.
4. Inserts a single canonical row with id=1 and the best mode.
5. Resets the identity sequence to 1.
6. Adds CHECK (id = 1) to prevent a second row from ever being
   inserted.

Using delete-all + single re-insert (rather than UPDATE + DELETE)
avoids the complexity of updating a primary key in place.
"""

import sqlalchemy as sa
from alembic import op


revision: str = "k4l5m6n7o8p9"
down_revision = "j3k4l5m6n7o8"
branch_labels = None
depends_on = None

# Columns that must be present in the re-insert (excluding id, created_at, updated_at)
_REQUIRED_TEXT_COLS = ["simple_model"]
_SIMPLE_MODEL_DEFAULT = "gpt-5.2"


def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 1: read current state ────────────────────────────────────────────
    rows = conn.execute(
        sa.text(
            "SELECT id, simple_model, signup_credit_mode "
            "FROM admin_config ORDER BY id"
        )
    ).fetchall()

    # Determine best values to preserve across any duplicates.
    best_mode = "normal"
    best_simple_model = _SIMPLE_MODEL_DEFAULT

    for _, simple_model, mode in rows:
        # Keep the first (lowest-id) simple_model as the authoritative value.
        if best_simple_model == _SIMPLE_MODEL_DEFAULT and simple_model:
            best_simple_model = simple_model
        # Prefer any non-default credit mode (admin's intentional change).
        if mode and mode != "normal":
            best_mode = mode

    # ── Step 2: delete all existing rows ─────────────────────────────────────
    conn.execute(sa.text("DELETE FROM admin_config"))

    # ── Step 3: insert the single canonical row with id=1 ────────────────────
    conn.execute(
        sa.text(
            "INSERT INTO admin_config "
            "(id, simple_model, signup_credit_mode, created_at, updated_at) "
            "VALUES (1, :simple_model, :mode, now(), now())"
        ).bindparams(simple_model=best_simple_model, mode=best_mode)
    )

    # ── Step 4: reset the identity sequence to 1 so the next auto-INSERT
    #    would get id=2.  Since CHECK (id=1) prevents any auto-INSERT anyway,
    #    this is belt-and-suspenders hygiene. ─────────────────────────────────
    conn.execute(
        sa.text(
            "SELECT setval(pg_get_serial_sequence('admin_config', 'id'), 1)"
        )
    )

    # ── Step 5: enforce single-row semantics at the DB level ──────────────────
    op.create_check_constraint(
        "ck_admin_config_single_row",
        "admin_config",
        "id = 1",
    )


def downgrade() -> None:
    op.drop_constraint("ck_admin_config_single_row", "admin_config", type_="check")
