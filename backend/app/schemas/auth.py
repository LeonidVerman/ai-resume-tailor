"""
backend/app/schemas/auth.py

Auth request/response schemas.
Auth identity is delegated to Supabase; these models cover the
app-level session handshake and token exchange used by the API.
"""

from pydantic import EmailStr, Field

from backend.app.schemas.common import APIModel


class LoginRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8)


class RegisterRequest(APIModel):
    email: EmailStr
    password: str = Field(min_length=8)


class AuthSessionResponse(APIModel):
    """Token payload returned after login or register."""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class AuthUserResponse(APIModel):
    """User identity returned alongside a session."""
    id: str
    email: str
    role: str
    is_admin: bool
    plan_type: str


class AuthLoginResponse(APIModel):
    """Returned by POST /auth/login and POST /auth/register."""
    authenticated: bool = True
    user: AuthUserResponse
    session: AuthSessionResponse


class AuthMeResponse(APIModel):
    """Returned by GET /auth/me."""
    user_id: str
    email: str
    role: str
    is_admin: bool
    plan_type: str
    onboarding_completed: bool
    legal_accepted: bool  # True if user has accepted all currently required legal docs
    is_anonymous: bool = False  # True for guest (/try) identities (issue #155)


class ForgotPasswordRequest(APIModel):
    """Body for POST /auth/forgot-password."""
    email: EmailStr
    redirect_to: str | None = None  # frontend passes window.location.origin + '/reset-password'


class ResetPasswordRequest(APIModel):
    """Body for POST /auth/reset-password."""
    access_token: str
    new_password: str = Field(min_length=8)


class RefreshRequest(APIModel):
    """Body for POST /auth/refresh."""
    refresh_token: str
