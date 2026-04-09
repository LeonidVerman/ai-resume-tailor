"""
backend/app/services/auth_service.py

Auth service — user resolution, identity helpers, role checks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

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

    def get_or_create_user(self, email: str, supabase_user_id: str) -> tuple[User, bool]:
        """Return (user, is_new) for the given Supabase identity.

        is_new is True only when a brand-new local user row was created.
        Callers that don't need is_new can unpack with: user, _ = ...

        Lookup order:
        1. By supabase_user_id (fast path for returning users)
        2. By email (migration path: existing dev-bypass users get their
           supabase_user_id backfilled on first real login)
        3. Create new user (brand-new registration)
        """
        # Fast path: already linked to this Supabase identity
        user = self._users.get_by_supabase_id(supabase_user_id)
        if user is not None:
            return user, False

        # Migration path: pre-existing dev-bypass user — link Supabase identity
        user = self._users.get_by_email(email)
        if user is not None:
            logger.info(
                "Linking supabase_user_id=%s to existing user id=%s email=%s",
                supabase_user_id, user.id, email,
            )
            self._users.update(user, supabase_user_id=supabase_user_id)
            return user, False

        # First login: create local user row, using Supabase UUID as primary key
        logger.info("Creating new user email=%s supabase_user_id=%s", email, supabase_user_id)
        user = self._users.create(
            id=supabase_user_id,
            supabase_user_id=supabase_user_id,
            email=email,
        )
        return user, True

    def record_login(self, user: User) -> None:
        """Update last_login_at and updated_at timestamps."""
        now = datetime.now(timezone.utc)
        self._users.update(user, last_login_at=now, updated_at=now)

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
