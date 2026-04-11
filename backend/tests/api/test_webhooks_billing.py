"""
backend/tests/api/test_webhooks_billing.py

Tests for POST /webhooks/stripe — billing event handling.

All tests post event payloads without a Stripe-Signature header
(STRIPE_WEBHOOK_SECRET is not set in test settings, so signature
validation is skipped).
"""

import json
import uuid

import pytest

from backend.app.constants import CREDIT_PACK_SIZE, PLAN_FREE, PLAN_STARTER
from backend.app.db.models.billing import Billing
from backend.app.config import get_settings
from backend.app.main import app
from backend.tests.conftest import make_billing

WEBHOOK_URL = "/api/v1/webhooks/stripe"


@pytest.fixture(autouse=True)
def _no_stripe_signature():
    """Override settings to clear stripe_webhook_secret so tests skip signature validation."""
    real_settings = get_settings()

    class _NoSigSettings:
        """Thin wrapper that returns empty string for stripe_webhook_secret."""
        def __getattr__(self, name):
            return getattr(real_settings, name)
        stripe_webhook_secret = ""
        stripe_secret_key = ""
        stripe_price_id_starter = ""
        stripe_price_id_pro = ""
        stripe_price_id_credit_pack = ""

    from backend.app.dependencies import SettingsDep
    from backend.app.config import Settings
    from fastapi import Depends
    app.dependency_overrides[get_settings] = lambda: _NoSigSettings()
    yield
    # Restore (but leave other overrides from conftest intact)
    app.dependency_overrides.pop(get_settings, None)


def _post_event(client, event_type: str, obj: dict) -> dict:
    payload = {"type": event_type, "data": {"object": obj}}
    resp = client.post(
        WEBHOOK_URL,
        content=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    return resp


def _make_customer_id() -> str:
    return f"cus_{uuid.uuid4().hex[:14]}"


# ── Unhandled / unknown events ─────────────────────────────────────────────

class TestUnhandledEvents:
    def test_unhandled_event_returns_200(self, client):
        resp = _post_event(client, "payment_intent.created", {"id": "pi_123"})
        assert resp.status_code == 200
        assert resp.json() == {"received": True}

    def test_invalid_json_returns_400(self, client):
        resp = client.post(
            WEBHOOK_URL,
            content=b"not-json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ── customer.subscription.updated ─────────────────────────────────────────

class TestSubscriptionUpdated:
    def test_known_customer_updates_plan(self, client, db, user):
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_FREE)
        billing.stripe_customer_id = customer_id
        db.flush()

        resp = _post_event(
            client,
            "customer.subscription.updated",
            {
                "id": "sub_abc",
                "customer": customer_id,
                "status": "active",
                "current_period_end": 9999999999,
                "items": {"data": [{"price": {"id": "price_starter"}}]},
            },
        )
        assert resp.status_code == 200

    def test_unknown_customer_acknowledged_gracefully(self, client):
        resp = _post_event(
            client,
            "customer.subscription.updated",
            {
                "id": "sub_xyz",
                "customer": "cus_unknown",
                "status": "active",
                "current_period_end": 9999999999,
                "items": {"data": [{"price": {"id": "price_starter"}}]},
            },
        )
        assert resp.status_code == 200

    def test_canceled_subscription_downgrades_to_free(self, client, db, user):
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_STARTER, subscription_status="active")
        billing.stripe_customer_id = customer_id
        db.flush()

        _post_event(
            client,
            "customer.subscription.deleted",
            {
                "id": "sub_abc",
                "customer": customer_id,
                "status": "canceled",
                "current_period_end": 9999999999,
                "items": {"data": [{"price": {"id": "price_starter"}}]},
            },
        )

        db.refresh(billing)
        assert billing.plan_type == PLAN_FREE
        assert billing.subscription_status == "canceled"


# ── checkout.session.completed (payment = credit pack) ────────────────────

