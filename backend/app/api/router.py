"""
backend/app/api/router.py

Central API router.  All sub-routers are registered here and included
in the FastAPI application via a single API prefix.

Currently registered:
- health

Future routers (not yet implemented):
- auth
- candidate_profile
- resume
- job_description
- generation
- documents
- billing
- webhooks
- admin
"""

from fastapi import APIRouter

from backend.app.api import health

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])

# ── Future routers ─────────────────────────────────────────────────────────
# from backend.app.api import auth, candidate_profile, resume, generation, billing, admin
# api_router.include_router(auth.router,               prefix="/auth",              tags=["auth"])
# api_router.include_router(candidate_profile.router,  prefix="/candidate-profile", tags=["candidate-profile"])
# api_router.include_router(resume.router,             prefix="/resumes",           tags=["resume"])
# api_router.include_router(generation.router,         prefix="/generations",       tags=["generation"])
# api_router.include_router(billing.router,            prefix="/billing",           tags=["billing"])
# api_router.include_router(admin.router,              prefix="/admin",             tags=["admin"])
