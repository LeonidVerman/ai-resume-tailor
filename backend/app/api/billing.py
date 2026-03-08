"""
backend/app/api/billing.py

Billing endpoints.

Endpoints
---------
GET  /billing/status                   — current plan and usage status
POST /billing/create-checkout-session  — create Stripe checkout session

Phase 8 status: PARTIALLY FUNCTIONAL (Stripe not required to start)
--------------------------------------------------------------------
GET /billing/status works with no Stripe config — returns free plan info.
POST /billing/create-checkout-session requires STRIPE_SECRET_KEY and
STRIPE_PRICE_STARTER or STRIPE_PRICE_PRO env vars to be set; returns
503 when Stripe is not configured.
"""

from fastapi import APIRouter, HTTPException, status

from backend.app.dependencies import CurrentUserDep, DbDep, SettingsDep
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.schemas.billing import (
    BillingStatus,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
)
from backend.app.services.billing_service import BillingService
from backend.app.services.usage_policy_service import UsagePolicyService

router = APIRouter()


def _billing_service(db, settings) -> BillingService:
    stripe_client = None
    if settings.stripe_secret_key:
        from backend.app.clients.stripe_client import StripeClient
        stripe_client = StripeClient(api_key=settings.stripe_secret_key)
    return BillingService(BillingRepository(db), stripe_client)


@router.get("/status", response_model=BillingStatus)
def billing_status(user: CurrentUserDep, db: DbDep, settings: SettingsDep):
    """Return the authenticated user's current plan and usage status."""
    free_used = UsagePolicyService(GenerationRunRepository(db)).free_generations_used(user.id)
    return _billing_service(db, settings).get_status(user.id, free_used)


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    request: CheckoutSessionRequest,
    user: CurrentUserDep,
    db: DbDep,
    settings: SettingsDep,
):
    """
    Create a Stripe Checkout session for a plan upgrade.

    Requires Stripe to be configured (STRIPE_SECRET_KEY + price env vars).
    Returns 503 when Stripe is not configured.
    """
    if not settings.stripe_secret_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Billing is not configured on this server",
        )
    return _billing_service(db, settings).create_checkout_session(
        user_id=user.id,
        user_email=user.email,
        request=request,
    )
