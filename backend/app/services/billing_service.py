"""
backend/app/services/billing_service.py

Billing service — manages Stripe subscription state and billing records.

Responsibilities
----------------
- Read current billing/plan status for a user
- Create Stripe checkout sessions (delegates to StripeClient)
- Handle webhook events: subscription created/updated/deleted
- Sync subscription state into the local Billing DB record

Business rules are here; StripeClient is transport only.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from backend.app.constants import PLAN_FREE, PLAN_PRO, PLAN_STARTER
from backend.app.db.models.billing import Billing
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.schemas.billing import (
    BillingStatus,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
)

logger = logging.getLogger(__name__)

# Map Stripe price ID → plan type (populated from env in production)
_PRICE_TO_PLAN: dict[str, str] = {}


class BillingService:
    """
    Billing and subscription management.

    Dependencies
    ------------
    billing_repo: BillingRepository
    stripe_client: StripeClient (Phase 5) — may be None when Stripe is not configured.
    """

    def __init__(self, billing_repo: BillingRepository, stripe_client=None) -> None:
        self._repo = billing_repo
        self._stripe = stripe_client

    # ── Read ───────────────────────────────────────────────────────────────

    def get_status(self, user_id: str, free_used: int) -> BillingStatus:
        """Return the user's current plan and usage status."""
        from backend.app.constants import FREE_TIER_GENERATION_LIMIT

        billing = self._repo.get_by_user_id(user_id)
        plan = billing.plan_type if billing else PLAN_FREE
        return BillingStatus(
            user_id=user_id,
            plan_type=plan,
            subscription_status=billing.subscription_status if billing else None,
            free_generations_used=free_used,
            free_generations_limit=FREE_TIER_GENERATION_LIMIT,
            current_period_end=billing.current_period_end if billing else None,
            stripe_customer_id=billing.stripe_customer_id if billing else None,
        )

    # ── Checkout ───────────────────────────────────────────────────────────

    def create_checkout_session(
        self, user_id: str, user_email: str, request: CheckoutSessionRequest
    ) -> CheckoutSessionResponse:
        """Create a Stripe Checkout session for plan upgrade."""
        if self._stripe is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing is not configured",
            )

        price_id = self._price_id_for_plan(request.plan_type)
        customer_id = self._stripe.get_or_create_customer(user_email, user_id)
        session = self._stripe.create_checkout_session(
            price_id=price_id,
            customer_id=customer_id,
            success_url=request.success_url,
            cancel_url=request.cancel_url,
        )
        logger.info("Created checkout session user=%s plan=%s", user_id, request.plan_type)
        return CheckoutSessionResponse(
            checkout_url=session.checkout_url,
            session_id=session.session_id,
        )

    # ── Webhook event handlers ──────────────────────────────────────────────

    def handle_subscription_updated(self, stripe_event: dict) -> None:
        """
        Sync subscription state from a Stripe webhook event.

        Called for customer.subscription.created / updated / deleted.
        """
        sub = stripe_event.get("data", {}).get("object", {})
        customer_id = sub.get("customer")
        if not customer_id:
            logger.warning("Webhook event missing customer ID")
            return

        billing = self._repo.get_by_stripe_customer_id(customer_id)
        if billing is None:
            logger.warning("No billing record for Stripe customer %s", customer_id)
            return

        sub_status = sub.get("status")
        period_end_ts = sub.get("current_period_end")
        period_end = (
            datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
            if period_end_ts
            else None
        )

        # Derive plan from price
        items = sub.get("items", {}).get("data", [])
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = _PRICE_TO_PLAN.get(price_id, billing.plan_type) if price_id else billing.plan_type

        if sub_status in ("canceled", "unpaid"):
            plan = PLAN_FREE

        self._repo.update(
            billing,
            stripe_subscription_id=sub.get("id"),
            subscription_status=sub_status,
            plan_type=plan,
            current_period_end=period_end,
        )
        logger.info(
            "Synced subscription customer=%s status=%s plan=%s",
            customer_id, sub_status, plan,
        )

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _price_id_for_plan(plan_type: str) -> str:
        """Return the Stripe price ID for the given plan type."""
        import os

        env_key = f"STRIPE_PRICE_{plan_type.upper()}"
        price_id = os.getenv(env_key, "")
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Stripe price ID for plan '{plan_type}' is not configured",
            )
        return price_id

    def _get_or_create_billing(self, user_id: str) -> Billing:
        billing = self._repo.get_by_user_id(user_id)
        if billing is None:
            billing = self._repo.create(user_id=user_id, plan_type=PLAN_FREE)
        return billing
