"""
backend/app/schemas/structured_resume.py

Structured resume schemas.

Represents a parsed, normalized resume — semantic content, not visual layout.
Aligned with the spec's Resume JSON Schema and the resume template
used by the existing generator.
"""

from datetime import datetime

from pydantic import Field

from backend.app.schemas.common import APIModel


# ── Sub-models ─────────────────────────────────────────────────────────────

class ContactInfo(APIModel):
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    website_url: str | None = None


class ExperienceEntry(APIModel):
    """A single employment/engagement entry."""
    company: str
    role: str
    start_date: str            # free-form, e.g. "Nov 2025"
    end_date: str = "Present"  # free-form or "Present"
    bullets: list[str] = Field(default_factory=list)
    # Optional: employment type (e.g. "Independent Contractor")
    employment_type: str | None = None


class EducationEntry(APIModel):
    institution: str
    degree: str | None = None
    field_of_study: str | None = None
    start_date: str | None = None
    end_date: str | None = None


class SkillsSection(APIModel):
    """
    Technical skills can be a flat list of strings (simple) or
    categorized (e.g. Languages: Python, Java; Infra: AWS, Docker).
    Both forms are supported.
    """
    flat: list[str] = Field(default_factory=list)
    categorized: dict[str, list[str]] = Field(default_factory=dict)


# ── Root resume document ───────────────────────────────────────────────────

class StructuredResumeDocument(APIModel):
    """
    Parsed, normalized resume document stored in JSONB.

    Aligns with the spec's Resume JSON Schema plus the richer structure
    the existing generator template uses (LinkedIn URL, employment type, etc.).
    """
    name: str
    contacts: ContactInfo = Field(default_factory=ContactInfo)
    summary: str = ""
    experience: list[ExperienceEntry] = Field(default_factory=list)
    technical_skills: SkillsSection = Field(default_factory=SkillsSection)
    education: list[EducationEntry] = Field(default_factory=list)
    # Raw text version used by the generator internally
    raw_text: str | None = None


# ── API request/response models ────────────────────────────────────────────

class StructuredResumeResponse(APIModel):
    """Returned by GET /resumes/{id}."""
    id: int
    user_id: str
    resume: StructuredResumeDocument
    source_file_url: str | None = None
    # Non-None when the uploaded file was a PDF and was converted to DOCX.
    input_conversion_warning: str | None = None
    created_at: datetime


class StructuredResumeSummary(APIModel):
    """Lightweight listing item for GET /resumes."""
    id: int
    name: str
    created_at: datetime
    source_file_url: str | None = None
