"""
Phase-1 UUID→BigInt migration sanity check.

Connects to the live database and reports:
  1. Row count per migrated table
  2. Old UUID / new bigint column names
  3. NULL bigint IDs
  4. Duplicate bigint IDs
  5. UUID FK exists but bigint FK is NULL (child tables)
  6. Orphan check: bigint FK references a non-existent parent
  7. Sample UUID↔bigint mappings (up to 5 rows each)
  8. alembic_version head
"""

import os
import sys
from urllib.parse import urlparse, unquote

import psycopg


# ── connection ────────────────────────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    # Try to read from .env in repo root
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    with open(env_path) as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("DATABASE_URL="):
                DATABASE_URL = line.split("=", 1)[1].strip()
                break

# psycopg3 expects postgresql:// not postgresql+psycopg://
conn_str = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://")


def q(cursor, sql, *args):
    cursor.execute(sql, args or None)
    return cursor.fetchall()


def q1(cursor, sql, *args):
    cursor.execute(sql, args or None)
    row = cursor.fetchone()
    return row[0] if row else None


SECTION = "=" * 72


def header(title):
    print(f"\n{SECTION}")
    print(f"  {title}")
    print(SECTION)


# ── table definitions ─────────────────────────────────────────────────────────
# (table, pk_col, uuid_id_col, parent_table, fk_col, fk_uuid_col, parent_pk)
TABLES = [
    # standalone tables (no FK migration)
    dict(table="job_descriptions",       pk="id", uuid_col="uuid_id"),
    dict(table="structured_resumes",     pk="id", uuid_col="uuid_id"),
    dict(table="generation_runs",        pk="id", uuid_col="uuid_id"),
    dict(table="candidate_profiles",     pk="id", uuid_col="uuid_id"),
    dict(table="monthly_usage",          pk="id", uuid_col="uuid_id"),
    dict(table="billing",               pk="id", uuid_col="uuid_id"),
    dict(table="admin_config",          pk="id", uuid_col="uuid_id"),
    dict(table="benchmark_runs",        pk="id", uuid_col="uuid_id"),
    # child tables (FK also migrated)
    dict(table="tailored_documents",    pk="id", uuid_col="uuid_id",
         parent="generation_runs", fk="generation_run_id", fk_uuid="generation_run_uuid_id", parent_pk="id"),
    dict(table="evaluation_runs",       pk="id", uuid_col="uuid_id",
         parent="generation_runs", fk="generation_run_id", fk_uuid="generation_run_uuid_id", parent_pk="id"),
    dict(table="benchmark_run_positions", pk="id", uuid_col="uuid_id",
         parent="benchmark_runs", fk="benchmark_run_id", fk_uuid="benchmark_run_uuid_id", parent_pk="id"),
]


