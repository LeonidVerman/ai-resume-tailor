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


class TokenResponse(APIModel):
    """Returned after successful login or register."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class AuthMeResponse(APIModel):
    """Returned by GET /auth/me."""
    user_id: str
    email: str
    role: str
    plan_type: str