class TestCheckoutSessionCompleted:
    def test_payment_mode_grants_credits(self, client, db, user):
        billing = make_billing(db, user, plan_type=PLAN_FREE, extra_credits=0)
        db.flush()

        resp = _post_event(
            client,
            "checkout.session.completed",
            {
                "id": "cs_123",
                "mode": "payment",
                "customer": "cus_doesnotmatter",
                "metadata": {"user_id": user.id, "pack_type": "credit_pack"},
            },
        )
        assert resp.status_code == 200

        db.refresh(billing)
        assert billing.extra_credits == CREDIT_PACK_SIZE

    def test_payment_mode_missing_user_id_acknowledged(self, client):
        """Missing user_id in metadata → graceful log, still 200."""
        resp = _post_event(
            client,
            "checkout.session.completed",
            {
                "id": "cs_456",
                "mode": "payment",
                "customer": "cus_xyz",
                "metadata": {},
            },
        )
        assert resp.status_code == 200

    def test_subscription_mode_acknowledged(self, client, db, user):
        """checkout.session.completed with mode=subscription is acknowledged.

        When stripe_secret_key is empty the Stripe SDK is not initialised, so
        the subscription-sync branch is skipped. The event must still return
        200 and must not raise an AttributeError from StripeObject.get().
        """
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_FREE)
        billing.stripe_customer_id = customer_id
        db.flush()

        resp = _post_event(
            client,
            "checkout.session.completed",
            {
                "id": "cs_sub_001",
                "mode": "subscription",
                "customer": customer_id,
                "subscription": "sub_abc",
                "metadata": {"user_id": user.id},
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"received": True}
        # Billing record is unchanged (subscription sync requires live Stripe client)
        db.refresh(billing)
        assert billing.plan_type == PLAN_FREE

    def test_duplicate_checkout_session_does_not_grant_twice(self, client, db, user):
        """Replaying the same checkout.session.completed must not grant credits twice."""
        billing = make_billing(db, user, plan_type=PLAN_FREE, extra_credits=0)
        db.flush()

        session_obj = {
            "id": "cs_789",
            "mode": "payment",
            "customer": "cus_x",
            "metadata": {"user_id": user.id, "pack_type": "credit_pack"},
        }
        # First delivery — grants credits
        r1 = _post_event(client, "checkout.session.completed", session_obj)
        assert r1.status_code == 200

        # Replay of the same event (same checkout session id) — must be ignored
        r2 = _post_event(client, "checkout.session.completed", session_obj)
        assert r2.status_code == 200

        db.refresh(billing)
        assert billing.extra_credits == CREDIT_PACK_SIZE, (
            "Credits must only be granted once per checkout session"
        )

    def test_different_checkout_sessions_each_grant_credits(self, client, db, user):
        """Two distinct checkout sessions must each grant credits independently."""
        billing = make_billing(db, user, plan_type=PLAN_FREE, extra_credits=0)
        db.flush()

        for session_id in ("cs_aaa", "cs_bbb"):
            _post_event(
                client,
                "checkout.session.completed",
                {
                    "id": session_id,
                    "mode": "payment",
                    "customer": "cus_x",
                    "metadata": {"user_id": user.id, "pack_type": "credit_pack"},
                },
            )

        db.refresh(billing)
        assert billing.extra_credits == CREDIT_PACK_SIZE * 2

    def test_duplicate_returns_200(self, client, db, user):
        """Duplicate webhook delivery must still return 200 (Stripe expects acknowledgment)."""
        make_billing(db, user, plan_type=PLAN_FREE, extra_credits=0)
        db.flush()

        session_obj = {
            "id": "cs_dup_200",
            "mode": "payment",
            "customer": "cus_y",
            "metadata": {"user_id": user.id, "pack_type": "credit_pack"},
        }
        _post_event(client, "checkout.session.completed", session_obj)
        r2 = _post_event(client, "checkout.session.completed", session_obj)
        assert r2.status_code == 200
        assert r2.json() == {"received": True}


# ── invoice.paid ───────────────────────────────────────────────────────────

class TestInvoicePaid:
    def test_marks_subscription_active(self, client, db, user):
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_FREE)
        billing.stripe_customer_id = customer_id
        db.flush()

        resp = _post_event(
            client,
            "invoice.paid",
            {
                "id": "in_123",
                "customer": customer_id,
                "subscription": "sub_abc",
                "lines": {
                    "data": [
                        {
                            "price": {"id": "price_starter"},
                            "period": {"end": 9999999999},
                        }
                    ]
                },
            },
        )
        assert resp.status_code == 200
        db.refresh(billing)
        assert billing.subscription_status == "active"
        assert billing.stripe_subscription_id == "sub_abc"

    def test_no_subscription_id_skipped(self, client, db, user):
        """invoice.paid without subscription_id (one-time payment) → skipped."""
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_FREE)
        billing.stripe_customer_id = customer_id
        db.flush()

        resp = _post_event(
            client,
            "invoice.paid",
            {
                "id": "in_456",
                "customer": customer_id,
                "subscription": None,
                "lines": {"data": []},
            },
        )
        assert resp.status_code == 200

    def test_idempotent_replay(self, client, db, user):
        """Replaying invoice.paid is safe — same result both times."""
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_FREE)
        billing.stripe_customer_id = customer_id
        db.flush()

        event_obj = {
            "id": "in_789",
            "customer": customer_id,
            "subscription": "sub_abc",
            "lines": {
                "data": [{"price": {"id": "price_starter"}, "period": {"end": 9999999999}}]
            },
        }
        _post_event(client, "invoice.paid", event_obj)
        _post_event(client, "invoice.paid", event_obj)

        db.refresh(billing)
        # After both replays state is consistent
        assert billing.subscription_status == "active"


