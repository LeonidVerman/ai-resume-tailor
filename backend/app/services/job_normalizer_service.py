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
        logger.info("Created scraped JD id=%s user=%s url=%s", jd.id, user_id, scraped.url)
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
        logger.info("Created manual JD id=%s user=%s", jd.id, user_id)
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
        logger.info("Deleted JD id=%s user=%s", jd_id, user_id)

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _to_response(jd: JobDescription) -> JobDescriptionResponse:
        meta_raw = jd.metadata_jsonb or {}
        metadata = JobMetadata(
            company=meta_raw.get("company"),
            job_title=meta_raw.get("job_title"),
            location=meta_raw.get("location"),
            employment_type=meta_raw.get("employment_type"),
            seniority_level=meta_raw.get("seniority_level"),
            remote_policy=meta_raw.get("remote_policy"),
        ) if meta_raw else None
        return JobDescriptionResponse(
            id=jd.id,
            user_id=jd.user_id,
            source_url=jd.source_url,
            source_type=jd.source_type,
            raw_text=jd.raw_text,
            metadata=metadata,
            created_at=jd.created_at,
        )

    @staticmethod
    def _to_summary(jd: JobDescription) -> JobDescriptionSummary:
        meta_raw = jd.metadata_jsonb or {}
        return JobDescriptionSummary(
            id=jd.id,
            company=meta_raw.get("company"),
            job_title=meta_raw.get("job_title"),
            source_url=jd.source_url,
            created_at=jd.created_at,
        )
