"""
backend/app/db/repositories/billing_repository.py

CRUD operations for Billing, including atomic credit operations.
"""

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.app.db.models.billing import Billing
from backend.app.db.models.stripe_checkout_purchase import StripeCheckoutPurchase


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
        Add (or deduct, if negative) credits to a user's balance.
        Creates a billing row if none exists.
        Balance is floored at 0 — cannot go negative.

        Used by the admin grant/adjust-credits endpoint and the credit-pack webhook handler.
        Admin-only path: not performance-critical, ORM is fine.
        """
        from backend.app.constants import PLAN_FREE

        billing = self.get_by_user_id(user_id)
        if billing is None:
            self.create(user_id=user_id, plan_type=PLAN_FREE, extra_credits=max(0, amount))
        else:
            billing.extra_credits = max(0, billing.extra_credits + amount)
            self._db.flush()

    def record_checkout_credit_grant(
        self,
        checkout_session_id: str,
        user_id: str,
        granted_credits: int,
        event_id: str | None = None,
        payment_intent_id: str | None = None,
    ) -> bool:
        """
        Record that a credit grant was made for a Stripe checkout session.

        Returns True if this is the first time this checkout session is processed
        (credits should be granted).
        Returns False if the checkout session was already recorded (duplicate
        delivery — credits must NOT be granted again).

        The INSERT and the subsequent add_credits() call share the same DB
        session, so they participate in the same transaction.
        """
        existing = (
            self._db.query(StripeCheckoutPurchase)
            .filter(
                StripeCheckoutPurchase.stripe_checkout_session_id == checkout_session_id
            )
            .first()
        )
        if existing is not None:
            return False

        purchase = StripeCheckoutPurchase(
            stripe_checkout_session_id=checkout_session_id,
            user_id=user_id,
            stripe_event_id=event_id,
            stripe_payment_intent_id=payment_intent_id,
            granted_credits=granted_credits,
            processed_at=datetime.now(timezone.utc),
        )
        self._db.add(purchase)
        self._db.flush()
        return True

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
