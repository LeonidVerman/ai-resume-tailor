"""
backend/app/api/router.py

Central API router.  All sub-routers are registered here and included
in the FastAPI application via a single API prefix.

Registered routers
------------------
- health            (no prefix)
- metrics           /metrics
- auth              /auth
- candidate_profile /candidate-profile
- resume            /resumes
- job_description   /job-descriptions
- generation        /generations
- documents         /documents
- billing           /billing
- webhooks          /webhooks
- admin             /admin
"""

from fastapi import APIRouter

from backend.app.api import (
    admin,
    auth,
    billing,
    candidate_profile,
    documents,
    generation,
    health,
    job_description,
    legal,
    metrics,
    resume,
    webhooks,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(metrics.router, tags=["metrics"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(candidate_profile.router, prefix="/candidate-profile", tags=["candidate-profile"])
api_router.include_router(resume.router, prefix="/resumes", tags=["resume"])
api_router.include_router(job_description.router, prefix="/job-descriptions", tags=["job-description"])
api_router.include_router(generation.router, prefix="/generations", tags=["generation"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
api_router.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
api_router.include_router(legal.router, prefix="/legal", tags=["legal"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
