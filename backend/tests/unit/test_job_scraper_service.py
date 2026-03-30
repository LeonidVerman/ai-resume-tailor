"""
backend/tests/unit/test_job_scraper_service.py

Unit tests for JobScraperService and its HTTP fallback helpers.
All network calls are mocked — no real HTTP requests are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.job_scraper_service import (
    JobScraperService,
    JobScrapedData,
    _extract_from_next_data,
    _extract_text_from_html,
    _http_scrape,
)


# ── Fixtures ────────────────────────────────────────────────────────────────

HIRING_CAFE_URL = "https://hiring.cafe/viewjob/83jfi7dadcki07vc"

JSONLD_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Staff Engineer",
 "hiringOrganization": {"name": "WidgetCo"},
 "description": "We are looking for a Staff Engineer."}
</script>
</head><body>Staff Engineer at WidgetCo</body></html>
"""

NEXT_DATA_HTML = """
<html><head></head><body>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "job": {
        "title": "Principal Engineer",
        "company": "HiringCafe Inc",
        "description": "Join our team as a Principal Engineer."
      }
    }
  }
}
</script>
</body></html>
"""

PLAIN_HTML = """
<html>
<head><title>Senior Developer @ Acme</title>
<meta property="og:title" content="Senior Developer">
<meta property="og:site_name" content="Acme Corp">
</head>
<body>
<main><p>We need a Senior Developer with Python skills.</p></main>
<script>var x = 1;</script>
</body></html>
"""


def _mock_response(html: str, url: str = HIRING_CAFE_URL):
    resp = MagicMock()
    resp.text = html
    resp.url = url
    resp.raise_for_status.return_value = None
    return resp


# ── _extract_from_next_data ──────────────────────────────────────────────────


class TestExtractFromNextData:
    def test_extracts_title_company_description(self):
        data = {
            "props": {
                "pageProps": {
                    "job": {
                        "title": "Principal Engineer",
                        "company": "HiringCafe Inc",
                        "description": "Join our team.",
                    }
                }
            }
        }
        company, title, desc = _extract_from_next_data(data)
        assert title == "Principal Engineer"
        assert company == "HiringCafe Inc"
        assert "Join our team" in desc

    def test_handles_html_in_description(self):
        data = {
            "props": {
                "pageProps": {
                    "job": {"title": "SWE", "description": "<p>We need <b>Python</b> skills.</p>"}
                }
            }
        }
        _, _, desc = _extract_from_next_data(data)
        assert "<p>" not in desc
        assert "Python" in desc

    def test_returns_empty_when_no_description(self):
        data = {"props": {"pageProps": {"title": "Engineer"}}}
        _, _, desc = _extract_from_next_data(data)
        assert desc == ""

    def test_nested_structure(self):
        data = {
            "props": {
                "pageProps": {
                    "jobData": {
                        "details": {
                            "jobTitle": "Staff SWE",
                            "companyName": "ACME",
                            "jobDescription": "Full description here.",
                        }
                    }
                }
            }
        }
        company, title, desc = _extract_from_next_data(data)
        assert title == "Staff SWE"
        assert company == "ACME"
        assert "Full description" in desc


# ── _http_scrape ─────────────────────────────────────────────────────────────