def check_column_exists(cur, table, col):
    return q1(cur,
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name=%s AND column_name=%s", table, col) > 0


def run():
    print(f"\nConnecting to database …")
    with psycopg.connect(conn_str) as conn:
        conn.autocommit = True
        with conn.cursor() as cur:

            # ── Alembic head ─────────────────────────────────────────────────
            header("0. Alembic migration head")
            try:
                rows = q(cur, "SELECT version_num FROM alembic_version")
                for r in rows:
                    print(f"  version_num = {r[0]}")
            except Exception as e:
                print(f"  ERROR: {e}")

            # ── Per-table checks ─────────────────────────────────────────────
            for td in TABLES:
                table      = td["table"]
                pk         = td["pk"]
                uuid_col   = td["uuid_col"]
                parent     = td.get("parent")
                fk         = td.get("fk")
                fk_uuid    = td.get("fk_uuid")
                parent_pk  = td.get("parent_pk")

                header(f"Table: {table}")

                # 1. row count
                total = q1(cur, f"SELECT count(*) FROM {table}")
                print(f"  [1] row count               : {total}")

                # 2. column names (pk + uuid_col)
                pk_exists   = check_column_exists(cur, table, pk)
                uuid_exists = check_column_exists(cur, table, uuid_col)
                print(f"  [2] pk column '{pk}'         : {'EXISTS' if pk_exists else 'MISSING'}")
                print(f"      uuid_id column '{uuid_col}': {'EXISTS' if uuid_exists else 'MISSING'}")
                if fk:
                    fk_exists      = check_column_exists(cur, table, fk)
                    fk_uuid_exists = check_column_exists(cur, table, fk_uuid)
                    print(f"      fk column '{fk}'   : {'EXISTS' if fk_exists else 'MISSING'}")
                    print(f"      fk_uuid '{fk_uuid}': {'EXISTS' if fk_uuid_exists else 'MISSING'}")

                if not pk_exists:
                    print("  ** Skipping further checks — pk column missing **")
                    continue

                # 3. NULL bigint PKs
                null_pk = q1(cur, f"SELECT count(*) FROM {table} WHERE {pk} IS NULL")
                print(f"  [3] NULL bigint PKs          : {null_pk}")

                # 4. Duplicate bigint PKs
                dup_pk = q1(cur,
                    f"SELECT count(*) FROM ("
                    f"  SELECT {pk} FROM {table} GROUP BY {pk} HAVING count(*) > 1"
                    f") x")
                print(f"  [4] duplicate bigint PKs     : {dup_pk}")

                # 5. FK checks (child tables)
                if fk and fk_uuid and check_column_exists(cur, table, fk) and check_column_exists(cur, table, fk_uuid):
                    null_fk_with_uuid = q1(cur,
                        f"SELECT count(*) FROM {table} "
                        f"WHERE {fk_uuid} IS NOT NULL AND {fk} IS NULL")
                    print(f"  [5] uuid_fk set but bigint fk NULL : {null_fk_with_uuid}")

                    # 6. orphan check: bigint FK → parent
                    orphans = q1(cur,
                        f"SELECT count(*) FROM {table} c "
                        f"WHERE c.{fk} IS NOT NULL "
                        f"AND NOT EXISTS ("
                        f"  SELECT 1 FROM {parent} p WHERE p.{parent_pk} = c.{fk}"
                        f")")
                    print(f"  [6] orphan rows (bigint fk → {parent}) : {orphans}")

                # 7. sample UUID↔bigint PK mappings
                if uuid_exists and total > 0:
                    print(f"  [7] sample UUID↔bigint PK mappings (up to 5):")
                    rows = q(cur,
                        f"SELECT {pk}, {uuid_col} FROM {table} ORDER BY {pk} LIMIT 5")
                    for r in rows:
                        print(f"        {pk}={r[0]}  uuid_id={r[1]}")

                # 8. sample FK mappings for children
                if fk and fk_uuid and check_column_exists(cur, table, fk) and check_column_exists(cur, table, fk_uuid) and total > 0:
                    print(f"  [8] sample child bigint↔uuid FK mappings (up to 5):")
                    rows = q(cur,
                        f"SELECT {pk}, {uuid_col}, {fk}, {fk_uuid} "
                        f"FROM {table} ORDER BY {pk} LIMIT 5")
                    for r in rows:
                        print(f"        id={r[0]} uuid_id={r[1]} {fk}={r[2]} {fk_uuid}={r[3]}")

            # ── Overall summary ──────────────────────────────────────────────
            header("Summary: NULL/duplicate/orphan totals across all tables")
            total_issues = 0
            for td in TABLES:
                table = td["table"]
                pk    = td["pk"]
                if not check_column_exists(cur, table, pk):
                    continue
                n_null = q1(cur, f"SELECT count(*) FROM {table} WHERE {pk} IS NULL")
                n_dup  = q1(cur,
                    f"SELECT count(*) FROM ("
                    f"  SELECT {pk} FROM {table} GROUP BY {pk} HAVING count(*) > 1"
                    f") x")
                fk = td.get("fk")
                fk_uuid = td.get("fk_uuid")
                n_orphan = 0
                n_fk_null = 0
                if fk and fk_uuid and check_column_exists(cur, table, fk) and check_column_exists(cur, table, fk_uuid):
                    parent = td["parent"]
                    parent_pk = td["parent_pk"]
                    n_fk_null = q1(cur,
                        f"SELECT count(*) FROM {table} "
                        f"WHERE {fk_uuid} IS NOT NULL AND {fk} IS NULL")
                    n_orphan = q1(cur,
                        f"SELECT count(*) FROM {table} c "
                        f"WHERE c.{fk} IS NOT NULL "
                        f"AND NOT EXISTS ("
                        f"  SELECT 1 FROM {parent} p WHERE p.{parent_pk} = c.{fk}"
                        f")")
                issues = n_null + n_dup + n_orphan + n_fk_null
                total_issues += issues
                status = "OK" if issues == 0 else f"ISSUES={issues}"
                print(f"  {table:<35} null_pk={n_null} dup_pk={n_dup} "
                      f"fk_null={n_fk_null} orphans={n_orphan}  [{status}]")

            print(f"\n  Total issues: {total_issues}")
            if total_issues == 0:
                print("  ✓ All checks passed — CLEAN")
            else:
                print("  ✗ Issues found — investigate before Phase 2")


if __name__ == "__main__":
    run()
