"""
backend/app/services/billing_service.py

Billing service — manages Stripe subscription state and billing records.

Responsibilities
----------------
- Read current billing/plan status (including monthly usage)
- Create Stripe checkout sessions (subscription and one-time payment)
- Create Stripe Customer Portal sessions
- Handle webhook events: subscription lifecycle, invoice events, checkout completion
- Sync subscription state into the local Billing DB record

PRICE_ID → PLAN mapping is built explicitly from settings at instantiation;
no global mutable state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from backend.app.constants import CREDIT_PACK_SIZE, PLAN_FREE, PLAN_PRO, PLAN_STARTER
from backend.app.db.models.billing import Billing
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.schemas.billing import (
    BillingStatus,
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CustomerPortalResponse,
    UpgradePlanRequest,
    UpgradePlanResponse,
)

logger = logging.getLogger(__name__)


class BillingService:
    """
    Billing and subscription management.

    Parameters
    ----------
    billing_repo:   BillingRepository
    usage_service:  UsagePolicyService  — for monthly usage summary
    stripe_client:  StripeClient | None — None when Stripe is not configured
    settings:       Settings            — needed to build price-to-plan mapping
    """

    def __init__(
        self,
        billing_repo: BillingRepository,
        usage_service=None,
        stripe_client=None,
        settings=None,
    ) -> None:
        self._repo = billing_repo
        self._usage = usage_service
        self._stripe = stripe_client

        # Build explicit price_id → plan_type mapping from settings
        self._price_to_plan: dict[str, str] = {}
        if settings:
            for price_id, plan in (
                (settings.stripe_price_id_starter, PLAN_STARTER),
                (settings.stripe_price_id_pro, PLAN_PRO),
            ):
                if price_id:
                    self._price_to_plan[price_id] = plan

        self._credit_pack_price_id: str = (
            getattr(settings, "stripe_price_id_credit_pack", "") if settings else ""
        )
        self._app_base_url: str = (
            getattr(settings, "app_base_url", "http://localhost:3000") if settings else "http://localhost:3000"
        )

    # ── Read ───────────────────────────────────────────────────────────────

    def get_status(self, user_id: str) -> BillingStatus:
        """Return the user's current plan, subscription state, and usage."""
        billing = self._repo.get_by_user_id(user_id)
        plan = billing.plan_type if billing else PLAN_FREE

        usage: dict = {"monthly_used": 0, "monthly_limit": 3, "extra_credits": 0}
        if self._usage is not None:
            usage = self._usage.get_usage_summary(user_id, billing)

        return BillingStatus(
            user_id=user_id,
            plan_type=plan,
            subscription_status=billing.subscription_status if billing else None,
            current_period_end=billing.current_period_end if billing else None,
            stripe_customer_id=billing.stripe_customer_id if billing else None,
            monthly_used=usage["monthly_used"],
            monthly_limit=usage["monthly_limit"],
            extra_credits=usage["extra_credits"],
        )

    # ── Checkout ───────────────────────────────────────────────────────────

    def create_checkout_session(
        self, user_id: str, user_email: str, request: CheckoutSessionRequest
    ) -> CheckoutSessionResponse:
        """Create a Stripe Checkout session for a plan upgrade or credit pack."""
        self._require_stripe()

        success_url = request.success_url or f"{self._app_base_url}/billing?success=1"
        cancel_url = request.cancel_url or f"{self._app_base_url}/billing"

        # Validate price IDs BEFORE making any Stripe API calls
        if request.plan_type == "credit_pack":
            if not self._credit_pack_price_id:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Credit pack price is not configured (STRIPE_PRICE_ID_CREDIT_PACK)",
                )
            price_id = self._credit_pack_price_id
            is_payment = True
        else:
            price_id = self._price_id_for_plan(request.plan_type)
            is_payment = False

        customer_id = self._stripe.get_or_create_customer(user_email, user_id)

        # Upsert billing record so webhooks can find it by stripe_customer_id.
        # Without this, checkout.session.completed / invoice.paid look up by
        # customer_id, find nothing, and silently skip all plan/credit updates.
        billing = self._repo.get_by_user_id(user_id)
        if billing is None:
            self._repo.create(
                user_id=user_id,
                plan_type=PLAN_FREE,
                stripe_customer_id=customer_id,
            )
        elif not billing.stripe_customer_id:
            self._repo.update(billing, stripe_customer_id=customer_id)

        if is_payment:
            session = self._stripe.create_payment_checkout_session(
                price_id=price_id,
                customer_id=customer_id,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"user_id": user_id, "pack_type": "credit_pack"},
            )
        else:
            session = self._stripe.create_checkout_session(
                price_id=price_id,
                customer_id=customer_id,
                success_url=success_url,
                cancel_url=cancel_url,
                metadata={"user_id": user_id},
            )

        logger.info("Created checkout session user=%s plan=%s", user_id, request.plan_type)
        return CheckoutSessionResponse(
            checkout_url=session.checkout_url,
            session_id=session.session_id,
        )

    def upgrade_plan(
        self, user_id: str, request: UpgradePlanRequest
    ) -> UpgradePlanResponse:
        """Upgrade an active paid subscription to a higher plan (e.g. starter → pro).

        Calls Stripe Subscription.modify() with proration_behavior='always_invoice'
        so the prorated net difference is charged immediately.
        Also syncs the billing record locally so the UI reflects the change
        without waiting for the webhook.

        Raises 400 if user is not on a paid plan or already on the target plan.
        Raises 404 if no billing record / subscription exists.
        Raises 503 if Stripe is not configured.
        """
        self._require_stripe()

        billing = self._repo.get_by_user_id(user_id)
        if billing is None or not billing.stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No billing record found. Subscribe to a plan first.",
            )

        if billing.plan_type == request.plan_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Already on the {request.plan_type} plan.",
            )

        if billing.plan_type == PLAN_FREE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot upgrade directly from Free. Subscribe to a plan first.",
            )

        if not billing.stripe_subscription_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No active subscription found to upgrade.",
            )

        if billing.subscription_status not in ("active", "trialing"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Subscription is not active (status={billing.subscription_status}). "
                       "Manage billing to resolve.",
            )

        new_price_id = self._price_id_for_plan(request.plan_type)

        try:
            updated_sub = self._stripe.upgrade_subscription(
                subscription_id=billing.stripe_subscription_id,
                new_price_id=new_price_id,
            )
        except Exception as exc:
            logger.error("Stripe upgrade failed user=%s: %s", user_id, exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Stripe upgrade failed: {exc}",
            )

        # Sync billing record immediately (webhook will also fire as safety net)
        self._sync_subscription_to_billing(billing.stripe_customer_id, updated_sub)

        logger.info("Plan upgraded user=%s -> %s", user_id, request.plan_type)
        return UpgradePlanResponse(
            ok=True,
            plan_type=request.plan_type,
            message=f"Successfully upgraded to {request.plan_type}.",
        )

    def create_customer_portal_session(
        self, user_id: str, return_url: str | None = None
    ) -> CustomerPortalResponse:
        """Create a Stripe Customer Portal session for subscription management."""
        self._require_stripe()

        billing = self._repo.get_by_user_id(user_id)
        if billing is None or not billing.stripe_customer_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No billing record found. Subscribe to a plan first.",
            )

        portal_return_url = return_url or f"{self._app_base_url}/billing"
        portal_url = self._stripe.create_customer_portal_session(
            customer_id=billing.stripe_customer_id,
            return_url=portal_return_url,
        )
        return CustomerPortalResponse(portal_url=portal_url)

    # ── Webhook event handlers ──────────────────────────────────────────────

    def handle_checkout_completed(self, stripe_event: dict) -> None:
        """
        Handle checkout.session.completed.

        - mode=payment  → grant CREDIT_PACK_SIZE extra credits to the user
        - mode=subscription → belt-and-suspenders plan sync (invoice.paid is primary)
        """
        session = stripe_event.get("data", {}).get("object", {})
        mode = session.get("mode")
        customer_id = session.get("customer")
        if not customer_id:
            logger.warning("checkout.session.completed missing customer_id")
            return

        if mode == "payment":
            metadata = session.get("metadata", {})
            user_id = metadata.get("user_id")
            if not user_id:
                logger.warning("checkout.session.completed payment missing user_id in metadata")
                return
            self._repo.add_credits(user_id, CREDIT_PACK_SIZE)
            logger.info(
                "Granted %d credits to user=%s (checkout.session.completed)",
                CREDIT_PACK_SIZE, user_id,
            )

        elif mode == "subscription":
            # Upsert billing record from session metadata so the webhook can
            # sync even when checkout.session.completed fires before any prior
            # billing row exists (e.g. first-ever subscription, or replayed events).
            user_id = session.get("metadata", {}).get("user_id")
            if user_id:
                billing = self._repo.get_by_user_id(user_id)
                if billing is None:
                    self._repo.create(
                        user_id=user_id,
                        plan_type=PLAN_FREE,
                        stripe_customer_id=customer_id,
                    )
                elif not billing.stripe_customer_id:
                    self._repo.update(billing, stripe_customer_id=customer_id)

            # Sync plan from subscription as a belt-and-suspenders measure
            sub_id = session.get("subscription")
            if sub_id and self._stripe:
                try:
                    import stripe as stripe_sdk
                    stripe_sdk.api_key = self._stripe._secret_key
                    sub = stripe_sdk.Subscription.retrieve(sub_id)
                    # Stripe API 2025+: current_period_end moved from subscription
                    # top-level to subscription_items. Try item first, fall back.
                    period_end = None
                    if sub.items.data:
                        period_end = getattr(sub.items.data[0], "current_period_end", None)
                    if period_end is None:
                        period_end = getattr(sub, "current_period_end", None)
                    sub_dict = {
                        "id": sub.id,
                        "status": sub.status,
                        "current_period_end": period_end,
                        "items": {"data": [{"price": {"id": sub.items.data[0].price.id}}]} if sub.items.data else {"data": []},
                    }
                    self._sync_subscription_to_billing(customer_id, sub_dict)
                except Exception as exc:
                    logger.warning(
                        "Could not retrieve subscription %s after checkout: %s", sub_id, exc
                    )

    def handle_invoice_paid(self, stripe_event: dict) -> None:
        """
        Handle invoice.paid — primary subscription activation signal.

        Upserts billing record with active plan derived from subscription price.
        Skips non-subscription invoices (one-time payments handled by checkout.session.completed).
        Idempotent: safe to replay.
        """
        invoice = stripe_event.get("data", {}).get("object", {})
        customer_id = invoice.get("customer")
        if not customer_id:
            return

        subscription_id = invoice.get("subscription")
        if not subscription_id:
            logger.debug("invoice.paid has no subscription_id — skipping")
            return

        billing = self._repo.get_by_stripe_customer_id(customer_id)
        if billing is None:
            logger.warning("invoice.paid: no billing record for customer %s", customer_id)
            return

        lines = invoice.get("lines", {}).get("data", [])
        price_id = lines[0].get("price", {}).get("id") if lines else None
        plan = self._price_to_plan.get(price_id, billing.plan_type) if price_id else billing.plan_type

        # Period end from the subscription line item
        period_end_ts = lines[0].get("period", {}).get("end") if lines else None
        period_end = (
            datetime.fromtimestamp(period_end_ts, tz=timezone.utc) if period_end_ts else None
        )

        self._repo.update(
            billing,
            stripe_subscription_id=subscription_id,
            stripe_price_id=price_id,
            subscription_status="active",
            plan_type=plan,
            current_period_end=period_end,
        )
        logger.info(
            "invoice.paid: customer=%s plan=%s subscription=%s",
            customer_id, plan, subscription_id,
        )

    def handle_subscription_updated(self, stripe_event: dict) -> None:
        """
        Handle customer.subscription.created / updated / deleted.

        Syncs subscription state into the billing record.
        Idempotent: safe to replay.
        """
        sub = stripe_event.get("data", {}).get("object", {})
        customer_id = sub.get("customer")
        if not customer_id:
            logger.warning("Subscription webhook missing customer_id")
            return

        billing = self._repo.get_by_stripe_customer_id(customer_id)
        if billing is None:
            logger.warning("No billing record for Stripe customer %s", customer_id)
            return

        self._sync_subscription_to_billing(customer_id, sub)

    def handle_invoice_payment_failed(self, stripe_event: dict) -> None:
        """Handle invoice.payment_failed — mark subscription as past_due."""
        invoice = stripe_event.get("data", {}).get("object", {})
        customer_id = invoice.get("customer")
        if not customer_id:
            return

        billing = self._repo.get_by_stripe_customer_id(customer_id)
        if billing is None:
            logger.warning("invoice.payment_failed: no billing record for customer %s", customer_id)
            return

        self._repo.update(billing, subscription_status="past_due")
        logger.info("invoice.payment_failed: customer=%s marked past_due", customer_id)

    # ── Internal ───────────────────────────────────────────────────────────

    def _sync_subscription_to_billing(self, customer_id: str, sub: dict) -> None:
        """Sync a Stripe subscription object (or dict) into the billing record."""
        billing = self._repo.get_by_stripe_customer_id(customer_id)
        if billing is None:
            return

        sub_status = sub.get("status")
        # Stripe API 2025+: current_period_end moved to subscription_items.
        # Fall back to items when missing at the subscription top-level.
        period_end_ts = sub.get("current_period_end")
        if period_end_ts is None:
            items_data = sub.get("items", {}).get("data", [])
            period_end_ts = items_data[0].get("current_period_end") if items_data else None
        period_end = (
            datetime.fromtimestamp(period_end_ts, tz=timezone.utc)
            if period_end_ts
            else None
        )

        items = sub.get("items", {}).get("data", [])
        price_id = items[0].get("price", {}).get("id") if items else None
        plan = self._price_to_plan.get(price_id, billing.plan_type) if price_id else billing.plan_type

        if sub_status in ("canceled", "unpaid"):
            plan = PLAN_FREE

        self._repo.update(
            billing,
            stripe_subscription_id=sub.get("id"),
            stripe_price_id=price_id,
            subscription_status=sub_status,
            plan_type=plan,
            current_period_end=period_end,
        )
        logger.info(
            "Synced subscription customer=%s status=%s plan=%s",
            customer_id, sub_status, plan,
        )

    def _require_stripe(self) -> None:
        if self._stripe is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Billing is not configured on this server",
            )

    def _price_id_for_plan(self, plan_type: str) -> str:
        """Return the Stripe price ID for a subscription plan type."""
        reverse = {v: k for k, v in self._price_to_plan.items()}
        price_id = reverse.get(plan_type, "")
        if not price_id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Stripe price ID for plan '{plan_type}' is not configured",
            )
        return price_id
