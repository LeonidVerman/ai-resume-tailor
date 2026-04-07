"""
backend/app/schemas/admin.py

Admin API schemas.
"""

from datetime import datetime

from backend.app.schemas.common import APIModel

# Curated list of OpenAI models available for generation.
AVAILABLE_MODELS: list[str] = [
    "gpt-5.2",
    "gpt-5.1",
    "gpt-4.1",
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1-mini",
]


class GenerationConfigRequest(APIModel):
    """PUT /admin/generation-config request body."""
    simple_model: str


class GenerationConfigResponse(APIModel):
    """GET /admin/generation-config response."""
    simple_model: str
    available_models: list[str]


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


# ── Benchmark schemas ──────────────────────────────────────────────────────

class BenchmarkStartRequest(APIModel):
    """POST /admin/benchmark-runs request body."""
    client_id: str
    assess_model: str = "gpt-5.2"
    generation_mode: str = "conservative"


class BenchmarkRunSummary(APIModel):
    """Compact benchmark run row for the dashboard history table."""
    id: int
    client_id: str
    status: str
    positions_count: int | None
    completed_positions: int
    integrated_score: float | None
    generation_mode: str | None = None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class BenchmarkPositionSummary(APIModel):
    """Per-position result for the details modal."""
    id: int
    position_url: str
    company: str | None
    role_title: str | None
    truthfulness_score: float | None
    role_fit_score: float | None
    seniority_positioning_score: float | None
    clarity_impact_score: float | None
    mechanism_quality_score: float | None
    constraint_compliance_score: float | None
    cover_letter_effectiveness_score: float | None
    overall_readiness_score: float | None
    integrated_score: float | None


class BenchmarkRunDetail(BenchmarkRunSummary):
    """Full benchmark run detail including scores and config."""
    truthfulness_score: float | None
    role_fit_score: float | None
    seniority_positioning_score: float | None
    clarity_impact_score: float | None
    mechanism_quality_score: float | None
    constraint_compliance_score: float | None
    cover_letter_effectiveness_score: float | None
    overall_readiness_score: float | None
    simple_model: str | None
    assess_model: str | None
    error_message: str | None
    report_dir: str | None
    weights_json: dict | None
    positions: list[BenchmarkPositionSummary]
    # generation_mode inherited from BenchmarkRunSummary
