"""
backend/app/schemas/admin.py

Admin API schemas.
"""

from typing import Literal

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

GenerationMode = Literal["simple", "two_phase"]


class GenerationConfigRequest(APIModel):
    """PUT /admin/generation-config request body."""
    generation_mode: GenerationMode
    simple_model: str
    phase1_model: str
    phase2_model: str


class GenerationConfigResponse(APIModel):
    """GET /admin/generation-config response."""
    generation_mode: GenerationMode
    simple_model: str
    phase1_model: str
    phase2_model: str
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
