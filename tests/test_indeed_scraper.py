"""Tests for the Indeed job scraper.

Unit tests run offline with no network access.
Integration tests are marked @pytest.mark.scraping and excluded from CI
(pytest -m 'not scraping').  Run them locally to verify live behaviour.
"""

from unittest.mock import MagicMock, patch

import pytest

from tailor.job.indeed import _clean_indeed_url, _extract_job_data, scrape_indeed
from tailor.job.scrape import _detect_site


# ---------------------------------------------------------------------------
# _clean_indeed_url
# ---------------------------------------------------------------------------

class TestCleanIndeedUrl:
    def test_basic_viewjob_url(self):
        clean, base = _clean_indeed_url("https://ca.indeed.com/viewjob?jk=abc123")
        assert clean == "https://ca.indeed.com/viewjob?jk=abc123"
        assert base == "https://ca.indeed.com/"

    def test_strips_tracking_params(self):
        clean, _ = _clean_indeed_url(
            "https://ca.indeed.com/viewjob?jk=abc123&from=shareddesktop_copy&tk=tracker"
        )
        assert clean == "https://ca.indeed.com/viewjob?jk=abc123"
        assert "from=" not in clean
        assert "tk=" not in clean

    def test_preserves_regional_hostname(self):
        clean, base = _clean_indeed_url("https://uk.indeed.com/viewjob?jk=xyz")
        assert "uk.indeed.com" in clean
        assert base == "https://uk.indeed.com/"

    def test_www_indeed(self):
        clean, base = _clean_indeed_url("https://www.indeed.com/viewjob?jk=test456")
        assert clean == "https://www.indeed.com/viewjob?jk=test456"
        assert base == "https://www.indeed.com/"

    def test_raises_without_jk(self):
        with pytest.raises(ValueError, match="'jk'"):
            _clean_indeed_url("https://ca.indeed.com/jobs?q=engineer")

    def test_raises_on_non_indeed_url(self):
        with pytest.raises(ValueError):
            _clean_indeed_url("https://www.linkedin.com/jobs/view/12345")


# ---------------------------------------------------------------------------
# _detect_site
# ---------------------------------------------------------------------------

class TestDetectSiteIndeed:
    def test_ca_indeed(self):
        assert _detect_site("https://ca.indeed.com/viewjob?jk=abc") == "indeed"

    def test_www_indeed(self):
        assert _detect_site("https://www.indeed.com/viewjob?jk=abc") == "indeed"

    def test_uk_indeed(self):
        assert _detect_site("https://uk.indeed.com/viewjob?jk=abc") == "indeed"


# ---------------------------------------------------------------------------
# _extract_job_data — unit tests with static HTML
# ---------------------------------------------------------------------------

_JSON_LD_HTML = """
<html><head></head><body>
<script type="application/ld+json">
{
  "@type": "JobPosting",
  "title": "Senior Backend Engineer",
  "hiringOrganization": {"name": "Acme Corp"},
  "description": "<p>We are looking for a <strong>backend engineer</strong>.</p>"
}
</script>
</body></html>
"""

_DATA_ATTR_HTML = """
<html><body>
<h1 data-testid="jobsearch-JobInfoHeader-title">Staff Engineer</h1>
<span data-testid="inlineHeader-companyName">Globex Inc</span>
<div id="jobDescriptionText">
  <p>Join our team and build scalable systems.</p>
</div>
</body></html>
"""

_OG_META_HTML = """
<html><head>
<meta property="og:title" content="Product Manager at Initech">
<meta property="og:description" content="We need a great PM.">
</head><body></body></html>
"""


class TestExtractJobData:
    def test_json_ld_strategy(self):
        result = _extract_job_data(_JSON_LD_HTML, "https://ca.indeed.com/viewjob?jk=x")
        assert result is not None
        assert result["company"] == "Acme Corp"
        assert result["job_title"] == "Senior Backend Engineer"
        assert "backend engineer" in result["description"]

    def test_data_attr_strategy(self):
        result = _extract_job_data(_DATA_ATTR_HTML, "https://ca.indeed.com/viewjob?jk=x")
        assert result is not None
        assert result["company"] == "Globex Inc"
        assert result["job_title"] == "Staff Engineer"
        assert "scalable systems" in result["description"]

    def test_og_meta_fallback(self):
        result = _extract_job_data(_OG_META_HTML, "https://ca.indeed.com/viewjob?jk=x")
        assert result is not None
        assert "Product Manager" in result["job_title"]
        assert result["description"] == "We need a great PM."

    def test_empty_html_returns_none(self):
        assert _extract_job_data("<html><body></body></html>", "http://x") is None

    def test_json_ld_list_format(self):
        """JSON-LD can be a list; the scraper must find the JobPosting entry."""
        html = """<script type="application/ld+json">
        [{"@type": "WebPage"}, {"@type": "JobPosting", "title": "Dev",
          "hiringOrganization": {"name": "Co"}, "description": "Do stuff."}]
        </script>"""
        result = _extract_job_data(html, "http://x")
        assert result is not None
        assert result["job_title"] == "Dev"


# ---------------------------------------------------------------------------
# scrape_indeed — unit test with mocked session
# ---------------------------------------------------------------------------

class TestScrapeIndeedUnit:
    def _make_session_mock(self, html: str):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html
        mock_resp.raise_for_status.return_value = None

        mock_session = MagicMock()
        mock_session.get.return_value = mock_resp
        return mock_session

    def test_returns_extracted_data(self):
        mock_session = self._make_session_mock(_JSON_LD_HTML)

        with patch("curl_cffi.requests.Session", return_value=mock_session):
            result = scrape_indeed("https://ca.indeed.com/viewjob?jk=abc123")

        assert result["company"] == "Acme Corp"
        assert result["job_title"] == "Senior Backend Engineer"
        # Homepage + search page + job page = 3 calls
        assert mock_session.get.call_count == 3

    def test_three_step_priming_order(self):
        """Verify session primes homepage then search page then job page."""
        mock_session = self._make_session_mock(_JSON_LD_HTML)

        with patch("curl_cffi.requests.Session", return_value=mock_session):
            scrape_indeed("https://ca.indeed.com/viewjob?jk=abc123")

        calls = [c.args[0] for c in mock_session.get.call_args_list]
        assert calls[0] == "https://ca.indeed.com/"          # homepage
        assert "jobs?q=" in calls[1]                          # search page
        assert calls[2] == "https://ca.indeed.com/viewjob?jk=abc123"  # job page

    def test_raises_when_no_data_extracted(self):
        mock_session = self._make_session_mock("<html><body>Nothing here</body></html>")

        with patch("curl_cffi.requests.Session", return_value=mock_session):
            with pytest.raises(ValueError, match="Could not extract"):
                scrape_indeed("https://ca.indeed.com/viewjob?jk=abc123")


# ---------------------------------------------------------------------------
# Integration test — live network, excluded from CI
# ---------------------------------------------------------------------------

@pytest.mark.scraping
class TestScrapeIndeedIntegration:
    """Live network tests.  Run locally with: pytest -m scraping tests/test_indeed_scraper.py"""

    def test_scrape_ca_indeed_job(self):
        result = scrape_indeed(
            "https://ca.indeed.com/viewjob?jk=1f5bfb2ae23d976b&from=shareddesktop_copy"
        )
        assert isinstance(result, dict)
        assert result.get("company", "Unknown") != ""
        assert result.get("job_title", "Unknown") != ""
        assert len(result.get("description", "")) > 100
