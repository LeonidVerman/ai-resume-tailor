"""
backend/app/services/job_scraper_service.py

Job scraper service — fetches a job posting URL and returns structured data.

Strategy
--------
1. Use the existing generator's scrape_job_url() when possible (it already
   handles LinkedIn, Wellfound, and generic JSON-LD extraction).
2. Fall back to the backend ScrapingClient for raw HTML when the generator
   scraper raises (e.g. Playwright not available in backend environment).
3. Return a JobScrapedData dict compatible with job_normalizer_service.

The existing generator's scrape.py is NOT modified.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class JobScrapedData:
    """Raw scraping output before normalization."""
    url: str
    company: str | None
    job_title: str | None
    raw_text: str
    source: str  # "generator" | "scraping_client"


class JobScraperService:
    """
    Fetch a job posting URL and return raw structured data.

    Dependencies
    ------------
    scraping_client: ScrapingClient (Phase 5) — used as fallback.
    """

    def __init__(self, scraping_client=None) -> None:
        self._scraping_client = scraping_client

    def scrape(self, url: str) -> JobScrapedData:
        """
        Scrape a job listing URL.

        Tries the existing generator's scrape_job_url() first (supports
        LinkedIn, Wellfound, generic JSON-LD).  Falls back to raw HTML
        fetch via ScrapingClient when the generator path fails.
        """
        logger.info("Scraping job URL: %s", url)

        # Primary path: reuse the existing generator scraper
        try:
            from tailor.job.scrape import scrape_job_url
            job_data = scrape_job_url(url)
            logger.debug(
                "Generator scraper succeeded: company=%r title=%r",
                job_data.company, job_data.job_title,
            )
            return JobScrapedData(
                url=url,
                company=job_data.company if job_data.company != "Unknown" else None,
                job_title=job_data.job_title if job_data.job_title != "Unknown" else None,
                raw_text=job_data.description,
                source="generator",
            )
        except Exception as exc:
            logger.warning("Generator scraper failed for %s: %s", url, exc)

        # Fallback: raw HTML via ScrapingClient
        if self._scraping_client is None:
            raise RuntimeError(
                f"Cannot scrape {url}: generator scraper failed and no ScrapingClient provided"
            )

        result = self._scraping_client.fetch(url)
        if not result.ok:
            raise RuntimeError(
                f"Failed to fetch job URL {url}: HTTP {result.status_code}"
            )

        logger.debug("ScrapingClient fallback succeeded, html_len=%d", len(result.html))
        # Best-effort text extraction from HTML
        raw_text = _extract_text_from_html(result.html)
        return JobScrapedData(
            url=result.url,
            company=None,
            job_title=None,
            raw_text=raw_text,
            source="scraping_client",
        )


def _extract_text_from_html(html: str) -> str:
    """Strip HTML tags and return plain text."""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        return soup.get_text(separator="\n")
    except ImportError:
        # Very basic fallback: strip angle-bracket tags
        import re
        return re.sub(r"<[^>]+>", " ", html)
