"""Extended validator for Phase 1 (Planner) output.

All v2.1 experimental checks have been removed.  The function is kept as a
no-op so callers (cli.py, llm.py) require no changes.
"""

from __future__ import annotations


def validate_plan_extended(plan: dict) -> list[str]:
    """Run extended checks on a TailoringPlan dict.

    Returns a list of error strings.  Currently performs no checks; always
    returns an empty list.
    """
    return []
