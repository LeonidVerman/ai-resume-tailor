"""
backend/app/services/usage_policy_service.py

Usage policy service — enforces per-plan monthly generation quotas.

Entitlement resolution order (per spec):
  1. Monthly included quota (plan limit or per-user override)
  2. One-time extra credits
  3. Reject with HTTP 429

All DB writes are atomic single-statement operations to prevent race
conditions under concurrent requests from the same user.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from backend.app.constants import PLAN_MONTHLY_LIMITS
from backend.app.db.models.billing import Billing
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.monthly_usage_repository import MonthlyUsageRepository

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(tz=timezone.utc)


class UsagePolicyService:
    """
    Check and consume a generation quota slot for a user.

    Dependencies
    ------------
    billing_repo:       BillingRepository
    monthly_usage_repo: MonthlyUsageRepository
    """

    def __init__(
        self,
        billing_repo: BillingRepository,
        monthly_usage_repo: MonthlyUsageRepository,
    ) -> None:
        self._billing = billing_repo
        self._usage = monthly_usage_repo

    # ── Public API ─────────────────────────────────────────────────────────

    def check_quota(self, user_id: str, billing: Billing | None) -> None:
        """
        Verify the user has remaining quota without consuming it.

        Called at the API layer before the generation pipeline starts, so
        users get an immediate 429 rather than waiting 30-90 s for the LLM
        to finish before discovering they are over limit.

        Raises HTTP 429 with a structured body when all quota is exhausted.
        Does NOT increment any counter.
        """
        now = _utc_now()
        year, month = now.year, now.month
        plan = billing.plan_type if billing else "free"
        limit = self._resolve_limit(billing)
        extra_credits = billing.extra_credits if billing else 0
        monthly_used = self._usage.get_count(user_id, year, month)

        if monthly_used < limit:
            return  # monthly quota available
        if billing is not None and extra_credits > 0:
            return  # credit pack available

        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": (
                    f"Monthly generation limit reached ({monthly_used}/{limit}). "
                    "Upgrade your plan or purchase a credit pack to continue."
                ),
                "error_code": "QUOTA_EXCEEDED",
                "plan": plan,
                "monthly_used": monthly_used,
                "monthly_limit": limit,
                "extra_credits": extra_credits,
            },
        )

    def consume(self, user_id: str, billing: Billing | None) -> None:
        """
        Atomically record one unit of usage after a successful generation.

        Called only when the generation pipeline has fully succeeded, so
        failures (post-processing errors, DB write errors, etc.) do not
        consume a generation slot.

        Falls back to extra credits when the monthly quota is already full
        (race condition: another request consumed the last slot between
        check_quota() and consume()).  Raises HTTP 429 only in the unlikely
        event that both quota and credits are exhausted at consume time.
        """
        now = _utc_now()
        year, month = now.year, now.month
        plan = billing.plan_type if billing else "free"
        limit = self._resolve_limit(billing)
        extra_credits = billing.extra_credits if billing else 0

        # Step 1: try monthly quota (atomic INSERT … ON CONFLICT DO UPDATE WHERE count < limit)
        allowed = self._usage.increment_atomic(user_id, year, month, limit)
        if allowed:
            logger.debug(
                "Monthly quota consumed user=%s plan=%s %d-%02d",
                user_id, plan, year, month,
            )
            return

        # Step 2: try one-time credits (atomic UPDATE WHERE extra_credits > 0)
        if billing is not None:
            allowed = self._billing.decrement_credits_atomic(user_id)
            if allowed:
                logger.info(
                    "Extra credit consumed user=%s (monthly quota exhausted)",
                    user_id,
                )
                return

        # Step 3: reject — quota and credits both exhausted (race condition)
        monthly_used = self._usage.get_count(user_id, year, month)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": (
                    f"Monthly generation limit reached ({monthly_used}/{limit}). "
                    "Upgrade your plan or purchase a credit pack to continue."
                ),
                "error_code": "QUOTA_EXCEEDED",
                "plan": plan,
                "monthly_used": monthly_used,
                "monthly_limit": limit,
                "extra_credits": extra_credits,
            },
        )

    def get_usage_summary(self, user_id: str, billing: Billing | None) -> dict:
        """Return usage counters for the billing status endpoint."""
        now = _utc_now()
        monthly_used = self._usage.get_count(user_id, now.year, now.month)
        limit = self._resolve_limit(billing)
        extra_credits = billing.extra_credits if billing else 0
        return {
            "monthly_used": monthly_used,
            "monthly_limit": limit,
            "extra_credits": extra_credits,
        }

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _resolve_limit(billing: Billing | None) -> int:
        """Return the effective monthly limit for the user."""
        if billing is not None and billing.monthly_limit_override is not None:
            return billing.monthly_limit_override
        plan = billing.plan_type if billing else "free"
        return PLAN_MONTHLY_LIMITS.get(plan, PLAN_MONTHLY_LIMITS["free"])
