"""
backend/tests/unit/test_usage_policy_service.py

Unit tests for UsagePolicyService.
The run_repo is mocked so no DB is required.
"""

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from backend.app.constants import FREE_TIER_GENERATION_LIMIT, PLAN_FREE, PLAN_STARTER
from backend.app.services.usage_policy_service import UsagePolicyService


def _make_billing(plan: str, status: str | None = None):
    b = MagicMock()
    b.plan_type = plan
    b.subscription_status = status
    return b


def _make_service(succeeded_count: int) -> UsagePolicyService:
    run_repo = MagicMock()
    run_repo.count_succeeded_by_user_id.return_value = succeeded_count
    return UsagePolicyService(run_repo)


class TestFreeTierPolicy:
    def test_under_limit_allowed(self):
        svc = _make_service(1)
        # Should not raise
        svc.check_can_generate("user-1", None)

    def test_at_limit_raises_402(self):
        svc = _make_service(FREE_TIER_GENERATION_LIMIT)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_can_generate("user-1", None)
        assert exc_info.value.status_code == 402

    def test_over_limit_raises_402(self):
        svc = _make_service(FREE_TIER_GENERATION_LIMIT + 5)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_can_generate("user-1", None)
        assert exc_info.value.status_code == 402

    def test_zero_used_allowed(self):
        svc = _make_service(0)
        svc.check_can_generate("user-1", None)

    def test_free_billing_record_respected(self):
        svc = _make_service(FREE_TIER_GENERATION_LIMIT)
        billing = _make_billing(PLAN_FREE)
        with pytest.raises(HTTPException) as exc_info:
            svc.check_can_generate("user-1", billing)
        assert exc_info.value.status_code == 402


class TestPaidPlanPolicy:
    def test_active_subscription_allowed(self):
        svc = _make_service(0)
        billing = _make_billing(PLAN_STARTER, "active")
        svc.check_can_generate("user-1", billing)

    def test_trialing_subscription_allowed(self):
        svc = _make_service(10)
        billing = _make_billing(PLAN_STARTER, "trialing")
        svc.check_can_generate("user-1", billing)

    def test_canceled_subscription_raises_402(self):
        svc = _make_service(0)
        billing = _make_billing(PLAN_STARTER, "canceled")
        with pytest.raises(HTTPException) as exc_info:
            svc.check_can_generate("user-1", billing)
        assert exc_info.value.status_code == 402

    def test_past_due_raises_402(self):
        svc = _make_service(0)
        billing = _make_billing(PLAN_STARTER, "past_due")
        with pytest.raises(HTTPException) as exc_info:
            svc.check_can_generate("user-1", billing)
        assert exc_info.value.status_code == 402

    def test_no_billing_with_paid_plan_raises_402(self):
        # billing=None is treated as free plan regardless
        svc = _make_service(FREE_TIER_GENERATION_LIMIT)
        with pytest.raises(HTTPException):
            svc.check_can_generate("user-1", None)


class TestFreeGenerationsUsed:
    def test_returns_count(self):
        svc = _make_service(3)
        assert svc.free_generations_used("user-1") == 3
