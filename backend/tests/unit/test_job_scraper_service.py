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
    _extract_rendered_meta,
    _company_from_hostname,
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

# Simulates a Taleo ATS shell: og:title present, og:site_name absent,
# raw_text >= 300 chars (loading-screen chrome).
TALEO_SHELL_HTML = """
<html>
<head>
<meta property="og:title" content="Staff Software Engineer">
</head>
<body>
<div class="app-shell">
  <p>Please wait while the application loads. The content will appear momentarily.</p>
  <p>If the page does not load within a few seconds, please try refreshing your browser.</p>
  <p>We apologise for any inconvenience. This application requires JavaScript to function.</p>
  <p>Please ensure that JavaScript is enabled in your browser settings before proceeding.</p>
</div>
</body>
</html>
"""

# Simulates Taleo's fully-rendered page returned by ScraperAPI (JSON-LD present).
TALEO_RENDERED_HTML = """
<html><head>
<script type="application/ld+json">
{"@type": "JobPosting", "title": "Staff Software Engineer",
 "hiringOrganization": {"name": "lululemon athletica"},
 "description": "We are looking for a Staff Software Engineer to join our team and build great things."}
</script>
</head><body><h1>Staff Software Engineer</h1></body></html>
"""

# Simulates a HiBob SPA shell: generic og:title, no body content.
HIBOB_SHELL_HTML = """
<html>
<head>
<title>Careers</title>
<meta property="og:title" content="Careers">
</head>
<body><div id="root"></div></body>
</html>
"""

# Simulates HiBob's fully-rendered page returned by ScraperAPI.
HIBOB_RENDERED_HTML = """
<html>
<head>
<title>Backend Engineer - HiBob</title>
<meta property="og:title" content="Backend Engineer">
<meta property="og:site_name" content="HiBob">
</head>
<body>
<h1>Backend Engineer</h1>
<div>We are looking for a Backend Engineer with 5+ years of Python experience.
You will scale our HR platform working alongside distributed-systems specialists.
Strong knowledge of PostgreSQL, Redis, and cloud infrastructure required.
Competitive salary, equity, and great benefits included.</div>
</body>
</html>
"""


def _mock_response(html: str, url: str = HIRING_CAFE_URL):
    resp = MagicMock()
    resp.text = html
    resp.url = url
    resp.status_code = 200
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


# ── ScraperAPI integration in _http_scrape ───────────────────────────────────


