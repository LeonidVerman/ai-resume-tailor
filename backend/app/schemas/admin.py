"""
backend/app/schemas/admin.py

Admin API schemas.
"""

from backend.app.schemas.common import APIModel


class SystemStats(APIModel):
    """Response for GET /admin/system-stats."""
    total_users: int
    total_generation_runs: int
    total_succeeded_runs: int
    total_failed_runs: int
    total_tailored_documents: int
    total_evaluation_runs: int


class AdminActionResponse(APIModel):
    """Generic success response for admin actions."""
    ok: bool
    message: str
