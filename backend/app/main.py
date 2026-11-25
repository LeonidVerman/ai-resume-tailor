"""
backend/app/main.py

FastAPI application factory for the ai-resume-tailor SaaS backend.

Start the server:
    uvicorn backend.app.main:app --reload

This backend is independent of the CLI generator (python -m tailor).
Both coexist in the same repository without interference.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import backend.app.db.models  # noqa: F401 — ensures all ORM models are registered before any mapper is used
from backend.app.api.router import api_router
from backend.app.config import get_settings
from backend.app.constants import API_PREFIX, SERVICE_NAME
from backend.app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(
    level="INFO",
    log_dir=settings.log_dir,
)
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "Starting %s env=%s version=%s",
        SERVICE_NAME,
        settings.app_env,
        settings.app_version,
    )
    _validate_storage(settings)
    scheduler = _start_guest_cleanup_scheduler(settings)
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        logger.info("Guest cleanup scheduler stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Resume Tailor",
        description="SaaS backend for tailored resume and cover letter generation.",
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
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

    return app


def _start_guest_cleanup_scheduler(s):
    """Start the APScheduler guest cleanup jobs (issue #155, Phase 5).

    In-process scheduling is a documented decision for the current
    single-instance deployment; if the backend later scales out, move to an
    external scheduler without touching the rest of the guest architecture.

    Not started under pytest / test environments or when DATABASE_URL is
    unset.  Set DISABLE_GUEST_SCHEDULER=1 to opt out explicitly.
    """
    import os
    import sys

    if (
        s.app_env == "test"
        or "pytest" in sys.modules
        or os.environ.get("DISABLE_GUEST_SCHEDULER") == "1"
    ):
        logger.info("Guest cleanup scheduler disabled (test env or opt-out)")
        return None
    if not s.database_url:
        logger.warning(
            "Guest cleanup scheduler not started: DATABASE_URL is not configured"
        )
        return None

    from apscheduler.schedulers.background import BackgroundScheduler

    from backend.app.services.guest_cleanup_service import (
        run_retention_purge,
        run_stale_reservation_release,
    )

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_stale_reservation_release,
        "interval",
        minutes=15,
        args=[s.database_url, s],
        id="guest_stale_reservation_release",
    )
    scheduler.add_job(
        run_retention_purge,
        "interval",
        hours=24,
        args=[s.database_url, s],
        id="guest_retention_purge",
    )
    scheduler.start()
    logger.info(
        "Guest cleanup scheduler started (stale-reservation release every "
        "15 min, retention purge daily)"
    )
    return scheduler


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
