"""
backend/tests/unit/test_signup_credit_service.py

Unit tests for SignupCreditService.
All DB interactions are mocked — no DB required.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from backend.app.constants import (
    SIGNUP_CREDIT_AMOUNTS,
    SIGNUP_CREDIT_MODE_BETA,
    SIGNUP_CREDIT_MODE_NORMAL,
)
from backend.app.services.signup_credit_service import SignupCreditService


# ── Helpers ────────────────────────────────────────────────────────────────

def _make_admin_config(mode: str):
    cfg = MagicMock()
    cfg.signup_credit_mode = mode
    return cfg


def _make_service(mode: str = SIGNUP_CREDIT_MODE_NORMAL) -> tuple[SignupCreditService, MagicMock, MagicMock]:
    """Return (service, mock_admin_config_repo, mock_billing_repo)."""
    db = MagicMock()

    admin_cfg = _make_admin_config(mode)
    mock_admin_repo = MagicMock()
    mock_admin_repo.get.return_value = admin_cfg

    mock_billing_repo = MagicMock()
    mock_billing_repo.grant_initial_signup_credits.return_value = True

    svc = SignupCreditService(db)

    with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_admin_repo), \
         patch("backend.app.services.signup_credit_service.BillingRepository", return_value=mock_billing_repo):
        yield svc, mock_admin_repo, mock_billing_repo


# ── resolve_credit_amount ──────────────────────────────────────────────────

class TestResolveCreditAmount:
    def test_normal_mode_resolves_3(self):
        assert SignupCreditService.resolve_credit_amount(SIGNUP_CREDIT_MODE_NORMAL) == 3

    def test_beta_mode_resolves_10(self):
        assert SignupCreditService.resolve_credit_amount(SIGNUP_CREDIT_MODE_BETA) == 10

    def test_unknown_mode_falls_back_to_normal(self):
        assert SignupCreditService.resolve_credit_amount("unknown") == 3

    def test_amounts_match_constants(self):
        for mode, expected in SIGNUP_CREDIT_AMOUNTS.items():
            assert SignupCreditService.resolve_credit_amount(mode) == expected


# ── get_current_policy ─────────────────────────────────────────────────────

class TestGetCurrentPolicy:
    def test_returns_normal_mode_and_amount(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        mock_repo = MagicMock()
        mock_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_NORMAL)

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            mode, amount = svc.get_current_policy()

        assert mode == SIGNUP_CREDIT_MODE_NORMAL
        assert amount == 3

    def test_returns_beta_mode_and_amount(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        mock_repo = MagicMock()
        mock_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_BETA)

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            mode, amount = svc.get_current_policy()

        assert mode == SIGNUP_CREDIT_MODE_BETA
        assert amount == 10

    def test_none_mode_falls_back_to_normal(self):
        db = MagicMock()
        svc = SignupCreditService(db)
        cfg = MagicMock()
        cfg.signup_credit_mode = None
        mock_repo = MagicMock()
        mock_repo.get.return_value = cfg

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=mock_repo):
            mode, amount = svc.get_current_policy()

        assert mode == SIGNUP_CREDIT_MODE_NORMAL
        assert amount == 3


# ── grant_initial_credits ──────────────────────────────────────────────────

class TestGrantInitialCredits:
    def test_grants_credits_in_normal_mode(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_NORMAL)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-123")

        billing_repo.grant_initial_signup_credits.assert_called_once_with(
            user_id="user-123",
            amount=3,
            mode=SIGNUP_CREDIT_MODE_NORMAL,
            monthly_limit_override=None,  # normal mode does not override monthly quota
        )

    def test_grants_credits_in_beta_mode(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_BETA)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-456")

        billing_repo.grant_initial_signup_credits.assert_called_once_with(
            user_id="user-456",
            amount=10,
            mode=SIGNUP_CREDIT_MODE_BETA,
            monthly_limit_override=0,  # beta mode suppresses the 3/month free quota
        )

    def test_no_double_grant_when_already_granted(self):
        """If repository reports already-granted, service does not retry."""
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_NORMAL)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = False  # already granted

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-789")
            svc.grant_initial_credits("user-789")  # call twice

        # Repository was called twice but each time it returned False — no extra side effects
        assert billing_repo.grant_initial_signup_credits.call_count == 2

    def test_grant_called_exactly_once_per_user(self):
        db = MagicMock()
        svc = SignupCreditService(db)

        admin_repo = MagicMock()
        admin_repo.get.return_value = _make_admin_config(SIGNUP_CREDIT_MODE_BETA)
        billing_repo = MagicMock()
        billing_repo.grant_initial_signup_credits.return_value = True

        with patch("backend.app.services.signup_credit_service.AdminConfigRepository", return_value=admin_repo), \
             patch("backend.app.services.signup_credit_service.BillingRepository", return_value=billing_repo):
            svc.grant_initial_credits("user-abc")

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

        result = repo.grant_initial_signup_credits("u1", amount=10, mode="beta")

        assert result is True
        assert billing.extra_credits == 10
        assert billing.initial_credits_granted is True
        assert billing.initial_credits_amount == 10
        assert billing.initial_credits_mode == "beta"

    def test_skips_when_already_granted(self):
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        billing = self._make_billing(granted=True, credits=10)
        repo.get_by_user_id = MagicMock(return_value=billing)

        result = repo.grant_initial_signup_credits("u1", amount=10, mode="beta")

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

        result = repo.grant_initial_signup_credits("new-user", amount=3, mode="normal")

        assert result is True
        assert created["extra_credits"] == 3
        assert created["initial_credits_granted"] is True
        assert created["initial_credits_amount"] == 3
        assert created["initial_credits_mode"] == "normal"

    def test_monthly_limit_override_stored_for_beta(self):
        """Beta grant passes monthly_limit_override=0 to suppress the free monthly quota."""
        from backend.app.db.repositories.billing_repository import BillingRepository

        db = MagicMock()
        repo = BillingRepository(db)
        repo.get_by_user_id = MagicMock(return_value=None)

        created = {}
        repo.create = MagicMock(side_effect=lambda **kw: created.update(kw) or MagicMock())

        repo.grant_initial_signup_credits("new-beta-user", amount=10, mode="beta", monthly_limit_override=0)

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

        repo.grant_initial_signup_credits("u2", amount=10, mode="beta", monthly_limit_override=0)

        assert billing.monthly_limit_override == 0
