"""
backend/app/api/webhooks.py

Stripe webhook endpoint.

Endpoint
--------
POST /webhooks/stripe   — receive and process Stripe webhook events

Phase 8 status: STRUCTURALLY CORRECT (signature validation + billing sync)
---------------------------------------------------------------------------
Webhook signature validation is performed when STRIPE_WEBHOOK_SECRET is set.
Unrecognised event types are silently acknowledged (200 OK) to avoid Stripe
retry storms.

Currently handled events:
  - customer.subscription.created
  - customer.subscription.updated
  - customer.subscription.deleted

All other events are acknowledged without processing.
"""

import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from backend.app.dependencies import DbDep, SettingsDep
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.services.billing_service import BillingService

logger = logging.getLogger(__name__)

router = APIRouter()

_HANDLED_EVENTS = {
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
}


@router.post("/stripe", status_code=200)
async def stripe_webhook(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
):
    """
    Receive Stripe webhook events and update subscription state.

    Verifies the Stripe-Signature header when STRIPE_WEBHOOK_SECRET is set.
    Returns 400 if signature verification fails.
    Returns 200 for all valid payloads (including unhandled event types).
    """
    raw_body = await request.body()

    # ── Validate signature (when secret is configured) ─────────────────────
    if settings.stripe_webhook_secret:
        if not stripe_signature:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing Stripe-Signature header",
            )
        try:
            from backend.app.clients.stripe_client import StripeClient
            stripe_client = StripeClient(api_key=settings.stripe_secret_key)
            event = stripe_client.construct_webhook_event(
                payload=raw_body,
                sig_header=stripe_signature,
            )
            event_dict = event  # StripeClient returns a dict-like event
        except Exception as exc:
            logger.warning("Stripe webhook signature validation failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Webhook signature invalid: {exc}",
            )
    else:
        # No secret configured — accept body as-is (dev/test mode)
        import json
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

    # ── Delegate to billing service ────────────────────────────────────────
    stripe_client_for_billing = None
    if settings.stripe_secret_key:
        from backend.app.clients.stripe_client import StripeClient
        stripe_client_for_billing = StripeClient(api_key=settings.stripe_secret_key)

    billing_svc = BillingService(BillingRepository(db), stripe_client_for_billing)
    billing_svc.handle_subscription_updated(event_dict)

    return {"received": True}
