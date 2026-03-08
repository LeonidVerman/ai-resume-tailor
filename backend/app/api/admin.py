"""
backend/app/api/admin.py

Admin endpoints.

Endpoints
---------
POST /admin/evaluate-run    — score a completed generation run
GET  /admin/system-stats    — aggregate counts for all users

Phase 8 status: FUNCTIONAL (admin-only via require_admin dependency)
---------------------------------------------------------------------
Both endpoints require the authenticated user to have role='admin'.
POST /admin/evaluate-run delegates to EvaluationService which wraps
the existing generator's assess pipeline (falls back to null scores
if tailor.assess is unavailable).
GET /admin/system-stats returns scalar DB counts; no complex analytics.
"""

from fastapi import APIRouter

from backend.app.dependencies import AdminDep, DbDep
from backend.app.db.repositories.evaluation_run_repository import EvaluationRunRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.admin import SystemStats
from backend.app.schemas.evaluation import EvaluationRequest, EvaluationResponse
from backend.app.services.evaluation_service import EvaluationService
from backend.app.services.stats_service import StatsService

router = APIRouter()


def _eval_service(db) -> EvaluationService:
    return EvaluationService(
        eval_repo=EvaluationRunRepository(db),
        run_repo=GenerationRunRepository(db),
        doc_repo=TailoredDocumentRepository(db),
    )


@router.post("/evaluate-run", response_model=EvaluationResponse, status_code=201)
def evaluate_run(request: EvaluationRequest, _admin: AdminDep, db: DbDep):
    """
    Score a completed generation run using the assess pipeline.

    The generation run must have status='succeeded'. Creates or replaces
    the EvaluationRun record and returns the scores.
    Returns 404 if the run or its tailored document is not found.
    Returns 422 if the run has not yet succeeded.
    """
    return _eval_service(db).evaluate(request.generation_run_id)


@router.get("/system-stats", response_model=SystemStats)
def system_stats(_admin: AdminDep, db: DbDep):
    """Return aggregate counts across all users for admin dashboard."""
    return StatsService(db).get_system_stats()
