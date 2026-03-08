"""
backend/app/schemas/job_description.py

Job description schemas.

Aligned with the existing generator's JobData dataclass
(src/tailor/job/__init__.py) and the DB model from Phase 3.
"""

from datetime import datetime
from typing import Literal

from backend.app.schemas.common import APIModel


# ── Parsed metadata ────────────────────────────────────────────────────────

class JobMetadata(APIModel):
    """
    Structured metadata extracted from a raw JD.
    Mirrors what the existing generator's extract_metadata_ai() produces.
    """
    company: str | None = None
    job_title: str | None = None
    location: str | None = None
    employment_type: str | None = None   # full-time, contract, etc.
    seniority_level: str | None = None   # senior, staff, director, etc.
    remote_policy: str | None = None     # remote, hybrid, onsite


# ── Input models ───────────────────────────────────────────────────────────

class JobDescriptionScrapeRequest(APIModel):
    """Request body for POST /job-description/scrape."""
    url: str


class JobDescriptionManualRequest(APIModel):
    """Request body for POST /job-description/manual."""
    raw_text: str
    company: str | None = None
    job_title: str | None = None
    source_url: str | None = None


# ── Response models ────────────────────────────────────────────────────────

class JobDescriptionResponse(APIModel):
    """Full JD response returned by GET /job-descriptions/{id}."""
    id: str
    user_id: str
    source_url: str | None = None
    source_type: Literal["scraped", "manual"] | None = None
    raw_text: str
    metadata: JobMetadata | None = None
    created_at: datetime


class JobDescriptionSummary(APIModel):
    """Lightweight listing item for GET /job-descriptions."""
    id: str
    company: str | None = None
    job_title: str | None = None
    source_url: str | None = None
    created_at: datetime
