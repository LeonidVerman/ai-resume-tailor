"""
backend/app/api/generation.py

Generation endpoints.

Endpoints
---------
POST /generations          — run the tailoring pipeline for a job + resume
GET  /generations          — list the user's generation runs
GET  /generations/{id}     — return run detail

Phase 8 status: FUNCTIONAL (synchronous — background worker deferred)
----------------------------------------------------------------------
Generation runs synchronously in the request.  Long generation times
(typically 30–90 s) mean the HTTP timeout needs to be generous.

Async/background execution via Celery or FastAPI BackgroundTasks is a
future enhancement.  The endpoint will return 202 Accepted when that
pattern is adopted.

Usage policy check (free-tier quota) is NOT enforced in this phase
because it depends on a BillingRepository lookup — that is wired in
the billing service and can be added here when billing is live.
"""

import logging

from fastapi import APIRouter, HTTPException, status

from backend.app.clients.storage_client import make_storage_client_from_settings

logger = logging.getLogger(__name__)
from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.generation import (
    GenerationRequest,
    GenerationResponse,
    GenerationRunDetail,
    GenerationRunSummary,
)
from backend.app.services.generation_service import GenerationService
from backend.app.services.storage_service import StorageService
from backend.app.services.usage_policy_service import UsagePolicyService
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.monthly_usage_repository import MonthlyUsageRepository
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository

router = APIRouter()


def _service(db) -> GenerationService:
    storage_svc = StorageService(make_storage_client_from_settings())
    return GenerationService(
        run_repo=GenerationRunRepository(db),
        doc_repo=TailoredDocumentRepository(db),
        jd_repo=JobDescriptionRepository(db),
        resume_repo=StructuredResumeRepository(db),
        profile_repo=CandidateProfileRepository(db),
        storage_service=storage_svc,
    )


def _run_repo(db) -> GenerationRunRepository:
    return GenerationRunRepository(db)


def _to_summary(run) -> GenerationRunSummary:
    doc = run.tailored_documents[0] if run.tailored_documents else None
    return GenerationRunSummary(
        id=run.id,
        status=run.status,
        run_type=run.run_type,
        model_name=run.model_name,
        started_at=run.started_at,
        completed_at=run.completed_at,
        cost_estimate=float(run.cost_estimate) if run.cost_estimate else None,
        tailored_document_id=doc.id if doc else None,
        company_name=doc.company_name if doc else None,
        role_title=doc.role_title if doc else None,
    )


def _to_detail(run) -> GenerationRunDetail:
    return GenerationRunDetail(
        id=run.id,
        user_id=run.user_id,
        job_description_id=run.job_description_id,
        status=run.status,
        run_type=run.run_type,
        model_name=run.model_name,
        prompt_version=run.prompt_version,
        token_input=run.token_input,
        token_output=run.token_output,
        cost_estimate=float(run.cost_estimate) if run.cost_estimate else None,
        error_message=run.error_message,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.post("", response_model=GenerationResponse, status_code=201)
def generate(request: GenerationRequest, user: CurrentUserDep, db: DbDep):
    """
    Run the single-pass tailoring pipeline.

    Accepts a job_description_id and structured_resume_id previously stored
    via the job description and resume upload endpoints.

    Returns immediately with run_id and tailored_document_id on success.
    On pipeline failure, returns 500 with the error message.
    """
    # ── Legal acceptance gate ──────────────────────────────────────────────
    from backend.app.services.legal_service import LegalService
    if not LegalService(db).is_compliant(user.id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You must accept the current Terms of Service and Privacy Notice before generating.",
        )

    # ── Onboarding gate ────────────────────────────────────────────────────
    profile = CandidateProfileRepository(db).get_by_user_id(user.id)
    if profile is None or not profile.onboarding_completed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Complete your candidate profile onboarding before generating.",
        )

    # ── Validate inputs before consuming quota ─────────────────────────────
    jd = JobDescriptionRepository(db).get_by_id(request.job_description_id)
    if jd is None or jd.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")

    resume = StructuredResumeRepository(db).get_by_id(request.structured_resume_id)
    if resume is None or resume.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Structured resume not found")

    # ── Quota gate (check only — consume happens on success inside generate()) ─
    billing = BillingRepository(db).get_by_user_id(user.id)
    UsagePolicyService(
        billing_repo=BillingRepository(db),
        monthly_usage_repo=MonthlyUsageRepository(db),
    ).check_quota(user.id, billing)

    try:
        return _service(db).generate(user.id, request, billing)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Generation failed: {exc}",
        )


@router.get("", response_model=list[GenerationRunSummary])
def list_generations(user: CurrentUserDep, db: DbDep, limit: int = 50, offset: int = 0):
    """List the authenticated user's generation runs, newest first."""
    runs = _run_repo(db).list_by_user_id(user.id, limit=limit, offset=offset)
    return [_to_summary(r) for r in runs]


@router.get("/{run_id}", response_model=GenerationRunDetail)
def get_generation(run_id: int, user: CurrentUserDep, db: DbDep):
    """Return the full detail of a generation run."""
    run = _run_repo(db).get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation run not found")
    if run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _to_detail(run)


@router.delete("/{run_id}", status_code=204)
def delete_generation(run_id: int, user: CurrentUserDep, db: DbDep):
    """Delete a generation run and its associated tailored documents."""
    repo = _run_repo(db)
    run = repo.get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Generation run not found")
    if run.user_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    doc_ids = [d.id for d in run.tailored_documents] if run.tailored_documents else []
    logger.info(
        "History deleted user_id=%s run_id=%s doc_ids=%s",
        user.id, run_id, doc_ids,
    )
    repo.delete(run)