class TestHttpScrape:
    def test_uses_jsonld_when_present(self):
        with patch("httpx.get", return_value=_mock_response(JSONLD_HTML)):
            result = _http_scrape(HIRING_CAFE_URL)
        assert result.job_title == "Staff Engineer"
        assert result.company == "WidgetCo"
        assert "Staff Engineer" in result.raw_text
        assert result.source == "http_fallback"

    def test_uses_next_data_when_no_jsonld(self):
        with patch("httpx.get", return_value=_mock_response(NEXT_DATA_HTML)):
            result = _http_scrape(HIRING_CAFE_URL)
        assert result.job_title == "Principal Engineer"
        assert result.company == "HiringCafe Inc"
        assert "Principal Engineer" in result.raw_text
        assert result.source == "http_fallback"

    def test_falls_back_to_og_and_text(self):
        with patch("httpx.get", return_value=_mock_response(PLAIN_HTML)):
            result = _http_scrape(HIRING_CAFE_URL)
        assert result.job_title == "Senior Developer"
        assert result.company == "Acme Corp"
        assert "Python" in result.raw_text

    def test_raises_on_http_error(self):
        resp = MagicMock()
        resp.raise_for_status.side_effect = Exception("HTTP 404")
        with patch("httpx.get", return_value=resp):
            with pytest.raises(Exception, match="HTTP 404"):
                _http_scrape(HIRING_CAFE_URL)


# ── JobScraperService.scrape ──────────────────────────────────────────────────


class TestJobScraperServiceScrape:
    def test_uses_generator_scraper_when_available(self):
        svc = JobScraperService()
        mock_job = MagicMock()
        mock_job.company = "Acme"
        mock_job.job_title = "Engineer"
        mock_job.description = "Engineer role."

        with patch("tailor.job.scrape.scrape_job_url", return_value=mock_job):
            result = svc.scrape(HIRING_CAFE_URL)

        assert result.source == "generator"
        assert result.company == "Acme"
        assert result.job_title == "Engineer"

    def test_falls_back_to_http_when_playwright_unavailable(self):
        """Simulates the HiringCafe scenario: Playwright unavailable in backend."""
        svc = JobScraperService()

        with (
            patch(
                "tailor.job.scrape.scrape_job_url",
                side_effect=Exception("Playwright not available"),
            ),
            patch("httpx.get", return_value=_mock_response(NEXT_DATA_HTML)),
        ):
            result = svc.scrape(HIRING_CAFE_URL)

        assert result.source == "http_fallback"
        assert result.job_title == "Principal Engineer"
        assert result.company == "HiringCafe Inc"

    def test_raises_when_all_strategies_fail(self):
        svc = JobScraperService()

        with (
            patch(
                "tailor.job.scrape.scrape_job_url",
                side_effect=Exception("Playwright not available"),
            ),
            patch("httpx.get", side_effect=Exception("Connection refused")),
        ):
            with pytest.raises(RuntimeError, match="all scraping strategies failed"):
                svc.scrape(HIRING_CAFE_URL)

    def test_generator_unknown_company_becomes_none(self):
        svc = JobScraperService()
        mock_job = MagicMock()
        mock_job.company = "Unknown"
        mock_job.job_title = "Unknown"
        mock_job.description = "Some description."

        with patch("tailor.job.scrape.scrape_job_url", return_value=mock_job):
            result = svc.scrape(HIRING_CAFE_URL)

        assert result.company is None
        assert result.job_title is None

    def test_scraping_client_used_as_last_resort(self):
        mock_client = MagicMock()
        mock_client.fetch.return_value = MagicMock(
            ok=True, html=PLAIN_HTML, url=HIRING_CAFE_URL
        )
        svc = JobScraperService(scraping_client=mock_client)

        with (
            patch(
                "tailor.job.scrape.scrape_job_url",
                side_effect=Exception("Playwright not available"),
            ),
            patch("httpx.get", side_effect=Exception("Connection refused")),
        ):
            result = svc.scrape(HIRING_CAFE_URL)

        assert result.source == "scraping_client"
        mock_client.fetch.assert_called_once_with(HIRING_CAFE_URL)


# ── _extract_text_from_html ───────────────────────────────────────────────────


class TestExtractTextFromHtml:
    def test_strips_script_and_style(self):
        html = "<html><body><p>Job text</p><script>bad()</script></body></html>"
        text = _extract_text_from_html(html)
        assert "Job text" in text
        assert "bad()" not in text

    def test_plain_text_passthrough(self):
        text = _extract_text_from_html("<p>Hello world</p>")
        assert "Hello world" in text
