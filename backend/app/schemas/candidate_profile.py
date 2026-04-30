"""
backend/app/schemas/candidate_profile.py

Candidate profile schemas — v2.0.

The profile is stored as JSONB in the DB. All sub-sections have default
factories so that old partial profiles (v1.x) continue to validate without
error. The normalizer service migrates v1.x structure (authz_authn_experience,
scalability_reliability_patterns) into the v2.0 layout at save time.
"""

from datetime import datetime

from pydantic import Field

from backend.app.schemas.common import APIModel


# ── Sub-models ─────────────────────────────────────────────────────────────

class CandidateContacts(APIModel):
    email: str = ""
    phone: str = ""
    linkedin_url: str | None = None
    location: str | None = None


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
    security_auth_patterns: list[str] = Field(default_factory=list)


class TechnicalSkills(APIModel):
    languages: list[str] = Field(default_factory=list)
    backend_systems: list[str] = Field(default_factory=list)
    datastores: list[str] = Field(default_factory=list)
    infra_devops: list[str] = Field(default_factory=list)
    frontend: list[str] = Field(default_factory=list)
    api_patterns: list[str] = Field(default_factory=list)
    async_messaging: list[str] = Field(default_factory=list)
    observability: list[str] = Field(default_factory=list)
    security_auth_patterns: list[str] = Field(default_factory=list)
    scalability_reliability_patterns: list[str] = Field(default_factory=list)


class ClaimBoundaries(APIModel):
    security_auth: list[str] = Field(default_factory=list)
    domain_limits: list[str] = Field(default_factory=list)
    employment_constraints: list[str] = Field(default_factory=list)


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

    Version 2.0 — adds security_auth_patterns and scalability_reliability_patterns
    to TechnicalSkills and ExperienceHighlight, and adds a ClaimBoundaries section.
    All sections have defaults for backward compatibility with v1.x profiles;
    the normalizer migrates old top-level fields at save time.
    """
    candidate_profile_version: str = "2.0"
    candidate: CandidateIdentity
    contacts: CandidateContacts = Field(default_factory=CandidateContacts)
    domains: DomainExperience = Field(default_factory=DomainExperience)
    experience_highlights: list[ExperienceHighlight] = Field(default_factory=list)
    technical_skills: TechnicalSkills = Field(default_factory=TechnicalSkills)
    leadership: Leadership = Field(default_factory=Leadership)
    ai_tooling_practice: AIToolingPractice = Field(default_factory=AIToolingPractice)
    role_fit_themes: list[str] = Field(default_factory=list)
    constraints_and_preferences: ConstraintsAndPreferences = Field(
        default_factory=ConstraintsAndPreferences
    )
    claim_boundaries: ClaimBoundaries = Field(default_factory=ClaimBoundaries)


# ── API request/response models ────────────────────────────────────────────

class CandidateProfileUpsertRequest(APIModel):
    """Request body for POST /candidate-profile and PUT /candidate-profile."""
    profile: CandidateProfileDocument
    profile_version: str = "1"


class CandidateProfileResponse(APIModel):
    """Full profile response returned by the API."""
    id: int
    user_id: str
    profile_version: str
    profile: CandidateProfileDocument
    onboarding_completed: bool
    source_resume_id: int | None = None
    created_at: datetime
    updated_at: datetime
