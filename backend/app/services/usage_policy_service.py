"""
backend/app/services/usage_policy_service.py

Usage policy service — enforces per-plan generation limits.

Rules (v1)
----------
- free  : FREE_TIER_GENERATION_LIMIT (2) total succeeded generations
- starter / pro: unlimited (governed by active subscription status)

This service checks policy but does NOT write to the DB; the caller
(generation service) is responsible for committing the run record.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from backend.app.constants import FREE_TIER_GENERATION_LIMIT, PLAN_FREE
from backend.app.db.models.billing import Billing
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository

logger = logging.getLogger(__name__)


class UsagePolicyService:
    """
    Check whether a user is allowed to start a new generation run.

    Dependencies
    ------------
    run_repo: GenerationRunRepository — to count past succeeded runs.
    """

    def __init__(self, run_repo: GenerationRunRepository) -> None:
        self._run_repo = run_repo

    def check_can_generate(self, user_id: str, billing: Billing | None) -> None:
        """
        Raise HTTP 402 if the user has exhausted their generation quota.

        Parameters
        ----------
        user_id:
            The requesting user's ID.
        billing:
            The user's Billing record (may be None if not yet created).
        """
        plan = billing.plan_type if billing else PLAN_FREE

        if plan == PLAN_FREE:
            used = self._run_repo.count_succeeded_by_user_id(user_id)
            if used >= FREE_TIER_GENERATION_LIMIT:
                raise HTTPException(
                    status_code=status.HTTP_402_PAYMENT_REQUIRED,
                    detail=(
                        f"Free plan limit reached ({FREE_TIER_GENERATION_LIMIT} generations). "
                        "Upgrade to Starter or Pro to continue."
                    ),
                )
            logger.debug(
                "Free plan user=%s used=%d limit=%d", user_id, used, FREE_TIER_GENERATION_LIMIT
            )
            return

        # Paid plans: require an active or trialing subscription
        if billing is None or billing.subscription_status not in ("active", "trialing"):
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail="No active subscription. Renew your plan to generate documents.",
            )

        logger.debug("Paid plan user=%s plan=%s allowed", user_id, plan)

    def free_generations_used(self, user_id: str) -> int:
        """Return the count of succeeded generations for quota display."""
        return self._run_repo.count_succeeded_by_user_id(user_id)
