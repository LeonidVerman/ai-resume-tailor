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


class SignupCreditPolicyResponse(APIModel):
    """GET /admin/signup-credit-policy response."""
    initial_credits: int


class SignupCreditPolicyRequest(APIModel):
    """PUT /admin/signup-credit-policy request body."""
    initial_credits: int


class GenerationConfigRequest(APIModel):
    """PUT /admin/generation-config request body.

    All fields are optional — only the provided ones are updated.  The
    guest_* fields (issue #155) share this endpoint so the admin config
    stays a single-row upsert.
    """
    simple_model: str | None = None
    guest_enabled: bool | None = None
    guest_daily_global_cap: int | None = None
    guest_concurrent_cap: int | None = None
    guest_ip_daily_limit: int | None = None
    guest_retention_days: int | None = None


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
    app_version: str = "dev"
    build_date: str = ""


class AdminActionResponse(APIModel):
    """Generic success response for admin actions."""
    ok: bool
    message: str


# ── Guest metrics (issue #155, Phase 5) ────────────────────────────────────

class GuestConfigValues(APIModel):
    """Current guest generation config (kill switch + caps)."""
    guest_enabled: bool
    guest_daily_global_cap: int
    guest_concurrent_cap: int
    guest_ip_daily_limit: int
    guest_retention_days: int


class GuestFunnelTotals(APIModel):
    """Aggregate funnel counts over the reporting window."""
    sessions: int = 0
    profiles: int = 0
    generations_started: int = 0
    generations_completed: int = 0
    generations_failed: int = 0
    claims: int = 0


class GuestFunnelDay(GuestFunnelTotals):
    """Per-day funnel counts (date is ISO YYYY-MM-DD, UTC)."""
    date: str


class GuestMetricsResponse(APIModel):
    """GET /admin/guest-metrics response."""
    days: list[GuestFunnelDay]
    totals: GuestFunnelTotals
    config: GuestConfigValues


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