class TestHttpScrapeScraperAPI:
    """ScraperAPI step: triggered when company is absent or text is thin."""

    def test_taleo_shell_triggers_scraperapi_and_uses_jsonld(self):
        """Lululemon/Taleo: shell has ≥300 chars but no company → ScraperAPI →
        JSON-LD in rendered page gives company + title + description."""
        with (
            patch("httpx.get", side_effect=[
                _mock_response(TALEO_SHELL_HTML, "https://careers.lululemon.com/en_US/careers/JobDetail/Staff-Software-Engineer/59455"),
                _mock_response(TALEO_RENDERED_HTML),
            ]),
            patch.dict("os.environ", {"SCRAPER_API_KEY": "test-key"}),
        ):
            result = _http_scrape("https://careers.lululemon.com/en_US/careers/JobDetail/Staff-Software-Engineer/59455")

        assert result.job_title == "Staff Software Engineer"
        assert result.company == "lululemon athletica"
        assert "Staff Software Engineer" in result.raw_text
        assert result.source == "http_fallback"

    def test_hibob_shell_triggers_scraperapi_and_re_extracts_og_title(self):
        """HiBob: SPA shell has generic 'Careers' og:title → ScraperAPI renders →
        real job title and company re-extracted from rendered DOM."""
        with (
            patch("httpx.get", side_effect=[
                _mock_response(HIBOB_SHELL_HTML, "https://hibob-e360.careers.hibob.com/jobs/some-uuid"),
                _mock_response(HIBOB_RENDERED_HTML),
            ]),
            patch.dict("os.environ", {"SCRAPER_API_KEY": "test-key"}),
        ):
            result = _http_scrape("https://hibob-e360.careers.hibob.com/jobs/some-uuid")

        assert result.job_title == "Backend Engineer"
        assert result.company == "HiBob"
        assert "Backend Engineer" in result.raw_text
        assert result.source == "http_fallback"

    def test_step3_returns_when_company_and_text_both_present(self):
        """Normal SSR page: step 3 returns early without calling ScraperAPI."""
        long_body = "<p>" + ("We need a Senior Developer with Python skills. " * 10) + "</p>"
        html = PLAIN_HTML.replace(
            "<p>We need a Senior Developer with Python skills.</p>", long_body
        )
        mock_get = MagicMock(return_value=_mock_response(html))
        with (
            patch("httpx.get", mock_get),
            patch.dict("os.environ", {"SCRAPER_API_KEY": "test-key"}),
        ):
            result = _http_scrape(HIRING_CAFE_URL)

        assert result.company == "Acme Corp"
        assert result.job_title == "Senior Developer"
        mock_get.assert_called_once()  # ScraperAPI not called

    def test_lululemon_taleo_company_derived_from_hostname(self):
        """Lululemon/Taleo: SSR page has >=300 chars but no og:site_name →
        company derived from hostname; no ScraperAPI call needed."""
        # Plain HTTP gives 15 K of SSR content but no og:site_name
        long_body = "<p>" + ("We are looking for a Staff Software Engineer. " * 40) + "</p>"
        html = f"""
        <html><head>
        <meta property="og:title" content="Staff Software Engineer">
        </head><body>{long_body}</body></html>
        """
        lulu_url = "https://careers.lululemon.com/en_US/careers/JobDetail/Staff-SWE/59455"
        with patch("httpx.get", return_value=_mock_response(html, url=lulu_url)):
            result = _http_scrape(lulu_url)

        assert result.company == "Lululemon"
        assert result.job_title == "Staff Software Engineer"
        assert len(result.raw_text) > 300

    def test_falls_back_to_partial_when_scraperapi_fails(self):
        """If ScraperAPI call raises, fall through to whatever step 3 produced."""
        with (
            patch("httpx.get", side_effect=[
                _mock_response(TALEO_SHELL_HTML),
                Exception("ScraperAPI unreachable"),
            ]),
            patch.dict("os.environ", {"SCRAPER_API_KEY": "test-key"}),
        ):
            result = _http_scrape(HIRING_CAFE_URL)

        # Falls back to the shell result: title from og:title, no company
        assert result.job_title == "Staff Software Engineer"
        assert result.company is None
        assert result.source == "http_fallback"


# ── _company_from_hostname ────────────────────────────────────────────────────


class TestCompanyFromHostname:
    def test_careers_subdomain(self):
        assert _company_from_hostname("https://careers.lululemon.com/jobs/123") == "Lululemon"

    def test_jobs_subdomain(self):
        assert _company_from_hostname("https://jobs.example.com/position/42") == "Example"

    def test_hyphenated_slug(self):
        assert _company_from_hostname("https://careers.my-company.com/jobs/1") == "My Company"

    def test_returns_none_when_only_tld_and_prefix(self):
        # hiring.cafe → TLD="cafe" stripped → only "hiring" which is in SKIP → None
        assert _company_from_hostname("https://hiring.cafe/viewjob/abc") is None

    def test_does_not_use_tld_as_company(self):
        # www.jobs.io → skip TLD "io" → skip "jobs" → skip "www" → None
        assert _company_from_hostname("https://www.jobs.io/") is None

    def test_skips_multiple_prefixes(self):
        # www.careers.acme.com → skip www, skip careers → "Acme"
        assert _company_from_hostname("https://www.careers.acme.com/jobs/1") == "Acme"


# ── _extract_rendered_meta ────────────────────────────────────────────────────


class TestExtractRenderedMeta:
    def test_prefers_og_site_name_over_fallback(self):
        company, _ = _extract_rendered_meta(HIBOB_RENDERED_HTML, "fallback-co", None)
        assert company == "HiBob"

    def test_uses_fallback_company_when_og_site_name_absent(self):
        company, _ = _extract_rendered_meta(TALEO_RENDERED_HTML, "lululemon", None)
        assert company == "lululemon"

    def test_prefers_og_title_over_h1(self):
        _, title = _extract_rendered_meta(HIBOB_RENDERED_HTML, None, None)
        assert title == "Backend Engineer"

    def test_falls_back_to_h1_when_og_title_absent(self):
        html = "<html><body><h1>Staff Software Engineer</h1></body></html>"
        _, title = _extract_rendered_meta(html, None, "fallback-title")
        assert title == "Staff Software Engineer"

    def test_falls_back_to_fallback_title_when_nothing_found(self):
        _, title = _extract_rendered_meta("<html><body></body></html>", None, "fallback-title")
        assert title == "fallback-title"


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
