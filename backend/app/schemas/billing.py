"""
backend/app/schemas/billing.py

Billing and subscription schemas.

Intentionally thin — Stripe is the source of truth for billing events.
These models expose what the app needs to know about a user's plan state.
"""

from datetime import datetime
from typing import Literal

from backend.app.schemas.common import APIModel

SubscriptionStatus = Literal["active", "canceled", "past_due", "trialing"] | None
PlanType = Literal["free", "starter", "pro"]


class BillingStatus(APIModel):
    """Returned by GET /billing/status — the user's current plan state."""
    user_id: str
    plan_type: PlanType
    subscription_status: SubscriptionStatus
    free_generations_used: int
    free_generations_limit: int
    current_period_end: datetime | None = None
    stripe_customer_id: str | None = None


class CheckoutSessionRequest(APIModel):
    """Request body for POST /billing/create-checkout-session."""
    plan_type: Literal["starter", "pro"]
    success_url: str
    cancel_url: str


class CheckoutSessionResponse(APIModel):
    """Response from POST /billing/create-checkout-session."""
    checkout_url: str
    session_id: str
