"""
backend/app/api/auth.py

Auth endpoints — register, login, logout, me, status.

Authentication provider: Supabase Auth
Authorization source of truth: local DB user row (role field)
"""

from fastapi import APIRouter, Header, HTTPException, status
from typing import Annotated

from backend.app.constants import ROLE_ADMIN
from backend.app.dependencies import CurrentUserDep, DbDep, SettingsDep
from backend.app.schemas.auth import (
    AuthLoginResponse,
    AuthMeResponse,
    AuthSessionResponse,
    AuthUserResponse,
    LoginRequest,
    RegisterRequest,
)

router = APIRouter()


def _user_response(user) -> AuthUserResponse:
    return AuthUserResponse(
        id=user.id,
        email=user.email,
        role=user.role,
        is_admin=(user.role == ROLE_ADMIN),
        plan_type=user.plan_type,
    )


def _session_response(session) -> AuthSessionResponse:
    return AuthSessionResponse(
        access_token=session.access_token,
        refresh_token=session.refresh_token,
        expires_in=session.expires_in,
    )


# ── Register ──────────────────────────────────────────────────────────────

@router.post("/register", response_model=AuthLoginResponse, status_code=201)
def register(request: RegisterRequest, db: DbDep, settings: SettingsDep):
    """Create a new account via Supabase and return session + user info."""
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Registration is only available when AUTH_MODE=supabase",
        )

    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    from backend.app.db.repositories.user_repository import UserRepository
    from backend.app.services.auth_service import AuthService

    supabase = make_supabase_client_from_settings()
    try:
        response = supabase.sign_up(request.email, request.password)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Registration failed: {exc}",
        )

    if response.user is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Registration failed: no user returned from Supabase",
        )
    if response.session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Email confirmation is required. "
                "Please verify your email address and then log in."
            ),
        )

    auth_service = AuthService(UserRepository(db))
    user = auth_service.get_or_create_user(
        email=response.user.email,
        supabase_user_id=response.user.id,
    )
    auth_service.record_login(user)
    db.commit()

    return AuthLoginResponse(
        user=_user_response(user),
        session=_session_response(response.session),
    )


# ── Login ─────────────────────────────────────────────────────────────────

@router.post("/login", response_model=AuthLoginResponse)
def login(request: LoginRequest, db: DbDep, settings: SettingsDep):
    """Authenticate with email/password and return session + user info."""
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Email/password login is only available when AUTH_MODE=supabase",
        )

    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    from backend.app.db.repositories.user_repository import UserRepository
    from backend.app.services.auth_service import AuthService

    supabase = make_supabase_client_from_settings()
    try:
        response = supabase.sign_in(request.email, request.password)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid email or password",
        )

    if response.user is None or response.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    auth_service = AuthService(UserRepository(db))
    user = auth_service.get_or_create_user(
        email=response.user.email,
        supabase_user_id=response.user.id,
    )
    auth_service.record_login(user)
    db.commit()

    return AuthLoginResponse(
        user=_user_response(user),
        session=_session_response(response.session),
    )


# ── Logout ────────────────────────────────────────────────────────────────

@router.post("/logout", status_code=204)
def logout(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    settings: SettingsDep = None,
):
    """Invalidate the current session. Frontend must clear stored tokens."""
    if settings.auth_mode == "supabase" and authorization and authorization.startswith("Bearer "):
        token = authorization.split(" ", 1)[1]
        from backend.app.clients.supabase_client import make_supabase_client_from_settings
        make_supabase_client_from_settings().sign_out(token)
    # Always return 204 — frontend clears tokens regardless


# ── Me ────────────────────────────────────────────────────────────────────

@router.get("/me", response_model=AuthMeResponse)
def auth_me(user: CurrentUserDep):
    """Return basic identity info for the authenticated user."""
    return AuthMeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        is_admin=(user.role == ROLE_ADMIN),
        plan_type=user.plan_type,
    )


# ── Status ────────────────────────────────────────────────────────────────

@router.get("/status")
def auth_status(settings: SettingsDep):
    """Return auth configuration mode. Useful for frontend bootstrapping."""
    return {"auth_mode": settings.auth_mode, "status": "ok"}
