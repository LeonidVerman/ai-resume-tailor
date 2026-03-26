"""
backend/app/main.py

FastAPI application factory for the ai-resume-tailor SaaS backend.

Start the server:
    uvicorn backend.app.main:app --reload

This backend is independent of the CLI generator (python -m tailor).
Both coexist in the same repository without interference.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import backend.app.db.models  # noqa: F401 — ensures all ORM models are registered before any mapper is used
from backend.app.api.router import api_router
from backend.app.config import get_settings
from backend.app.constants import API_PREFIX, SERVICE_NAME
from backend.app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(
    level="DEBUG" if settings.is_development else "INFO",
    log_dir=settings.log_dir,
)
logger = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Resume Tailor",
        description="SaaS backend for tailored resume and cover letter generation.",
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],
    )

    app.include_router(api_router, prefix=API_PREFIX)

    @app.on_event("startup")
    async def on_startup() -> None:
        logger.info(
            "Starting %s env=%s version=%s",
            SERVICE_NAME,
            settings.app_env,
            settings.app_version,
        )
        _validate_storage(settings)

    return app


def _validate_storage(s) -> None:
    """Validate storage configuration and prepare the storage backend.

    Raises RuntimeError if required settings are missing.
    """
    import os
    if s.storage_type == "s3":
        missing = [
            name for name, val in (
                ("storage_endpoint", s.storage_endpoint),
                ("storage_access_key_id", s.storage_access_key_id),
                ("storage_secret_access_key", s.storage_secret_access_key),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"S3 storage is configured but the following settings are missing: "
                f"{', '.join(missing)}"
            )
        logger.info("Storage: s3 endpoint=%s bucket=%s", s.storage_endpoint, s.storage_bucket)
    else:
        if not s.storage_local_path:
            raise RuntimeError("storage_local_path must be non-empty when storage_type='local'")
        os.makedirs(s.storage_local_path, exist_ok=True)
        logger.info("Storage: local path=%s", s.storage_local_path)


app = create_app()
