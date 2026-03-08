"""
backend/app/api/health.py

Health check endpoints.  Used by infrastructure to confirm the service is up.

Endpoints:
  GET /health       — liveness probe (always returns 200 if the process is up)
  GET /health/db    — readiness probe (verifies DB connectivity)
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from backend.app.constants import SERVICE_NAME
from backend.app.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class DbHealthResponse(BaseModel):
    status: str
    database: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — returns 200 if the process is running."""
    from backend.app.config import get_settings

    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=settings.app_version,
    )


@router.get("/health/db", response_model=DbHealthResponse)
async def health_db() -> DbHealthResponse:
    """Readiness probe — verifies database connectivity."""
    from backend.app.config import get_settings
    from backend.app.db.session import get_engine

    settings = get_settings()

    if not settings.database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DATABASE_URL is not configured",
        )

    try:
        from sqlalchemy import text

        engine = get_engine(settings.database_url)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return DbHealthResponse(status="ok", database="connected")
    except Exception as exc:
        logger.error("DB health check failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database unavailable: {exc}",
        )
