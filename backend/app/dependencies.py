"""
backend/app/dependencies.py

FastAPI dependency functions for injection into route handlers.

Auth strategy (Phase 8)
-----------------------
Full Supabase JWT verification is deferred to Phase 9+ when the frontend
auth flow is complete.  For now, route handlers that need a user identity
accept an ``X-User-Id`` header (UUID string).  This is a DEV-MODE bypass
only — do not expose to production without replacing with real JWT auth.

The ``get_current_user`` dependency currently:
  1. Reads ``X-User-Id`` header
  2. Looks up the User record in the DB
  3. Returns the User or raises 401/404

When Supabase JWT is wired, replace step 1 with JWT verification via
SupabaseClientWrapper.verify_token() and keep steps 2–3 unchanged.
"""

from collections.abc import Generator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from backend.app.config import Settings, get_settings
from backend.app.constants import ROLE_ADMIN

# ── Settings ──────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── Database session ───────────────────────────────────────────────────────

def get_db_session(settings: Settings = Depends(get_settings)) -> Generator[Session, None, None]:
    """
    Yield a database session for the duration of a request.

    Commits on success, rolls back on exception.
    Raises RuntimeError (→ 500) if DATABASE_URL is not configured.
    """
    from backend.app.db.session import get_db
    yield from get_db(settings.database_url)


DbDep = Annotated[Session, Depends(get_db_session)]


# ── Current user (dev-mode bypass) ────────────────────────────────────────

def get_current_user(
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    db: Session = Depends(get_db_session),
):
    """
    Resolve the requesting user from the ``X-User-Id`` header.

    DEV-MODE ONLY: This is a placeholder until Supabase JWT is wired.
    Replace with real JWT verification before any production deployment.

    Raises:
        401 — header missing
        404 — user not found
    """
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-User-Id header required (dev-mode auth bypass)",
        )
    from backend.app.db.repositories.user_repository import UserRepository
    user = UserRepository(db).get_by_id(x_user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {x_user_id!r} not found",
        )
    return user


CurrentUserDep = Annotated[object, Depends(get_current_user)]


# ── Admin guard ────────────────────────────────────────────────────────────

def require_admin(user=Depends(get_current_user)):
    """Raise 403 if the user is not an admin."""
    if user.role != ROLE_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


AdminDep = Annotated[object, Depends(require_admin)]
