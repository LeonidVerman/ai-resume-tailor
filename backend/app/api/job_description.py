"""
backend/app/api/job_description.py

Job description endpoints.

Endpoints
---------
POST /job-descriptions/scrape    — scrape a job posting URL
POST /job-descriptions/manual    — submit raw JD text manually
GET  /job-descriptions           — list the user's stored JDs
GET  /job-descriptions/{id}      — return a specific JD record
DELETE /job-descriptions/{id}    — delete a JD record

Phase 8 status: FUNCTIONAL (scraping requires Playwright/generator env)
-----------------------------------------------------------------------
The /scrape endpoint delegates to the existing generator's scrape_job_url()
via JobScraperService.  If the generator environment is not available
(e.g. missing Playwright), the endpoint returns a 503.
"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from backend.app.dependencies import CurrentUserDep, DbDep
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.schemas.job_description import (
    JobDescriptionManualRequest,
    JobDescriptionResponse,
    JobDescriptionScrapeRequest,
    JobDescriptionSummary,
)
from backend.app.services.job_normalizer_service import JobNormalizerService
from backend.app.services.job_scraper_service import JobScraperService
from tailor.job.scrape import get_scrape_failure_message

router = APIRouter()


def _normalizer(db: Session) -> JobNormalizerService:
    return JobNormalizerService(JobDescriptionRepository(db))


@router.post("/scrape", response_model=JobDescriptionResponse, status_code=201)
def scrape_job_description(
    request: JobDescriptionScrapeRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """
    Fetch a job listing from a URL and persist it.

    Guests are paste-only (#155): URL fetching exposes the scraper to
    anonymous traffic and stays registered-only.

    Uses the existing generator's scraper (LinkedIn, Wellfound, generic JSON-LD).
    Falls back to raw HTML extraction via httpx / Playwright.
    """
    if getattr(user, "is_anonymous", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="GUEST_PASTE_ONLY: paste the job description text — URL "
                   "fetch is available after creating a free account",
        )
    scraper = JobScraperService()
    try:
        scraped = scraper.scrape(request.url)
    except RuntimeError:
        msg = get_scrape_failure_message(request.url)
        logger.warning("Scrape failed for %s — returning: %s", request.url, msg)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=msg,
        )
    return _normalizer(db).create_from_scrape(user.id, scraped)


@router.post("/manual", response_model=JobDescriptionResponse, status_code=201)
def submit_manual_job_description(
    request: JobDescriptionManualRequest,
    user: CurrentUserDep,
    db: DbDep,
):
    """Persist a manually pasted job description."""
    return _normalizer(db).create_from_manual(user.id, request)


@router.get("", response_model=list[JobDescriptionSummary])
def list_job_descriptions(user: CurrentUserDep, db: DbDep):
    """List all stored job descriptions for the authenticated user."""
    return _normalizer(db).list_by_user(user.id)


@router.get("/{jd_id}", response_model=JobDescriptionResponse)
def get_job_description(jd_id: int, user: CurrentUserDep, db: DbDep):
    """Return a specific job description record."""
    return _normalizer(db).get_by_id(jd_id, user.id)


@router.delete("/{jd_id}", status_code=204)
def delete_job_description(jd_id: int, user: CurrentUserDep, db: DbDep):
    """Delete a stored job description."""
    _normalizer(db).delete(jd_id, user.id)
