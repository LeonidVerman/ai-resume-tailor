"""
backend/app/clients/stripe_client.py

Thin Stripe SDK wrapper.

Responsibilities
----------------
- create checkout sessions
- retrieve customer / subscription state
- construct and validate webhook events
- normalize Stripe exceptions into application errors

This client contains no business rules (plan transitions, quota logic, etc.).
That belongs in the billing service (Phase 14).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class CheckoutSessionResult:
    session_id: str
    checkout_url: str


@dataclass
class SubscriptionState:
    customer_id: str
    subscription_id: str | None
    status: str | None          # active | canceled | past_due | trialing | None
    plan_type: str | None       # starter | pro | None (derived from price metadata)
    current_period_end: int | None   # Unix timestamp


# ── Client ─────────────────────────────────────────────────────────────────

class StripeClient:
    """
    Thin wrapper around the Stripe Python SDK.

    Parameters
    ----------
    secret_key:
        Stripe secret key (``sk_live_...`` or ``sk_test_...``).
    webhook_secret:
        Signing secret for webhook validation (``whsec_...``).
    """

    def __init__(self, secret_key: str = "", webhook_secret: str = "") -> None:
        self._secret_key = secret_key
        self._webhook_secret = webhook_secret

    # ── Internal ───────────────────────────────────────────────────────────

    def _stripe(self):
        """Return the stripe module configured with the instance key."""
        import stripe
        if self._secret_key:
            stripe.api_key = self._secret_key
        elif not stripe.api_key:
            raise RuntimeError(
                "Stripe API key is not configured. "
                "Set STRIPE_SECRET_KEY in backend/.env."
            )
        return stripe

    # ── Checkout ───────────────────────────────────────────────────────────

    def create_checkout_session(
        self,
        price_id: str,
        *,
        customer_id: str | None = None,
        customer_email: str | None = None,
        success_url: str,
        cancel_url: str,
        metadata: dict | None = None,
    ) -> CheckoutSessionResult:
        """Create a Stripe Checkout Session for a subscription."""
        stripe = self._stripe()

        params: dict[str, Any] = {
            "mode": "subscription",
            "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if customer_id:
            params["customer"] = customer_id
        elif customer_email:
            params["customer_email"] = customer_email
        if metadata:
            params["metadata"] = metadata

        session = stripe.checkout.Session.create(**params)
        logger.info("Created checkout session id=%s", session.id)
        return CheckoutSessionResult(
            session_id=session.id,
            checkout_url=session.url,
        )

    # ── Customer / subscription ────────────────────────────────────────────

    def get_or_create_customer(self, email: str, user_id: str) -> str:
        """Return an existing Stripe customer ID or create a new customer.

        Searches by email first to avoid duplicates.
        Returns the Stripe customer id string.
        """
        stripe = self._stripe()
        existing = stripe.Customer.search(query=f'email:"{email}"', limit=1)
        if existing.data:
            customer_id = existing.data[0].id
            logger.debug("Found existing Stripe customer %s for %s", customer_id, email)
            return customer_id

        customer = stripe.Customer.create(
            email=email,
            metadata={"user_id": user_id},
        )
        logger.info("Created Stripe customer %s for user %s", customer.id, user_id)
        return customer.id

    def get_subscription_state(self, customer_id: str) -> SubscriptionState:
        """Return the current subscription state for a customer."""
        stripe = self._stripe()
        subscriptions = stripe.Subscription.list(
            customer=customer_id, status="all", limit=1
        )
        if not subscriptions.data:
            return SubscriptionState(
                customer_id=customer_id,
                subscription_id=None,
                status=None,
                plan_type=None,
                current_period_end=None,
            )

        sub = subscriptions.data[0]
        # Derive plan type from subscription metadata or price metadata
        plan_type = (
            sub.metadata.get("plan_type")
            or (sub.items.data[0].price.metadata.get("plan_type") if sub.items.data else None)
        )
        return SubscriptionState(
            customer_id=customer_id,
            subscription_id=sub.id,
            status=sub.status,
            plan_type=plan_type,
            current_period_end=sub.current_period_end,
        )

    # ── Webhooks ───────────────────────────────────────────────────────────

    def construct_webhook_event(self, payload: bytes, sig_header: str) -> Any:
        """Validate and parse an incoming Stripe webhook event.

        Raises ``stripe.error.SignatureVerificationError`` if the signature
        is invalid (caller should return HTTP 400).
        """
        stripe = self._stripe()
        if not self._webhook_secret:
            raise RuntimeError(
                "STRIPE_WEBHOOK_SECRET is not configured. "
                "Set it in backend/.env to validate webhooks."
            )
        return stripe.Webhook.construct_event(
            payload, sig_header, self._webhook_secret
        )


# ── Factory ────────────────────────────────────────────────────────────────

def make_stripe_client_from_settings() -> StripeClient:
    """Create a StripeClient from application settings."""
    from backend.app.config import get_settings
    s = get_settings()
    return StripeClient(
        secret_key=s.stripe_secret_key,
        webhook_secret=s.stripe_webhook_secret,
    )
