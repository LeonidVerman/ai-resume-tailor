"""
backend/app/api/auth.py

Auth endpoints.

Phase 8 status: PARTIALLY SCAFFOLDED
-------------------------------------
Full Supabase JWT authentication is deferred.  These endpoints provide:
  - GET /auth/me       — return the resolved user from the dev-mode bypass
  - GET /auth/status   — quick reachability / config check

When Supabase is wired (Phase 9+):
  - POST /auth/login and POST /auth/register will delegate to Supabase
  - get_current_user dependency will verify JWTs instead of reading headers
"""

from fastapi import APIRouter

from backend.app.dependencies import CurrentUserDep
from backend.app.schemas.auth import AuthMeResponse

router = APIRouter()


@router.get("/me", response_model=AuthMeResponse)
def auth_me(user: CurrentUserDep):
    """Return basic identity info for the authenticated user."""
    return AuthMeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        plan_type=user.plan_type,
    )


@router.get("/status")
def auth_status():
    """
    Return auth configuration status.

    In dev mode this always returns the bypass indicator.
    In production this will reflect Supabase connectivity.
    """
    return {"auth_mode": "dev_bypass", "status": "ok"}
