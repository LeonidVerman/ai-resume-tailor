"""
backend/app/api/webhooks.py

Stripe webhook endpoint.

Endpoint
--------
POST /webhooks/stripe   — receive and process Stripe webhook events

Handled events
--------------
  customer.subscription.created   — sync subscription state
  customer.subscription.updated   — sync subscription state
  customer.subscription.deleted   — downgrade to free
  checkout.session.completed      — grant credits (payment) or sync plan (subscription)
  invoice.paid                    — primary subscription activation signal
  invoice.payment_failed          — mark subscription past_due

All other events are acknowledged (200 OK) without processing.

Idempotency: all handlers perform upsert/update operations; safe to replay.
"""

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from backend.app.dependencies import DbDep, SettingsDep
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.monthly_usage_repository import MonthlyUsageRepository
from backend.app.services.billing_service import BillingService
from backend.app.services.usage_policy_service import UsagePolicyService

logger = logging.getLogger(__name__)

router = APIRouter()

_HANDLED_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "checkout.session.completed",
    "invoice.paid",
    "invoice.payment_failed",
}


def _make_billing_service(db, settings) -> BillingService:
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


@router.post("/stripe", status_code=200)
async def stripe_webhook(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    """
    Receive Stripe webhook events and update subscription/credit state.

    Verifies the Stripe-Signature header when STRIPE_WEBHOOK_SECRET is set.
    Returns 400 if signature verification fails.
    Returns 200 for all valid payloads (including unhandled event types).
    """
    raw_body = await request.body()

    # ── Validate signature ─────────────────────────────────────────────────
    if settings.stripe_webhook_secret:
        if not stripe_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing Stripe-Signature header",
            )
        try:
            from backend.app.clients.stripe_client import StripeClient
            stripe_client = StripeClient(
                secret_key=settings.stripe_secret_key,
                webhook_secret=settings.stripe_webhook_secret,
            )
            # Verify signature only — construct_event returns a StripeObject,
            # not a plain dict, so we parse raw_body ourselves after verification.
            stripe_client.construct_webhook_event(
                payload=raw_body,
                sig_header=stripe_signature,
            )
            event_dict = json.loads(raw_body)
        except Exception as exc:
            logger.warning("Stripe webhook signature validation failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Webhook signature invalid: {exc}",
            )
    else:
        try:
            event_dict = json.loads(raw_body)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            )

    event_type = event_dict.get("type", "")
    logger.info("Stripe webhook received: type=%s", event_type)

    if event_type not in _HANDLED_EVENTS:
        logger.debug("Unhandled Stripe event type: %s — acknowledged", event_type)
        return {"received": True}

    # ── Dispatch to billing service ────────────────────────────────────────
    svc = _make_billing_service(db, settings)

    if event_type in (
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        svc.handle_subscription_updated(event_dict)

    elif event_type == "checkout.session.completed":
        svc.handle_checkout_completed(event_dict)

    elif event_type == "invoice.paid":
        svc.handle_invoice_paid(event_dict)

    elif event_type == "invoice.payment_failed":
        svc.handle_invoice_payment_failed(event_dict)

    return {"received": True}
