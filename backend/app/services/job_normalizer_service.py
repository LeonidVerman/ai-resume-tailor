"""
backend/app/services/job_normalizer_service.py

Job normalizer service — converts raw scraped/manual JD text into a
structured DB record via the JobDescriptionRepository.

Responsibilities
----------------
- Accept raw text (from scraper or manual entry) + optional metadata
- Store in DB via repository
- Return a JobDescriptionResponse

The generator's extract_metadata_ai() is intentionally NOT called here
to avoid an extra LLM call at ingest time.  Metadata enrichment can be
triggered separately or during generation.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, status

from backend.app.db.models.job_description import JobDescription
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.schemas.job_description import (
    JobDescriptionManualRequest,
    JobDescriptionResponse,
    JobDescriptionSummary,
    JobMetadata,
)
from backend.app.services.job_scraper_service import JobScrapedData

logger = logging.getLogger(__name__)


class JobNormalizerService:
    """
    Store and retrieve job description records.

    Dependencies
    ------------
    repo: JobDescriptionRepository
    """

    def __init__(self, repo: JobDescriptionRepository) -> None:
        self._repo = repo

    # ── Create ─────────────────────────────────────────────────────────────

    def create_from_scrape(
        self, user_id: str, scraped: JobScrapedData
    ) -> JobDescriptionResponse:
        """Persist a scraped job description."""
        metadata = {"company": scraped.company, "job_title": scraped.job_title}
        jd = self._repo.create(
            user_id=user_id,
            source_url=scraped.url,
            source_type="scraped",
            raw_text=scraped.raw_text,
            metadata_jsonb=metadata,
        )
        logger.info(
            "Job description added jd_id=%s user_id=%s type=scraped company=%s title=%s url=%s",
            jd.id, user_id, scraped.company, scraped.job_title, scraped.url,
        )
        return self._to_response(jd)

    def create_from_manual(
        self, user_id: str, request: JobDescriptionManualRequest
    ) -> JobDescriptionResponse:
        """Persist a manually submitted job description."""
        metadata: dict = {}
        if request.company:
            metadata["company"] = request.company
        if request.job_title:
            metadata["job_title"] = request.job_title
        jd = self._repo.create(
            user_id=user_id,
            source_url=request.source_url,
            source_type="manual",
            raw_text=request.raw_text,
            metadata_jsonb=metadata or None,
        )
        logger.info(
            "Job description added jd_id=%s user_id=%s type=manual company=%s title=%s",
            jd.id, user_id, metadata.get("company"), metadata.get("job_title"),
        )
        return self._to_response(jd)

    # ── Read ───────────────────────────────────────────────────────────────

    def get_by_id(self, jd_id: int, user_id: str) -> JobDescriptionResponse:
        jd = self._repo.get_by_id(jd_id)
        if jd is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")
        if jd.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        return self._to_response(jd)

    def list_by_user(self, user_id: str) -> list[JobDescriptionSummary]:
        jds = self._repo.list_by_user_id(user_id)
        return [self._to_summary(jd) for jd in jds]

    def delete(self, jd_id: int, user_id: str) -> None:
        jd = self._repo.get_by_id(jd_id)
        if jd is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")
        if jd.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        self._repo.delete(jd)
        logger.info("Job description deleted jd_id=%s user_id=%s", jd_id, user_id)

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _clean(value: str | None) -> str | None:
        """Strip legacy placeholder values so they are never shown as real data."""
        if value is None:
            return None
        stripped = value.strip()
        if not stripped or stripped.lower() == "unknown":
            return None
        return stripped

    @staticmethod
    def _parse_status(source_type: str | None, company: str | None, job_title: str | None) -> str:
        if source_type == "manual":
            return "manual"
        # scraped — ok only when both fields were successfully extracted
        if company and job_title:
            return "ok"
        return "partial"

    @staticmethod
    def _to_response(jd: JobDescription) -> JobDescriptionResponse:
        meta_raw = jd.metadata_jsonb or {}
        company = JobNormalizerService._clean(meta_raw.get("company"))
        job_title = JobNormalizerService._clean(meta_raw.get("job_title"))
        metadata = JobMetadata(
            company=company,
            job_title=job_title,
            location=JobNormalizerService._clean(meta_raw.get("location")),
            employment_type=JobNormalizerService._clean(meta_raw.get("employment_type")),
            seniority_level=JobNormalizerService._clean(meta_raw.get("seniority_level")),
            remote_policy=JobNormalizerService._clean(meta_raw.get("remote_policy")),
        ) if meta_raw else None
        return JobDescriptionResponse(
            id=jd.id,
            user_id=jd.user_id,
            source_url=jd.source_url,
            source_type=jd.source_type,
            raw_text=jd.raw_text,
            metadata=metadata,
            parse_status=JobNormalizerService._parse_status(jd.source_type, company, job_title),
            created_at=jd.created_at,
        )

    @staticmethod
    def _to_summary(jd: JobDescription) -> JobDescriptionSummary:
        meta_raw = jd.metadata_jsonb or {}
        company = JobNormalizerService._clean(meta_raw.get("company"))
        job_title = JobNormalizerService._clean(meta_raw.get("job_title"))
        return JobDescriptionSummary(
            id=jd.id,
            company=company,
            job_title=job_title,
            source_url=jd.source_url,
            parse_status=JobNormalizerService._parse_status(jd.source_type, company, job_title),
            created_at=jd.created_at,
        )
