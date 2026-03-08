"""
backend/app/schemas/candidate_profile.py

Candidate profile schemas.

Grounded in the real profile structure used by the existing CLI generator
(profile/candidate_profile.json), extended to be generic for SaaS users.

The profile is stored as JSONB in the DB; these models validate and
document the expected document shape.
"""

from datetime import datetime
from typing import Any

from pydantic import Field

from backend.app.schemas.common import APIModel


# ── Sub-models ─────────────────────────────────────────────────────────────

class CandidateIdentity(APIModel):
    """Basic candidate identity block."""
    name: str
    headline: str | None = None
    summary: str | None = None


class DomainExperience(APIModel):
    """Industry/domain knowledge areas."""
    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)


class ExperienceHighlight(APIModel):
    """A notable experience area with impact, context, and patterns.
    Kept flexible (dict fields) because content varies significantly.
    """
    area: str
    market: str | None = None
    employer_relationship: str | None = None
    impact: list[str] = Field(default_factory=list)
    team_context: list[str] = Field(default_factory=list)
    architecture_patterns: list[str] = Field(default_factory=list)
    constraints_and_tradeoffs: list[str] = Field(default_factory=list)
    skills_applied: list[str] = Field(default_factory=list)


class TechnicalSkills(APIModel):
    """Technical skill inventory, grouped by category."""
    languages: list[str] = Field(default_factory=list)
    backend_systems: list[str] = Field(default_factory=list)
    datastores: list[str] = Field(default_factory=list)
    infra_devops: list[str] = Field(default_factory=list)
    api_patterns: list[str] = Field(default_factory=list)
    async_messaging: list[str] = Field(default_factory=list)
    observability: list[str] = Field(default_factory=list)
    # catch-all for extra categories a generic user may supply
    other: dict[str, list[str]] = Field(default_factory=dict)


class AuthzAuthnExperience(APIModel):
    implemented: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    scalability_challenges: list[str] = Field(default_factory=list)


class LeadershipScope(APIModel):
    team_size_max: int | None = None
    style_keywords: list[str] = Field(default_factory=list)


class Leadership(APIModel):
    scope: LeadershipScope | None = None
    practices: list[str] = Field(default_factory=list)
    risk_management: list[str] = Field(default_factory=list)


class AIToolingPractice(APIModel):
    hands_on_tools: list[str] = Field(default_factory=list)
    usage_patterns: list[str] = Field(default_factory=list)
    principles: list[str] = Field(default_factory=list)
    concepts_familiarity: list[str] = Field(default_factory=list)


class ConstraintsAndPreferences(APIModel):
    work_context: list[str] = Field(default_factory=list)
    communication: list[str] = Field(default_factory=list)
    resume_constraint: list[str] = Field(default_factory=list)
    # catch-all for arbitrary extra preferences
    extra: dict[str, Any] = Field(default_factory=dict)


# ── Root profile document ──────────────────────────────────────────────────

class CandidateProfileDocument(APIModel):
    """
    Full candidate profile document stored in JSONB.

    Mirrors the structure of profile/candidate_profile.json used by the
    existing generator, while remaining generic for SaaS users.

    All sections beyond candidate identity are optional to support
    partial profiles during onboarding.
    """
    candidate_profile_version: str = "1.0"
    candidate: CandidateIdentity
    domains: DomainExperience | None = None
    experience_highlights: list[ExperienceHighlight] = Field(default_factory=list)
    technical_skills: TechnicalSkills | None = None
    authz_authn_experience: AuthzAuthnExperience | None = None
    scalability_reliability_patterns: list[str] = Field(default_factory=list)
    leadership: Leadership | None = None
    ai_tooling_practice: AIToolingPractice | None = None
    role_fit_themes: list[str] = Field(default_factory=list)
    constraints_and_preferences: ConstraintsAndPreferences | None = None


# ── API request/response models ────────────────────────────────────────────

class CandidateProfileUpsertRequest(APIModel):
    """Request body for POST /candidate-profile and PUT /candidate-profile."""
    profile: CandidateProfileDocument
    profile_version: str = "1"


class CandidateProfileResponse(APIModel):
    """Full profile response returned by the API."""
    id: str
    user_id: str
    profile_version: str
    profile: CandidateProfileDocument
    created_at: datetime
    updated_at: datetime
