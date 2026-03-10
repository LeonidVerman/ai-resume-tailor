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

from backend.app.api.router import api_router
from backend.app.config import get_settings
from backend.app.constants import API_PREFIX, SERVICE_NAME
from backend.app.logging import configure_logging, get_logger

settings = get_settings()
configure_logging(level="DEBUG" if settings.is_development else "INFO")
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

    return app


app = create_app()
