"""
backend/app/db/repositories/billing_repository.py

CRUD operations for Billing, including atomic credit operations.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.models.billing import Billing


class BillingRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get_by_user_id(self, user_id: str) -> Billing | None:
        return (
            self._db.query(Billing)
            .filter(Billing.user_id == user_id)
            .first()
        )

    def get_by_stripe_customer_id(self, stripe_customer_id: str) -> Billing | None:
        return (
            self._db.query(Billing)
            .filter(Billing.stripe_customer_id == stripe_customer_id)
            .first()
        )

    def create(self, **kwargs) -> Billing:
        billing = Billing(**kwargs)
        self._db.add(billing)
        self._db.flush()
        return billing

    def update(self, billing: Billing, **kwargs) -> Billing:
        for key, value in kwargs.items():
            setattr(billing, key, value)
        self._db.flush()
        return billing

    # ── Atomic credit operations ───────────────────────────────────────────

    def decrement_credits_atomic(self, user_id: str) -> bool:
        """
        Atomically decrement extra_credits by 1 if > 0.

        Returns True if a credit was consumed.
        Returns False if extra_credits was already 0 (no credits available).

        Single UPDATE statement — safe under concurrent requests.
        """
        result = self._db.execute(
            text(
                "UPDATE billing SET extra_credits = extra_credits - 1, "
                "updated_at = now() "
                "WHERE user_id = :user_id AND extra_credits > 0"
            ),
            {"user_id": user_id},
        )
        return result.rowcount > 0

    def add_credits(self, user_id: str, amount: int) -> None:
        """
        Add credits to a user's balance.  Creates a billing row if none exists.
        Used by the admin grant-credits endpoint and the credit-pack webhook handler.

        Admin-only path: not performance-critical, ORM is fine.
        """
        from backend.app.constants import PLAN_FREE

        billing = self.get_by_user_id(user_id)
        if billing is None:
            self.create(user_id=user_id, plan_type=PLAN_FREE, extra_credits=amount)
        else:
            billing.extra_credits += amount
            self._db.flush()

    def grant_initial_signup_credits(
        self, user_id: str, amount: int, mode: str
    ) -> bool:
        """
        Grant initial signup credits exactly once per user.

        Creates the billing row if absent, then atomically sets
        initial_credits_granted=True and credits extra_credits.

        Returns True if credits were granted, False if already granted (idempotent).
        """
        from backend.app.constants import PLAN_FREE

        billing = self.get_by_user_id(user_id)
        if billing is None:
            self.create(
                user_id=user_id,
                plan_type=PLAN_FREE,
                extra_credits=amount,
                initial_credits_granted=True,
                initial_credits_amount=amount,
                initial_credits_mode=mode,
            )
            return True

        if billing.initial_credits_granted:
            return False

        billing.extra_credits += amount
        billing.initial_credits_granted = True
        billing.initial_credits_amount = amount
        billing.initial_credits_mode = mode
        self._db.flush()
        return True
