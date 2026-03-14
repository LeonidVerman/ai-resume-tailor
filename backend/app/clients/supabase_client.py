"""
backend/app/clients/supabase_client.py

Supabase client wrapper.

Responsibilities
----------------
- bootstrap the Supabase SDK with application config
- provide a stable access point for auth token verification
- expose a minimal helper for future Supabase-adjacent operations
  (e.g. storage, realtime, edge functions) if needed

Current status
--------------
The application does not yet use Supabase in production flows.
This wrapper is intentionally lightweight — it establishes configuration
patterns and avoids scattering direct Supabase SDK calls later.

Auth integration will be fleshed out in Phase 6.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SupabaseClientWrapper:
    """
    Thin wrapper around the Supabase Python SDK.

    Parameters
    ----------
    url:
        Supabase project URL (``https://<project>.supabase.co``).
    anon_key:
        Public anon key (safe for browser).
    service_role_key:
        Service role key (server-side only; has elevated privileges).
    """

    def __init__(
        self,
        url: str = "",
        anon_key: str = "",
        service_role_key: str = "",
    ) -> None:
        self._url = url
        self._anon_key = anon_key
        self._service_role_key = service_role_key
        self._client: Any = None          # lazy — public client
        self._admin_client: Any = None    # lazy — service-role client

    # ── Internal ───────────────────────────────────────────────────────────

    def _require_config(self) -> None:
        if not self._url or not self._anon_key:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_ANON_KEY must be set in backend/.env "
                "before using the Supabase client."
            )

    def _get_client(self):
        """Return the public (anon) Supabase client."""
        if self._client is None:
            self._require_config()
            from supabase import create_client
            self._client = create_client(self._url, self._anon_key)
        return self._client

    def _get_admin_client(self):
        """Return the service-role Supabase client (elevated privileges)."""
        if self._admin_client is None:
            self._require_config()
            if not self._service_role_key:
                raise RuntimeError(
                    "SUPABASE_SERVICE_ROLE_KEY must be set to use admin operations."
                )
            from supabase import create_client
            self._admin_client = create_client(self._url, self._service_role_key)
        return self._admin_client

    # ── Auth helpers ───────────────────────────────────────────────────────

    def verify_token(self, jwt: str) -> dict:
        """
        Decode and verify a Supabase JWT, returning the user payload.

        Uses the service-role client so verification happens server-side.
        Raises an exception if the token is invalid or expired.

        Note: full JWT verification will be implemented in Phase 6 (auth service).
        """
        client = self._get_admin_client()
        response = client.auth.get_user(jwt)
        if response.user is None:
            raise ValueError("Invalid or expired token")
        return {
            "id": response.user.id,
            "email": response.user.email,
        }

    def sign_up(self, email: str, password: str):
        """Register a new user via Supabase Auth. Returns AuthResponse."""
        return self._get_client().auth.sign_up({"email": email, "password": password})

    def sign_in(self, email: str, password: str):
        """Authenticate an existing user. Returns AuthResponse."""
        return self._get_client().auth.sign_in_with_password(
            {"email": email, "password": password}
        )

    def sign_out(self, access_token: str) -> None:
        """Invalidate the given access token on the Supabase side."""
        try:
            client = self._get_client()
            # supabase-py v2: set session so the SDK knows which token to revoke
            client.auth.set_session(access_token, "")
            client.auth.sign_out()
        except Exception:
            # Best-effort — frontend already cleared the token
            pass

    def get_user_by_id(self, user_id: str) -> dict | None:
        """Look up a Supabase Auth user by UUID (admin only)."""
        client = self._get_admin_client()
        response = client.auth.admin.get_user_by_id(user_id)
        if response.user is None:
            return None
        return {"id": response.user.id, "email": response.user.email}

    # ── Raw client access ──────────────────────────────────────────────────

    @property
    def client(self):
        """Return the raw public Supabase client for direct SDK usage."""
        return self._get_client()

    @property
    def admin_client(self):
        """Return the raw service-role Supabase client for admin SDK usage."""
        return self._get_admin_client()

    # ── Health ────────────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Return True if URL and anon key are set (does not make a network call)."""
        return bool(self._url and self._anon_key)


# ── Factory ────────────────────────────────────────────────────────────────

def make_supabase_client_from_settings() -> SupabaseClientWrapper:
    """Create a SupabaseClientWrapper from application settings."""
    from backend.app.config import get_settings
    s = get_settings()
    return SupabaseClientWrapper(
        url=s.supabase_url,
        anon_key=s.supabase_anon_key,
        service_role_key=s.supabase_service_role_key,
    )
