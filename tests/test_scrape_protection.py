"""Tests for protected-site scrape-failure helpers.

All tests are unit tests — no real network requests or file I/O are made.
The config is patched in-memory so tests are hermetic.
"""

from unittest.mock import patch, MagicMock

import pytest

from tailor.job.scrape import (
    _normalize_hostname,
    is_scrape_protected_host,
    get_scrape_failure_message,
)


# ---------------------------------------------------------------------------
# Helpers to patch the config loader
# ---------------------------------------------------------------------------

_FAKE_SITES = [
    {"domain": "wellfound.com", "label": "Wellfound"},
    {"domain": "example-protected.io", "label": "ExampleProtected"},
]


def _patch_sites(sites=_FAKE_SITES):
    """Return a context-manager that replaces _load_protected_sites."""
    mock_fn = MagicMock(return_value=sites)
    return patch("tailor.job.scrape._load_protected_sites", mock_fn)


# ---------------------------------------------------------------------------
# _normalize_hostname
# ---------------------------------------------------------------------------

class TestNormalizeHostname:
    def test_strips_www(self):
        assert _normalize_hostname("https://www.wellfound.com/jobs") == "wellfound.com"

    def test_no_www(self):
        assert _normalize_hostname("https://wellfound.com/jobs") == "wellfound.com"

    def test_lowercase(self):
        assert _normalize_hostname("https://WellFound.COM/jobs") == "wellfound.com"

    def test_subdomain_preserved(self):
        assert _normalize_hostname("https://boards.greenhouse.io/acme/jobs/1") == "boards.greenhouse.io"


# ---------------------------------------------------------------------------
# is_scrape_protected_host
# ---------------------------------------------------------------------------

class TestIsScrapeProtectedHost:
    def test_exact_domain_match(self):
        with _patch_sites():
            assert is_scrape_protected_host("wellfound.com") is True

    def test_www_subdomain_match(self):
        with _patch_sites():
            assert is_scrape_protected_host("www.wellfound.com") is True

    def test_other_subdomain_match(self):
        with _patch_sites():
            assert is_scrape_protected_host("jobs.wellfound.com") is True

    def test_case_insensitive(self):
        with _patch_sites():
            assert is_scrape_protected_host("WellFound.COM") is True

    def test_non_protected_host(self):
        with _patch_sites():
            assert is_scrape_protected_host("greenhouse.io") is False

    def test_partial_suffix_does_not_match(self):
        # "notwellfound.com" should NOT match "wellfound.com"
        with _patch_sites():
            assert is_scrape_protected_host("notwellfound.com") is False

    def test_second_configured_site(self):
        with _patch_sites():
            assert is_scrape_protected_host("example-protected.io") is True

    def test_empty_list(self):
        with _patch_sites([]):
            assert is_scrape_protected_host("wellfound.com") is False


# ---------------------------------------------------------------------------
# get_scrape_failure_message
# ---------------------------------------------------------------------------

class TestGetScrapeFailureMessage:
    def test_protected_site_returns_label_message(self):
        with _patch_sites():
            msg = get_scrape_failure_message("https://wellfound.com/jobs?job_listing_slug=123")
        assert "Wellfound" in msg
        assert "bot protection" in msg
        assert "copy and paste" in msg

    def test_protected_site_www_subdomain(self):
        with _patch_sites():
            msg = get_scrape_failure_message("https://www.wellfound.com/jobs/456")
        assert "Wellfound" in msg
        assert "bot protection" in msg

    def test_generic_site_returns_generic_message(self):
        with _patch_sites():
            msg = get_scrape_failure_message("https://somecompany.com/careers/123")
        assert "Wellfound" not in msg
        assert "Scraping the job description from the URL failed" in msg
        assert "copy and paste" in msg

    def test_protected_message_does_not_contain_generic_prefix(self):
        with _patch_sites():
            msg = get_scrape_failure_message("https://wellfound.com/jobs/1")
        assert "Scraping the job description from the URL failed" not in msg

    def test_second_protected_site_uses_its_label(self):
        with _patch_sites():
            msg = get_scrape_failure_message("https://example-protected.io/jobs/1")
        assert "ExampleProtected" in msg
        assert "bot protection" in msg
