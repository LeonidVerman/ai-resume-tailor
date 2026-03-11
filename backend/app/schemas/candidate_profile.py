"""
backend/app/schemas/candidate_profile.py

Candidate profile schemas — v1.1.

The profile is stored as JSONB in the DB. All sub-sections have default
factories so that old partial profiles (v1.0, domains-only) continue to
validate without error. Deprecated v1.0 fields (authz_authn_experience,
scalability_reliability_patterns) are silently dropped by Pydantic on load.
"""

from datetime import datetime

from pydantic import Field

from backend.app.schemas.common import APIModel


# ── Sub-models ─────────────────────────────────────────────────────────────

class CandidateIdentity(APIModel):
    name: str
    headline: str | None = None
    summary: str | None = None


class DomainExperience(APIModel):
    primary: list[str] = Field(default_factory=list)
    secondary: list[str] = Field(default_factory=list)


class ExperienceHighlight(APIModel):
    area: str
    market: str | None = None
    employer_relationship: str | None = None
    impact: list[str] = Field(default_factory=list)
    team_context: list[str] = Field(default_factory=list)
    architecture_patterns: list[str] = Field(default_factory=list)
    constraints_and_tradeoffs: list[str] = Field(default_factory=list)
    skills_applied: list[str] = Field(default_factory=list)


class TechnicalSkills(APIModel):
    languages: list[str] = Field(default_factory=list)
    backend_systems: list[str] = Field(default_factory=list)
    datastores: list[str] = Field(default_factory=list)
    infra_devops: list[str] = Field(default_factory=list)
    api_patterns: list[str] = Field(default_factory=list)
    async_messaging: list[str] = Field(default_factory=list)
    observability: list[str] = Field(default_factory=list)


class LeadershipScope(APIModel):
    team_size_max: int | None = None
    style_keywords: list[str] = Field(default_factory=list)


class Leadership(APIModel):
    scope: LeadershipScope = Field(default_factory=LeadershipScope)
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


# ── Root profile document ──────────────────────────────────────────────────

class CandidateProfileDocument(APIModel):
    """
    Full candidate profile document stored in JSONB.

    Version 1.1 — adds leadership, AI tooling, role fit themes, and
    constraints. All sections have defaults for backward compatibility
    with v1.0 profiles. Deprecated fields are silently ignored by Pydantic.
    """
    candidate_profile_version: str = "1.1"
    candidate: CandidateIdentity
    domains: DomainExperience = Field(default_factory=DomainExperience)
    experience_highlights: list[ExperienceHighlight] = Field(default_factory=list)
    technical_skills: TechnicalSkills = Field(default_factory=TechnicalSkills)
    leadership: Leadership = Field(default_factory=Leadership)
    ai_tooling_practice: AIToolingPractice = Field(default_factory=AIToolingPractice)
    role_fit_themes: list[str] = Field(default_factory=list)
    constraints_and_preferences: ConstraintsAndPreferences = Field(
        default_factory=ConstraintsAndPreferences
    )


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
