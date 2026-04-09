"""
backend/app/services/signup_credit_service.py

SignupCreditService — resolves the active signup credit policy and grants
the one-time initial credit allocation to newly registered users.

Policy is stored in admin_config.signup_credit_mode.
Resolved amounts are defined in constants.SIGNUP_CREDIT_AMOUNTS.
Idempotency is enforced via billing.initial_credits_granted.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from backend.app.constants import SIGNUP_CREDIT_AMOUNTS, SIGNUP_CREDIT_MODE_NORMAL
from backend.app.db.repositories.admin_config_repository import AdminConfigRepository
from backend.app.db.repositories.billing_repository import BillingRepository

logger = logging.getLogger(__name__)


class SignupCreditService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Policy resolution ──────────────────────────────────────────────────

    def get_current_policy(self) -> tuple[str, int]:
        """Return (mode, amount) for the current signup credit policy."""
        cfg = AdminConfigRepository(self._db).get()
        mode = cfg.signup_credit_mode or SIGNUP_CREDIT_MODE_NORMAL
        amount = self.resolve_credit_amount(mode)
        return mode, amount

    @staticmethod
    def resolve_credit_amount(mode: str) -> int:
        """Map a signup credit mode to its resolved credit amount."""
        return SIGNUP_CREDIT_AMOUNTS.get(mode, SIGNUP_CREDIT_AMOUNTS[SIGNUP_CREDIT_MODE_NORMAL])

    # ── Grant ──────────────────────────────────────────────────────────────

    def grant_initial_credits(self, user_id: str) -> None:
        """
        Grant the configured initial signup credits to a new user.

        Idempotent: if the user already has initial_credits_granted=True on
        their billing row this method is a no-op.  Safe to call on every
        registration attempt.
        """
        mode, amount = self.get_current_policy()
        granted = BillingRepository(self._db).grant_initial_signup_credits(
            user_id=user_id,
            amount=amount,
            mode=mode,
        )
        if granted:
            logger.info(
                "Signup credits granted user_id=%s amount=%d mode=%s",
                user_id, amount, mode,
            )
        else:
            logger.debug(
                "Signup credits already granted for user_id=%s — skipped.", user_id
            )
