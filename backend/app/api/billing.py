"""
backend/app/api/billing.py

Billing endpoints.

Endpoints
---------
GET  /billing/status                   — current plan, usage, and credit balance
POST /billing/create-checkout-session  — Stripe Checkout (subscription or credit pack)
POST /billing/customer-portal          — Stripe Customer Portal session
"""

from fastapi import APIRouter, status

from backend.app.dependencies import CurrentUserDep, DbDep, SettingsDep
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.monthly_usage_repository import MonthlyUsageRepository
from backend.app.schemas.billing import (
    BillingStatus,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalResponse,
)
from backend.app.services.billing_service import BillingService
from backend.app.services.usage_policy_service import UsagePolicyService

router = APIRouter()


def _billing_service(db, settings) -> BillingService:
    stripe_client = None
    if settings.stripe_secret_key:
        from backend.app.clients.stripe_client import StripeClient
        stripe_client = StripeClient(
            secret_key=settings.stripe_secret_key,
            webhook_secret=settings.stripe_webhook_secret,
        )
    usage_svc = UsagePolicyService(
        billing_repo=BillingRepository(db),
        monthly_usage_repo=MonthlyUsageRepository(db),
    )
    return BillingService(
        billing_repo=BillingRepository(db),
        usage_service=usage_svc,
        stripe_client=stripe_client,
        settings=settings,
    )


@router.get("/status", response_model=BillingStatus)
def billing_status(user: CurrentUserDep, db: DbDep, settings: SettingsDep):
    """Return the authenticated user's current plan, usage, and credit balance."""
    return _billing_service(db, settings).get_status(user.id)


@router.post("/create-checkout-session", response_model=CheckoutSessionResponse)
def create_checkout_session(
    request: CheckoutSessionRequest,
    user: CurrentUserDep,
    db: DbDep,
    settings: SettingsDep,
):
    """
    Create a Stripe Checkout session.

    plan_type: "starter" | "pro"        → subscription checkout
    plan_type: "credit_pack"            → one-time payment (10 credits)

    Requires Stripe to be configured. Returns 503 otherwise.
    """
    return _billing_service(db, settings).create_checkout_session(
        user_id=user.id,
        user_email=user.email,
        request=request,
    )


@router.post(
    "/customer-portal",
    response_model=CustomerPortalResponse,
    status_code=status.HTTP_200_OK,
)
def customer_portal(user: CurrentUserDep, db: DbDep, settings: SettingsDep):
    """
    Create a Stripe Customer Portal session for managing the user's subscription.

    Requires the user to have a stripe_customer_id (i.e. previously subscribed).
    Returns 404 if no billing record exists, 503 if Stripe is not configured.
    """
    return _billing_service(db, settings).create_customer_portal_session(user_id=user.id)
