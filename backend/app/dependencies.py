"""
backend/app/dependencies.py

FastAPI dependency functions for injection into route handlers.

Currently provides:
- get_settings: returns the application Settings object

Future dependencies (placeholders, not yet implemented):
- get_db: async database session
- get_current_user: authenticated user from JWT
- require_admin: admin-only guard
"""

from typing import Annotated

from fastapi import Depends

from backend.app.config import Settings, get_settings

# ── Settings ──────────────────────────────────────────────────────────────

SettingsDep = Annotated[Settings, Depends(get_settings)]


# ── Future: Database session ───────────────────────────────────────────────
# async def get_db() -> AsyncGenerator[AsyncSession, None]:
#     async with async_session_factory() as session:
#         yield session
#
# DbDep = Annotated[AsyncSession, Depends(get_db)]


# ── Future: Auth ───────────────────────────────────────────────────────────
# async def get_current_user(
#     token: str = Depends(oauth2_scheme),
#     db: DbDep = ...,
# ) -> User:
#     ...
#
# CurrentUserDep = Annotated[User, Depends(get_current_user)]
#
# async def require_admin(user: CurrentUserDep) -> User:
#     if user.role != ROLE_ADMIN:
#         raise HTTPException(status_code=403, detail="Admin required")
#     return user
