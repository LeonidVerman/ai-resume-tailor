"""
backend/tests/unit/test_usage_policy_service.py

Unit tests for UsagePolicyService (v2 — monthly quota + credits).
Both repositories are mocked; no DB required.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.app.constants import PLAN_FREE, PLAN_MONTHLY_LIMITS, PLAN_PRO, PLAN_STARTER
from backend.app.services.usage_policy_service import UsagePolicyService


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_billing(
    plan: str = PLAN_FREE,
    extra_credits: int = 0,
    monthly_limit_override: int | None = None,
):
    b = MagicMock()
    b.plan_type = plan
    b.extra_credits = extra_credits
    b.monthly_limit_override = monthly_limit_override
    return b


def _make_service(
    increment_result: bool = True,
    decrement_result: bool = False,
    current_count: int = 0,
) -> UsagePolicyService:
    billing_repo = MagicMock()
    billing_repo.decrement_credits_atomic.return_value = decrement_result

    monthly_repo = MagicMock()
    monthly_repo.increment_atomic.return_value = increment_result
    monthly_repo.get_count.return_value = current_count

    return UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)


# ── check_and_consume ──────────────────────────────────────────────────────

class TestCheckAndConsume:
    def test_monthly_quota_available_passes(self):
        """Monthly quota still available → increment succeeds, no exception."""
        svc = _make_service(increment_result=True)
        svc.check_and_consume("user-1", None)  # should not raise

    def test_monthly_quota_exhausted_credits_consumed(self):
        """Monthly quota full but credits available → decrement credits, no exception."""
        svc = _make_service(increment_result=False, decrement_result=True)
        billing = _make_billing(extra_credits=3)
        svc.check_and_consume("user-1", billing)  # should not raise

    def test_all_exhausted_raises_429(self):
        """Both quota and credits exhausted → HTTP 429."""
        svc = _make_service(increment_result=False, decrement_result=False, current_count=3)
        billing = _make_billing(extra_credits=0)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_and_consume("user-1", billing)
        assert exc_info.value.status_code == 429

    def test_429_has_error_code(self):
        svc = _make_service(increment_result=False, decrement_result=False, current_count=3)
        billing = _make_billing()
        with pytest.raises(HTTPException) as exc_info:
            svc.check_and_consume("user-1", billing)
        detail = exc_info.value.detail
        assert detail["error_code"] == "QUOTA_EXCEEDED"

    def test_429_detail_contains_usage_info(self):
        svc = _make_service(increment_result=False, decrement_result=False, current_count=3)
        billing = _make_billing(plan=PLAN_FREE, extra_credits=0)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_and_consume("user-1", billing)
        detail = exc_info.value.detail
        assert "monthly_used" in detail
        assert "monthly_limit" in detail
        assert "extra_credits" in detail
        assert detail["plan"] == PLAN_FREE

    def test_no_billing_treated_as_free_limit(self):
        """billing=None → limit defaults to PLAN_MONTHLY_LIMITS['free']."""
        # At limit with no credits: should 429
        svc = _make_service(increment_result=False, decrement_result=False, current_count=3)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_and_consume("user-1", None)
        assert exc_info.value.status_code == 429

    def test_monthly_limit_override_used(self):
        """billing.monthly_limit_override takes precedence over plan default."""
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.increment_atomic.return_value = True

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        billing = _make_billing(plan=PLAN_FREE, monthly_limit_override=10)
        svc.check_and_consume("user-1", billing)

        # Verify the limit passed to increment_atomic was 10 (override), not 3 (free default)
        call_kwargs = monthly_repo.increment_atomic.call_args
        assert call_kwargs[0][3] == 10 or call_kwargs[1].get("limit") == 10

    def test_starter_plan_limit_correct(self):
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.increment_atomic.return_value = True

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        billing = _make_billing(plan=PLAN_STARTER)
        svc.check_and_consume("user-1", billing)

        call_kwargs = monthly_repo.increment_atomic.call_args
        limit_arg = call_kwargs[0][3] if len(call_kwargs[0]) > 3 else call_kwargs[1].get("limit")
        assert limit_arg == PLAN_MONTHLY_LIMITS[PLAN_STARTER]  # 40

    def test_pro_plan_limit_correct(self):
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.increment_atomic.return_value = True

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        billing = _make_billing(plan=PLAN_PRO)
        svc.check_and_consume("user-1", billing)

        call_kwargs = monthly_repo.increment_atomic.call_args
        limit_arg = call_kwargs[0][3] if len(call_kwargs[0]) > 3 else call_kwargs[1].get("limit")
        assert limit_arg == PLAN_MONTHLY_LIMITS[PLAN_PRO]  # 200

    def test_no_billing_no_credits_attempt(self):
        """When billing=None, decrement_credits_atomic should not be called."""
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.increment_atomic.return_value = False
        monthly_repo.get_count.return_value = 3

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        with pytest.raises(HTTPException):
            svc.check_and_consume("user-1", None)
        billing_repo.decrement_credits_atomic.assert_not_called()


# ── get_usage_summary ──────────────────────────────────────────────────────

class TestGetUsageSummary:
    def test_returns_correct_fields(self):
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.get_count.return_value = 2

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        billing = _make_billing(plan=PLAN_FREE, extra_credits=5)
        summary = svc.get_usage_summary("user-1", billing)

        assert summary["monthly_used"] == 2
        assert summary["monthly_limit"] == PLAN_MONTHLY_LIMITS[PLAN_FREE]
        assert summary["extra_credits"] == 5

    def test_no_billing_defaults(self):
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.get_count.return_value = 0

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        summary = svc.get_usage_summary("user-1", None)

        assert summary["monthly_used"] == 0
        assert summary["monthly_limit"] == PLAN_MONTHLY_LIMITS[PLAN_FREE]
        assert summary["extra_credits"] == 0

    def test_override_reflected_in_summary(self):
        billing_repo = MagicMock()
        monthly_repo = MagicMock()
        monthly_repo.get_count.return_value = 5

        svc = UsagePolicyService(billing_repo=billing_repo, monthly_usage_repo=monthly_repo)
        billing = _make_billing(plan=PLAN_FREE, monthly_limit_override=50)
        summary = svc.get_usage_summary("user-1", billing)

        assert summary["monthly_limit"] == 50


# ── _resolve_limit ─────────────────────────────────────────────────────────

class TestResolveLimit:
    def test_free_default(self):
        assert UsagePolicyService._resolve_limit(None) == PLAN_MONTHLY_LIMITS[PLAN_FREE]

    def test_starter_default(self):
        b = _make_billing(plan=PLAN_STARTER)
        assert UsagePolicyService._resolve_limit(b) == PLAN_MONTHLY_LIMITS[PLAN_STARTER]

    def test_override_beats_plan_default(self):
        b = _make_billing(plan=PLAN_PRO, monthly_limit_override=5)
        assert UsagePolicyService._resolve_limit(b) == 5
