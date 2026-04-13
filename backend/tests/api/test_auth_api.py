"""
backend/tests/api/test_auth_api.py

Tests for the auth endpoints, focused on the signup credit grant path.

All external dependencies (Supabase, DB, settings, SignupCreditService) are
mocked so these tests run without any real infrastructure.
"""

import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest


API = "/api/v1/auth"

# Patch targets. Lazy imports in the endpoint functions must be patched
# at the module where they are *defined*.
_SUPA   = "backend.app.clients.supabase_client.make_supabase_client_from_settings"
_SVC    = "backend.app.services.signup_credit_service.SignupCreditService"
_SETTINGS = "backend.app.config.get_settings"
_AUTH_SVC = "backend.app.services.auth_service.AuthService"


def _settings_stub() -> MagicMock:
    s = MagicMock()
    s.auth_mode = "supabase"
    return s


def _supa_response(email: str, supa_uid: str | None = None) -> MagicMock:
    user = MagicMock()
    user.id = supa_uid or str(uuid.uuid4())
    user.email = email
    resp = MagicMock()
    resp.user = user
    resp.session = MagicMock()
    resp.session.access_token = "tok"
    resp.session.refresh_token = "ref"
    resp.session.expires_in = 3600
    return resp


def _mock_user(email: str) -> MagicMock:
    u = MagicMock()
    u.id = str(uuid.uuid4())
    u.email = email
    u.role = "user"
    u.plan_type = "free"
    return u


@contextmanager
def _patch_login(supa_resp, is_new: bool, grant_mock):
    """Patch the full dependency chain for the /login endpoint."""
    local_user = _mock_user(supa_resp.user.email)

    with patch(_SETTINGS, return_value=_settings_stub()), \
         patch(_SUPA) as mock_factory, \
         patch("backend.app.services.auth_service.AuthService.get_or_create_user",
               return_value=(local_user, is_new)), \
         patch("backend.app.services.auth_service.AuthService.record_login"), \
         patch(_SVC) as MockSvc, \
         patch("backend.app.db.session.get_session_factory") as mock_sf:

        # Give the DB dependency a valid mock session
        mock_session = MagicMock()
        mock_sf.return_value.return_value = mock_session

        mock_supabase = MagicMock()
        mock_supabase.sign_in.return_value = supa_resp
        mock_factory.return_value = mock_supabase

        MockSvc.return_value.grant_initial_credits = grant_mock
        yield


@contextmanager
def _patch_refresh(supa_resp, is_new: bool, grant_mock):
    """Patch the full dependency chain for the /refresh endpoint."""
    local_user = _mock_user(supa_resp.user.email)

    with patch(_SETTINGS, return_value=_settings_stub()), \
         patch(_SUPA) as mock_factory, \
         patch("backend.app.services.auth_service.AuthService.get_or_create_user",
               return_value=(local_user, is_new)), \
         patch(_SVC) as MockSvc, \
         patch("backend.app.db.session.get_session_factory") as mock_sf:

        mock_session = MagicMock()
        mock_sf.return_value.return_value = mock_session

        mock_supabase = MagicMock()
        mock_supabase.refresh_session.return_value = supa_resp
        mock_factory.return_value = mock_supabase

        MockSvc.return_value.grant_initial_credits = grant_mock
        yield


# ── Login ───────────────────────────────────────────────────────────���──────

class TestLoginGrantsCreditsOnFirstLogin:
    """
    When is_new=True (user's first backend login — e.g. after email
    confirmation), the login endpoint must call grant_initial_credits.
    """

    def test_grants_credits_when_user_is_new(self, anon_client):
        email = f"new-{uuid.uuid4()}@example.com"
        supa_resp = _supa_response(email)
        mock_grant = MagicMock()

        with _patch_login(supa_resp, is_new=True, grant_mock=mock_grant):
            resp = anon_client.post(
                f"{API}/login",
                json={"email": email, "password": "password123"},
            )

        assert resp.status_code == 200
        mock_grant.assert_called_once()

    def test_does_not_grant_credits_for_returning_user(self, anon_client):
        email = f"returning-{uuid.uuid4()}@example.com"
        supa_resp = _supa_response(email)
        mock_grant = MagicMock()

        with _patch_login(supa_resp, is_new=False, grant_mock=mock_grant):
            resp = anon_client.post(
                f"{API}/login",
                json={"email": email, "password": "password123"},
            )

        assert resp.status_code == 200
        mock_grant.assert_not_called()


# ── Refresh ────────────────────────────────────────────────────────────────

class TestRefreshGrantsCreditsOnFirstRefresh:
    """
    Edge case: a user calls /refresh before /login (token recovered from
    storage after email confirmation).  Credits must be granted on is_new=True.
    """

    def test_grants_credits_when_user_is_new(self, anon_client):
        email = f"refresh-new-{uuid.uuid4()}@example.com"
        supa_resp = _supa_response(email)
        mock_grant = MagicMock()

        with _patch_refresh(supa_resp, is_new=True, grant_mock=mock_grant):
            resp = anon_client.post(
                f"{API}/refresh",
                json={"refresh_token": "old-tok"},
            )

        assert resp.status_code == 200
        mock_grant.assert_called_once()

    def test_does_not_grant_credits_for_returning_user(self, anon_client):
        email = f"refresh-returning-{uuid.uuid4()}@example.com"
        supa_resp = _supa_response(email)
        mock_grant = MagicMock()

        with _patch_refresh(supa_resp, is_new=False, grant_mock=mock_grant):
            resp = anon_client.post(
                f"{API}/refresh",
                json={"refresh_token": "old-tok"},
            )

        assert resp.status_code == 200
        mock_grant.assert_not_called()
