"""
backend/app/schemas/user.py

User summary and response schemas.
"""

from datetime import datetime

from backend.app.schemas.common import APIModel


class UserSummary(APIModel):
    """Lightweight user representation returned in most API responses."""
    id: str
    email: str
    plan_type: str
    role: str
    free_generations_used: int
    created_at: datetime


class UserUpdateRequest(APIModel):
    """Fields a user can update on their own account (limited set)."""
    email: str | None = None
