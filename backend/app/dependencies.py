"""
backend/app/dependencies.py

FastAPI dependency functions for injection into route handlers.

Auth modes (controlled by AUTH_MODE env var)
--------------------------------------------
"dev_bypass"  — trust X-User-Id header (local dev only, never production)
"supabase"    — verify Authorization: Bearer <token> via Supabase JWT

In production only "supabase" should be used.
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
    """Yield a database session. Commits on success, rolls back on exception."""
    from backend.app.db.session import get_db
    yield from get_db(settings.database_url)


DbDep = Annotated[Session, Depends(get_db_session)]


# ── Current user ──────────────────────────────────────────────────────────

def get_current_user(
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
    x_user_id: Annotated[str | None, Header(alias="X-User-Id")] = None,
    db: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
):
    """Resolve the authenticated user from the request.

    In ``supabase`` mode: reads ``Authorization: Bearer <token>``, verifies
    the JWT via Supabase, and creates/returns the local user row.

    In ``dev_bypass`` mode: reads ``X-User-Id`` header (UUID) and looks up
    the user directly — no token verification.
    """
    if settings.auth_mode == "supabase":
        return _get_user_from_bearer(authorization, db, settings)
    else:
        return _get_user_from_header(x_user_id, db)


def _get_user_from_bearer(
    authorization: str | None,
    db: Session,
    settings: Settings,
):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization: Bearer <token> required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1]

    from backend.app.clients.supabase_client import make_supabase_client_from_settings
    from backend.app.db.repositories.user_repository import UserRepository
    from backend.app.services.auth_service import AuthService

    supabase = make_supabase_client_from_settings()
    try:
        payload = supabase.verify_token(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    auth_service = AuthService(UserRepository(db))
    return auth_service.get_or_create_user(
        email=payload["email"],
        supabase_user_id=payload["id"],
    )


def _get_user_from_header(x_user_id: str | None, db: Session):
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
