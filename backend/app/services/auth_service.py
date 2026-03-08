"""
backend/app/services/auth_service.py

Auth service — user resolution, identity helpers, role checks.

Full Supabase JWT verification is wired in Phase 8 when auth endpoints
are built.  For now this service provides the helpers future route
handlers will call.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from backend.app.constants import ROLE_ADMIN
from backend.app.db.repositories.user_repository import UserRepository
from backend.app.db.models.user import User

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, user_repo: UserRepository) -> None:
        self._users = user_repo

    # ── User resolution ────────────────────────────────────────────────────

    def get_user_by_id(self, user_id: str) -> User:
        """Return the User record or raise 404."""
        user = self._users.get_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found",
            )
        return user

    def get_user_by_email(self, email: str) -> User | None:
        return self._users.get_by_email(email)

    def get_or_create_user(self, email: str, supabase_user_id: str) -> User:
        """
        Return an existing user or create one on first login.

        Called during JWT verification flow (Phase 8).
        ``supabase_user_id`` is used as the primary key so it matches
        the Supabase auth UUID.
        """
        user = self._users.get_by_id(supabase_user_id)
        if user is not None:
            return user
        user = self._users.get_by_email(email)
        if user is not None:
            return user
        logger.info("Creating new user for email=%s id=%s", email, supabase_user_id)
        return self._users.create(id=supabase_user_id, email=email)

    # ── Role / permission checks ───────────────────────────────────────────

    @staticmethod
    def require_admin(user: User) -> None:
        """Raise 403 if the user is not an admin."""
        if user.role != ROLE_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Admin access required",
            )

    @staticmethod
    def is_admin(user: User) -> bool:
        return user.role == ROLE_ADMIN
