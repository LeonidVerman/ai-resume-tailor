"""Tests for the Greenhouse job scraper.

All tests are unit tests — no real network requests are made.
"""

from unittest.mock import MagicMock, patch

import pytest

from tailor.job.greenhouse import scrape_greenhouse, _board_and_id_from_embed
from tailor.job.scrape import _detect_site, scrape_job_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_api_response(company_name="Acme Corp", title="Staff Engineer", content="<p>Great job.</p>"):
    resp = MagicMock()
    resp.json.return_value = {
        "company_name": company_name,
        "title": title,
        "content": content,
    }
    resp.raise_for_status.return_value = None
    return resp


def _mock_page_response(board_token="acmecorp", status_code=200):
    html = f'<script src="https://boards.greenhouse.io/embed/job_board/js?for={board_token}"></script>'
    resp = MagicMock()
    resp.text = html
    resp.status_code = status_code
    return resp


# ---------------------------------------------------------------------------
# _detect_site
# ---------------------------------------------------------------------------

class TestDetectSite:
    def test_direct_greenhouse_url(self):
        url = "https://job-boards.greenhouse.io/acme/jobs/12345"
        assert _detect_site(url) == "greenhouse"

    def test_embedded_gh_jid_param(self):
        url = "https://www.somecompany.com/careers?gh_jid=5035377007"
        assert _detect_site(url) == "greenhouse"

    def test_gh_jid_with_other_params(self):
        url = "https://careers.example.com/jobs?dept=eng&gh_jid=999888"
        assert _detect_site(url) == "greenhouse"

    def test_non_greenhouse_url(self):
        url = "https://www.somecompany.com/careers"
        assert _detect_site(url) == "generic"

    def test_linkedin_not_greenhouse(self):
        url = "https://www.linkedin.com/jobs/view/12345"
        assert _detect_site(url) == "linkedin"


# ---------------------------------------------------------------------------
# _board_and_id_from_embed
# ---------------------------------------------------------------------------

class TestBoardAndIdFromEmbed:
    def test_extracts_board_and_job_id(self):
        url = "https://www.getfiber.ai/careers?gh_jid=5035377007"
        with patch("tailor.job.greenhouse.requests.get", return_value=_mock_page_response("clerkie")):
            board, job_id = _board_and_id_from_embed(url)
        assert board == "clerkie"
        assert job_id == "5035377007"

    def test_returns_none_when_no_gh_jid(self):
        url = "https://www.example.com/careers"
        board, job_id = _board_and_id_from_embed(url)
        assert board is None
        assert job_id is None

    def test_returns_none_when_no_embed_script(self):
        url = "https://www.example.com/careers?gh_jid=123"
        resp = MagicMock()
        resp.text = "<html><body>No greenhouse embed here.</body></html>"
        with patch("tailor.job.greenhouse.requests.get", return_value=resp):
            board, job_id = _board_and_id_from_embed(url)
        assert board is None
        assert job_id is None


# ---------------------------------------------------------------------------
# scrape_greenhouse — direct URL
# ---------------------------------------------------------------------------

class TestScrapeGreenhouseDirect:
    def test_direct_url_returns_job_data(self):
        url = "https://job-boards.greenhouse.io/life360/jobs/8438670002"
        with patch("tailor.job.greenhouse.requests.get", return_value=_mock_api_response(
            company_name="Life360", title="Staff Software Engineer", content="<p>Great role.</p>"
        )):
            result = scrape_greenhouse(url)
        assert result["company"] == "Life360"
        assert result["job_title"] == "Staff Software Engineer"
        assert "Great role." in result["description"]

    def test_html_content_is_stripped(self):
        url = "https://job-boards.greenhouse.io/acme/jobs/1"
        content = "<h2>About Us</h2><p>We build <strong>great</strong> software.</p>"
        with patch("tailor.job.greenhouse.requests.get", return_value=_mock_api_response(content=content)):
            result = scrape_greenhouse(url)
        assert "<" not in result["description"]
        assert "About Us" in result["description"]
        assert "great" in result["description"]

    def test_falls_back_to_board_token_when_no_company_name(self):
        url = "https://job-boards.greenhouse.io/myboard/jobs/42"
        resp = MagicMock()
        resp.json.return_value = {"company_name": None, "title": "Engineer", "content": ""}
        resp.raise_for_status.return_value = None
        with patch("tailor.job.greenhouse.requests.get", return_value=resp):
            result = scrape_greenhouse(url)
        assert result["company"] == "myboard"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Cannot parse Greenhouse URL"):
            scrape_greenhouse("https://www.example.com/jobs/123")


# ---------------------------------------------------------------------------
# scrape_greenhouse — embedded (gh_jid) URL
# ---------------------------------------------------------------------------

class TestScrapeGreenhouseEmbedded:
    def test_embedded_url_resolves_via_api(self):
        page_url = "https://www.getfiber.ai/careers?gh_jid=5035377007"

        page_resp = _mock_page_response("clerkie")
        api_resp = _mock_api_response("Clerkie", "Backend Engineer", "<p>Fintech startup.</p>")

        # First call = page fetch, second call = API fetch
        with patch("tailor.job.greenhouse.requests.get", side_effect=[page_resp, api_resp]):
            result = scrape_greenhouse(page_url)

        assert result["company"] == "Clerkie"
        assert result["job_title"] == "Backend Engineer"
        assert "Fintech startup." in result["description"]

    def test_embedded_url_no_embed_script_raises(self):
        page_url = "https://www.example.com/careers?gh_jid=999"
        resp = MagicMock()
        resp.text = "<html>No embed tag here.</html>"
        with patch("tailor.job.greenhouse.requests.get", return_value=resp):
            with pytest.raises(ValueError, match="Cannot parse Greenhouse URL"):
                scrape_greenhouse(page_url)


# ---------------------------------------------------------------------------
# scrape_job_url dispatcher integration
# ---------------------------------------------------------------------------

class TestScrapeJobUrlDispatch:
    def test_direct_greenhouse_dispatched(self):
        url = "https://job-boards.greenhouse.io/acme/jobs/1"
        with patch("tailor.job.greenhouse.requests.get", return_value=_mock_api_response("Acme", "SWE", "<p>desc</p>")):
            job = scrape_job_url(url)
        assert job.company == "Acme"
        assert job.job_title == "SWE"
        assert job.source_url == url

    def test_embedded_gh_jid_dispatched(self):
        url = "https://www.somecompany.com/careers?gh_jid=42"
        page_resp = _mock_page_response("somecoboard")
        api_resp = _mock_api_response("SomeCo", "Product Engineer", "<p>Build things.</p>")
        with patch("tailor.job.greenhouse.requests.get", side_effect=[page_resp, api_resp]):
            job = scrape_job_url(url)
        assert job.company == "SomeCo"
        assert job.job_title == "Product Engineer"
        assert job.source_url == url
