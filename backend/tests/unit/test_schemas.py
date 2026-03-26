"""
backend/tests/unit/test_schemas.py

Unit tests for Pydantic schemas — validate field defaults, required fields,
type coercion, and model serialisation.
"""

import pytest
from pydantic import ValidationError

from backend.app.schemas.candidate_profile import (
    CandidateIdentity,
    CandidateProfileDocument,
    CandidateProfileUpsertRequest,
    DomainExperience,
)
from backend.app.schemas.generation import GenerationRequest
from backend.app.schemas.job_description import (
    JobDescriptionManualRequest,
    JobDescriptionScrapeRequest,
)
from backend.app.schemas.structured_resume import (
    ContactInfo,
    StructuredResumeDocument,
)


# ── CandidateProfileDocument ───────────────────────────────────────────────

class TestCandidateProfileDocument:
    def test_minimal_valid(self):
        doc = CandidateProfileDocument(
            candidate=CandidateIdentity(name="Alice Smith")
        )
        assert doc.candidate.name == "Alice Smith"
        assert doc.candidate.headline is None
        assert doc.domains == DomainExperience(primary=[], secondary=[])
        assert doc.experience_highlights == []
        assert doc.role_fit_themes == []

    def test_full_profile(self):
        doc = CandidateProfileDocument(
            candidate=CandidateIdentity(name="Bob", headline="SWE", summary="Experienced"),
            domains=DomainExperience(primary=["fintech"], secondary=["healthtech"]),
            role_fit_themes=["distributed systems", "team leadership"],
        )
        assert doc.domains.primary == ["fintech"]
        assert len(doc.role_fit_themes) == 2

    def test_candidate_name_required(self):
        with pytest.raises(ValidationError):
            CandidateProfileDocument(candidate=CandidateIdentity())

    def test_version_default(self):
        doc = CandidateProfileDocument(candidate=CandidateIdentity(name="X"))
        assert doc.candidate_profile_version == "2.0"

    def test_upsert_request(self):
        req = CandidateProfileUpsertRequest(
            profile=CandidateProfileDocument(
                candidate=CandidateIdentity(name="Carol")
            )
        )
        assert req.profile_version == "1"


# ── StructuredResumeDocument ───────────────────────────────────────────────

class TestStructuredResumeDocument:
    def test_minimal(self):
        doc = StructuredResumeDocument(name="Jane Doe", raw_text="resume text")
        assert doc.name == "Jane Doe"
        assert doc.contacts is None or isinstance(doc.contacts, ContactInfo)
        assert doc.experience == []

    def test_contact_info(self):
        ci = ContactInfo(
            email="jane@example.com",
            phone="+1-555-0100",
            linkedin_url="https://linkedin.com/in/janedoe",
        )
        assert ci.email == "jane@example.com"
        assert ci.github_url is None

    def test_model_dump_roundtrip(self):
        doc = StructuredResumeDocument(
            name="John",
            raw_text="some raw text",
            contacts=ContactInfo(email="john@example.com"),
        )
        data = doc.model_dump(mode="json")
        doc2 = StructuredResumeDocument.model_validate(data)
        assert doc2.name == "John"
        assert doc2.contacts.email == "john@example.com"


# ── GenerationRequest ──────────────────────────────────────────────────────

class TestGenerationRequest:
    def test_valid_request(self):
        req = GenerationRequest(
            job_description_id=42,
            structured_resume_id="resume-uuid",
        )
        assert req.job_description_id == 42
        assert req.structured_resume_id == "resume-uuid"

    def test_jd_id_required(self):
        with pytest.raises(ValidationError):
            GenerationRequest(structured_resume_id="res")


# ── JobDescription schemas ─────────────────────────────────────────────────

class TestJobDescriptionSchemas:
    def test_scrape_request(self):
        req = JobDescriptionScrapeRequest(url="https://example.com/job/123")
        assert req.url == "https://example.com/job/123"

    def test_manual_request(self):
        req = JobDescriptionManualRequest(
            raw_text="We are looking for a senior engineer..."
        )
        assert req.raw_text.startswith("We are looking")

    def test_scrape_url_required(self):
        with pytest.raises(ValidationError):
            JobDescriptionScrapeRequest()

    def test_manual_text_required(self):
        with pytest.raises(ValidationError):
            JobDescriptionManualRequest()
