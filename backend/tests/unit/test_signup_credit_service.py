"""
backend/tests/unit/test_signup_credit_service.py

Unit tests for SignupCreditService.
All DB interactions are mocked — no DB required.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.signup_credit_service import SignupCreditService


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_admin_config(initial_credits: int):
    cfg = MagicMock()
    cfg.initial_credits = initial_credits
    return cfg


# ── get_initial_credits ────────────────────────────────────────────────────

class TestGetInitialCredits:
    def test_returns_configured_amount(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        mock_repo = MagicMock()
        mock_repo.get.return_value = _make_admin_config(10)

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            result = svc.get_initial_credits()

        assert result == 10

    def test_returns_zero_when_configured_zero(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        mock_repo = MagicMock()
        mock_repo.get.return_value = _make_admin_config(0)

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            result = svc.get_initial_credits()

        assert result == 0

    def test_returns_default_amount(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        mock_repo = MagicMock()
        mock_repo.get.return_value = _make_admin_config(3)

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            result = svc.get_initial_credits()

        assert result == 3


# ── grant_initial_credits ──────────────────────────────────────────────────

class TestGrantInitialCredits:
    def test_grants_configured_credits(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(10)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-123")

        billing_repo.grant_initial_signup_credits.assert_called_once_with(
            user_id="user-123",
            amount=10,
            mode=None,
            monthly_limit_override=0,  # non-zero amount suppresses monthly quota
        )

    def test_grants_three_credits_with_default_config(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(3)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-456")

        billing_repo.grant_initial_signup_credits.assert_called_once_with(
            user_id="user-456",
            amount=3,
            mode=None,
            monthly_limit_override=0,
        )

    def test_zero_credits_does_not_set_monthly_override(self):
        """When initial_credits=0, monthly_limit_override stays None (no suppression)."""
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(0)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-789")

        billing_repo.grant_initial_signup_credits.assert_called_once_with(
            user_id="user-789",
            amount=0,
            mode=None,
            monthly_limit_override=None,
        )

    def test_no_double_grant_when_already_granted(self):
        """If repository reports already-granted, service does not retry."""
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(10)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = False  # already granted

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-abc")
            svc.grant_initial_credits("user-abc")  # call twice

        # Repository was called twice but each time it returned False — no extra side effects
        assert billing_repo.grant_initial_signup_credits.call_count == 2

    def test_grant_called_exactly_once_per_user(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(10)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-xyz")

        billing_repo.grant_initial_signup_credits.assert_called_once()


# ── BillingRepository.grant_initial_signup_credits ────────────────────────

class TestBillingRepositoryGrantInitialCredits:
    """Unit tests for the repository method (pure logic, no real DB)."""

    def _make_billing(self, granted: bool, credits: int = 0):
        b = MagicMock()
        b.initial_credits_granted = granted
        b.extra_credits = credits
        return b

    def test_grants_when_not_yet_granted(self):
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        billing = self._make_billing(granted=False, credits=0)
        repo.get_by_user_id = MagicMock(return_value=billing)
        repo._db = MagicMock()
        repo._db.flush = MagicMock()

        result = repo.grant_initial_signup_credits("u1", amount=10, mode=None)

        assert result is True
        assert billing.extra_credits == 10
        assert billing.initial_credits_granted is True
        assert billing.initial_credits_amount == 10
        assert billing.initial_credits_mode is None

    def test_skips_when_already_granted(self):
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        billing = self._make_billing(granted=True, credits=10)
        repo.get_by_user_id = MagicMock(return_value=billing)

        result = repo.grant_initial_signup_credits("u1", amount=10, mode=None)

        assert result is False
        assert billing.extra_credits == 10  # unchanged

    def test_creates_billing_row_for_new_user(self):
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        db.flush = MagicMock()
        repo = BillingRepository(db)
        repo.get_by_user_id = MagicMock(return_value=None)

        created = {}

        def fake_create(**kwargs):
            created.update(kwargs)
            return MagicMock()

        repo.create = MagicMock(side_effect=fake_create)

        result = repo.grant_initial_signup_credits("new-user", amount=3, mode=None)

        assert result is True
        assert created["extra_credits"] == 3
        assert created["initial_credits_granted"] is True
        assert created["initial_credits_amount"] == 3
        assert created["initial_credits_mode"] is None

    def test_monthly_limit_override_stored(self):
        """Grant with monthly_limit_override=0 suppresses the free monthly quota."""
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        repo.get_by_user_id = MagicMock(return_value=None)

        created = {}
        repo.create = MagicMock(side_effect=lambda **kw: created.update(kw) or MagicMock())

        repo.grant_initial_signup_credits("new-user", amount=10, mode=None, monthly_limit_override=0)

        assert created["monthly_limit_override"] == 0
        assert created["extra_credits"] == 10

    def test_monthly_limit_override_applied_to_existing_billing_row(self):
        """Override is applied even when billing row already exists."""
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        billing = self._make_billing(granted=False, credits=0)
        billing.monthly_limit_override = None
        repo.get_by_user_id = MagicMock(return_value=billing)
        repo._db = MagicMock()

        repo.grant_initial_signup_credits("u2", amount=10, mode=None, monthly_limit_override=0)

        assert billing.monthly_limit_override == 0
