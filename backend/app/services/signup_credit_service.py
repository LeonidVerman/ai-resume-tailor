"""
backend/app/services/signup_credit_service.py

SignupCreditService — grants the configured number of one-time initial
credits to newly registered users.

The credit amount is stored in admin_config.initial_credits and is
configurable through the Admin panel without a code change.

Users granted credits via this path have monthly_limit_override=0 so
their total allowance is exactly `initial_credits` — they do not also
receive the standard free-plan monthly quota on top.

Idempotency is enforced via billing.initial_credits_granted.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.app.db.repositories.admin_config_repository import AdminConfigRepository
from backend.app.db.repositories.billing_repository import BillingRepository

logger = logging.getLogger(__name__)


class SignupCreditService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_initial_credits(self) -> int:
        """Return the currently configured initial credit amount."""
        return AdminConfigRepository(self._db).get().initial_credits

    def grant_initial_credits(self, user_id: str) -> None:
        """
        Grant the configured initial credits to a new user.

        Sets monthly_limit_override=0 so the user's total allowance is
        exactly the granted amount (not credits + free monthly quota).

        Idempotent: if initial_credits_granted=True this is a no-op.
        """
        amount = self.get_initial_credits()
        monthly_override = 0 if amount > 0 else None
        granted = BillingRepository(self._db).grant_initial_signup_credits(
            user_id=user_id,
            amount=amount,
            mode=None,
            monthly_limit_override=monthly_override,
        )
        if granted:
            logger.info(
                "Signup credits granted user_id=%s amount=%d",
                user_id, amount,
            )
        else:
            logger.debug(
                "Signup credits already granted for user_id=%s — skipped.", user_id
            )
