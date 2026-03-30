#!/usr/bin/env python3
"""
scripts/create_admin_user.py

Create an admin user in the development database.

Status: PLACEHOLDER — auth not yet implemented.
        This script will be implemented in Phase 6 of the task plan.

Future usage:
    python scripts/create_admin_user.py --email admin@example.com

Prerequisites (future):
    - PostgreSQL running with migrations applied
    - DATABASE_URL set in backend/.env
    - pip install -e backend/
"""

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an admin user (placeholder).")
    parser.add_argument("--email", required=True, help="Admin user email")
    args = parser.parse_args()

    print(f"[create_admin_user] Admin user creation not yet implemented (Phase 6).")
    print(f"  Email: {args.email}")
    print(f"  Spec: doc/IMPLEMENTATION_TASK_PLAN.md (Task 38)")


if __name__ == "__main__":
    main()
