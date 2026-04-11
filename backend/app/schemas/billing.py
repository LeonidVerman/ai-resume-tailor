"""
backend/app/schemas/billing.py

Billing and subscription schemas.
"""

from datetime import datetime
from typing import Literal

from backend.app.schemas.common import APIModel

SubscriptionStatus = Literal["active", "canceled", "past_due", "trialing"] | None
PlanType = Literal["free", "starter", "pro"]


class BillingStatus(APIModel):
    """Returned by GET /billing/status — the user's current plan and usage state."""
    user_id: str
    plan_type: PlanType
    subscription_status: SubscriptionStatus
    # Monthly quota
    monthly_used: int
    monthly_limit: int
    # One-time credit pack balance
    extra_credits: int
    current_period_end: datetime | None = None
    stripe_customer_id: str | None = None


class CheckoutSessionRequest(APIModel):
    """Request body for POST /billing/create-checkout-session."""
    plan_type: Literal["starter", "pro", "credit_pack"]
    success_url: str = ""
    cancel_url: str = ""


class CheckoutSessionResponse(APIModel):
    """Response from POST /billing/create-checkout-session."""
    checkout_url: str
    session_id: str


class CustomerPortalResponse(APIModel):
    """Response from POST /billing/customer-portal."""
    portal_url: str


class UpgradePlanRequest(APIModel):
    """Request body for POST /billing/upgrade-plan."""
    plan_type: Literal["pro"]  # Only upward upgrade supported (starter → pro)


class UpgradePlanResponse(APIModel):
    """Response from POST /billing/upgrade-plan."""
    ok: bool
    plan_type: str
    message: str


class GrantCreditsRequest(APIModel):
    """Request body for POST /admin/billing/grant-credits.
    Use a negative amount to deduct credits (floored at 0)."""
    user_id: str
    amount: int  # positive to add, negative to deduct


class RegisterCheckoutSessionRequest(APIModel):
    """Request body for POST /admin/billing/register-checkout-session.

    Manually records a Stripe checkout session as already processed in
    stripe_checkout_purchases. Use this to seed the dedup table for any
    checkout sessions that were processed before the idempotency migration
    was deployed (cold-start gap), preventing future replays from granting
    credits again.
    """
    checkout_session_id: str
    user_id: str
    granted_credits: int
    stripe_event_id: str | None = None