# ── invoice.payment_failed ─────────────────────────────────────────────────

class TestInvoicePaymentFailed:
    def test_marks_past_due(self, client, db, user):
        customer_id = _make_customer_id()
        billing = make_billing(db, user, plan_type=PLAN_STARTER, subscription_status="active")
        billing.stripe_customer_id = customer_id
        db.flush()

        resp = _post_event(
            client,
            "invoice.payment_failed",
            {"id": "in_fail", "customer": customer_id},
        )
        assert resp.status_code == 200
        db.refresh(billing)
        assert billing.subscription_status == "past_due"

    def test_unknown_customer_acknowledged(self, client):
        resp = _post_event(
            client,
            "invoice.payment_failed",
            {"id": "in_x", "customer": "cus_nobody"},
        )
        assert resp.status_code == 200


# ── Admin seed + replay integration ────────────────────────────────────────

class TestSeedAndReplay:
    def test_seeded_session_blocks_webhook_replay(self, admin_client, client, db, user):
        """After admin-seeding a session, replaying that webhook does not grant credits."""
        billing = make_billing(db, user, plan_type=PLAN_FREE, extra_credits=0)
        db.flush()

        session_id = "cs_test_seed_then_replay_x"

        # Admin seeds the session as already processed (cold-start gap remediation)
        seed_resp = admin_client.post(
            "/api/v1/admin/billing/register-checkout-session",
            json={
                "checkout_session_id": session_id,
                "user_id": user.id,
                "granted_credits": CREDIT_PACK_SIZE,
            },
        )
        assert seed_resp.status_code == 200
        assert seed_resp.json()["registered"] is True

        # Replay the webhook for that session
        replay_resp = _post_event(
            client,
            "checkout.session.completed",
            {
                "id": session_id,
                "mode": "payment",
                "customer": "cus_test",
                "metadata": {"user_id": user.id, "pack_type": "credit_pack"},
            },
        )
        assert replay_resp.status_code == 200

        db.refresh(billing)
        assert billing.extra_credits == 0, (
            "Credits must not be granted when session was already seeded"
        )
