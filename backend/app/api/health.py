"""
backend/app/api/health.py

Health check endpoint.  Used by infrastructure to confirm the service is up.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from backend.app.constants import SERVICE_NAME

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service liveness status."""
    from backend.app.config import get_settings

    settings = get_settings()
    return HealthResponse(
        status="ok",
        service=SERVICE_NAME,
        version=settings.app_version,
    )
