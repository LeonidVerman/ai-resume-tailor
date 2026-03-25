"""
backend/tests/api/test_billing_api.py

API tests for GET /billing/status, POST /billing/create-checkout-session,
POST /billing/customer-portal, and POST /admin/billing/grant-credits.
"""

import uuid

import pytest

from backend.app.constants import PLAN_FREE, PLAN_STARTER
from backend.app.db.models.billing import Billing
from backend.tests.conftest import make_billing

API = "/api/v1/billing"


# ── GET /billing/status ────────────────────────────────────────────────────

class TestBillingStatus:
    def test_returns_200(self, client):
        resp = client.get(f"{API}/status")
        assert resp.status_code == 200

    def test_response_has_required_fields(self, client):
        resp = client.get(f"{API}/status")
        data = resp.json()
        for field in ("plan_type", "monthly_used", "monthly_limit", "extra_credits"):
            assert field in data, f"Missing field: {field}"

    def test_free_plan_by_default(self, client):
        resp = client.get(f"{API}/status")
        assert resp.json()["plan_type"] == "free"

    def test_monthly_limit_matches_free(self, client):
        from backend.app.constants import PLAN_MONTHLY_LIMITS
        resp = client.get(f"{API}/status")
        assert resp.json()["monthly_limit"] == PLAN_MONTHLY_LIMITS[PLAN_FREE]

    def test_monthly_used_starts_at_zero(self, client):
        resp = client.get(f"{API}/status")
        assert resp.json()["monthly_used"] == 0

    def test_extra_credits_shown(self, client, db, user):
        make_billing(db, user, plan_type=PLAN_FREE, extra_credits=5)
        resp = client.get(f"{API}/status")
        assert resp.json()["extra_credits"] == 5

    def test_starter_plan_reflected(self, client, db, user):
        make_billing(db, user, plan_type=PLAN_STARTER, subscription_status="active")
        from backend.app.constants import PLAN_MONTHLY_LIMITS
        resp = client.get(f"{API}/status")
        data = resp.json()
        assert data["plan_type"] == PLAN_STARTER
        assert data["monthly_limit"] == PLAN_MONTHLY_LIMITS[PLAN_STARTER]

    def test_unauthenticated_returns_401(self, anon_client):
        resp = anon_client.get(f"{API}/status")
        assert resp.status_code == 401


# ── POST /billing/create-checkout-session ─────────────────────────────────

class TestCreateCheckoutSession:
    def test_returns_503_without_stripe(self, client):
        """Stripe not configured → 503."""
        resp = client.post(
            f"{API}/create-checkout-session",
            json={"plan_type": "starter", "success_url": "http://x.com/ok", "cancel_url": "http://x.com/cancel"},
        )
        assert resp.status_code == 503

    def test_credit_pack_503_without_stripe(self, client):
        resp = client.post(
            f"{API}/create-checkout-session",
            json={"plan_type": "credit_pack"},
        )
        assert resp.status_code == 503

    def test_invalid_plan_type_422(self, client):
        resp = client.post(
            f"{API}/create-checkout-session",
            json={"plan_type": "unknown"},
        )
        assert resp.status_code == 422

    def test_unauthenticated_returns_401(self, anon_client):
        resp = anon_client.post(
            f"{API}/create-checkout-session",
            json={"plan_type": "starter"},
        )
        assert resp.status_code == 401


# ── POST /billing/customer-portal ─────────────────────────────────────────

class TestCustomerPortal:
    def test_no_billing_returns_503_or_404(self, client):
        """No Stripe configured AND no billing row → 503 (Stripe checked first)."""
        resp = client.post(f"{API}/customer-portal")
        assert resp.status_code in (503, 404)

    def test_unauthenticated_returns_401(self, anon_client):
        resp = anon_client.post(f"{API}/customer-portal")
        assert resp.status_code == 401


# ── POST /admin/billing/grant-credits ─────────────────────────────────────

class TestGrantCredits:
    def test_admin_can_grant_credits(self, admin_client, db, user):
        resp = admin_client.post(
            "/api/v1/admin/billing/grant-credits",
            json={"user_id": user.id, "amount": 10},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["credits_granted"] == 10
        assert data["user_id"] == user.id

    def test_credits_reflected_in_billing_status(self, admin_client, client, db, user):
        admin_client.post(
            "/api/v1/admin/billing/grant-credits",
            json={"user_id": user.id, "amount": 7},
        )
        resp = client.get(f"{API}/status")
        assert resp.json()["extra_credits"] == 7

    def test_non_admin_returns_403(self, client, user):
        resp = client.post(
            "/api/v1/admin/billing/grant-credits",
            json={"user_id": user.id, "amount": 5},
        )
        assert resp.status_code == 403

    def test_zero_amount_rejected(self, admin_client, user):
        resp = admin_client.post(
            "/api/v1/admin/billing/grant-credits",
            json={"user_id": user.id, "amount": 0},
        )
        assert resp.status_code == 400

    def test_negative_amount_rejected(self, admin_client, user):
        resp = admin_client.post(
            "/api/v1/admin/billing/grant-credits",
            json={"user_id": user.id, "amount": -1},
        )
        assert resp.status_code == 400
