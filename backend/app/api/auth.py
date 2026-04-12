"""
backend/app/api/auth.py

Auth endpoints — register, login, logout, me, status.

Authentication provider: Supabase Auth
Authorization source of truth: local DB user row (role field)
"""

import logging

from fastapi import APIRouter, Header, HTTPException, status
from typing import Annotated

from backend.app.constants import ROLE_ADMIN
from backend.app.dependencies import CurrentUserDep, DbDep, SettingsDep

logger = logging.getLogger(__name__)
from backend.app.schemas.auth import (
    AuthLoginResponse,
    AuthMeResponse,
    AuthSessionResponse,
    AuthUserResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
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

    from backend.app.services.signup_credit_service import SignupCreditService

    auth_service = AuthService(UserRepository(db))
    user, is_new = auth_service.get_or_create_user(
        email=response.user.email,
        supabase_user_id=response.user.id,
    )
    auth_service.record_login(user)

    if is_new:
        SignupCreditService(db).grant_initial_credits(user.id)

    db.commit()

    logger.info("User registered user_id=%s email=%s is_new=%s", user.id, user.email, is_new)

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

    from backend.app.services.signup_credit_service import SignupCreditService

    auth_service = AuthService(UserRepository(db))
    user, is_new = auth_service.get_or_create_user(
        email=response.user.email,
        supabase_user_id=response.user.id,
    )
    auth_service.record_login(user)
    if is_new:
        SignupCreditService(db).grant_initial_credits(user.id)
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
def auth_me(user: CurrentUserDep, db: DbDep):
    """Return basic identity info for the authenticated user."""
    from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
    from backend.app.services.legal_service import LegalService

    profile = CandidateProfileRepository(db).get_by_user_id(user.id)
    onboarding_completed = profile.onboarding_completed if profile is not None else False

    try:
        legal_accepted = LegalService(db).is_compliant(user.id)
    except Exception:
        legal_accepted = False

    from backend.app.db.repositories.billing_repository import BillingRepository
    from backend.app.constants import PLAN_FREE
    billing = BillingRepository(db).get_by_user_id(user.id)
    plan_type = billing.plan_type if billing is not None else PLAN_FREE

    return AuthMeResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        is_admin=(user.role == ROLE_ADMIN),
        plan_type=plan_type,
        onboarding_completed=onboarding_completed,
        legal_accepted=legal_accepted,
    )


# ── Forgot password ───────────────────────────────────────────────────────

@router.post("/forgot-password", status_code=204)
def forgot_password(request: ForgotPasswordRequest, settings: SettingsDep):
    """Send a password-reset email.

    Always returns 204 regardless of whether the email exists, to prevent
    user enumeration.  Only available when AUTH_MODE=supabase.
    """
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Password reset is only available when AUTH_MODE=supabase",
        )
    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    # Use the redirect URL supplied by the frontend (window.location.origin +
    # '/reset-password') so the link works on any environment without hardcoding
    # the domain.  Supabase validates redirect_to against its allowed-URL list.
    redirect_url = request.redirect_to or f"{settings.app_base_url}/reset-password"
    try:
        make_supabase_client_from_settings().reset_password_request(
            request.email, redirect_url
        )
    except Exception:
        pass  # Always return 204 — don't reveal whether the email exists
    logger.info("Password reset requested email=%s", request.email)


# ── Reset password ────────────────────────────────────────────────────────

@router.post("/reset-password", status_code=204)
def reset_password(request: ResetPasswordRequest, settings: SettingsDep):
    """Update the user's password using their Supabase recovery token.

    The recovery token is the ``access_token`` from the URL fragment that
    Supabase appended to the reset-password redirect URL.
    """
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Password reset is only available when AUTH_MODE=supabase",
        )
    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    try:
        make_supabase_client_from_settings().update_password(
            request.access_token, request.new_password
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "Invalid or expired recovery token",
        )
    logger.info("Password reset completed (token accepted)")


# ── Refresh ───────────────────────────────────────────────────────────────

@router.post("/refresh", response_model=AuthLoginResponse)
def refresh(request: RefreshRequest, db: DbDep, settings: SettingsDep):
    """Exchange a refresh token for a new session."""
    if settings.auth_mode != "supabase":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Token refresh is only available when AUTH_MODE=supabase",
        )

    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    from backend.app.db.repositories.user_repository import UserRepository
    from backend.app.services.auth_service import AuthService

    supabase = make_supabase_client_from_settings()
    try:
        response = supabase.refresh_session(request.refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    if response.user is None or response.session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    from backend.app.services.signup_credit_service import SignupCreditService

    auth_service = AuthService(UserRepository(db))
    user, is_new = auth_service.get_or_create_user(
        email=response.user.email,
        supabase_user_id=response.user.id,
    )
    if is_new:
        SignupCreditService(db).grant_initial_credits(user.id)
    db.commit()

    return AuthLoginResponse(
        user=_user_response(user),
        session=_session_response(response.session),
    )


# ── Status ────────────────────────────────────────────────────────────────

@router.get("/status")
def auth_status(settings: SettingsDep):
    """Return auth configuration mode. Useful for frontend bootstrapping."""
    return {"auth_mode": settings.auth_mode, "status": "ok"}
