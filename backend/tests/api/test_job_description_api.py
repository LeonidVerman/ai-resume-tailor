"""
backend/tests/api/test_job_description_api.py

API tests for job description endpoints.
The scrape endpoint is tested with a mocked JobScraperService so we don't
need Playwright or a live network connection.
"""

from unittest.mock import MagicMock, patch

import pytest

from backend.app.services.job_scraper_service import JobScrapedData

API = "/api/v1/job-descriptions"

MANUAL_BODY = {
    "raw_text": "We are looking for a Senior Software Engineer to join our team. "
                "Requirements: 5+ years Python, distributed systems experience."
}

SCRAPE_BODY = {"url": "https://example.com/jobs/123"}


def _scraped_job_data():
    return JobScrapedData(
        url="https://example.com/jobs/123",
        company="Acme Corp",
        job_title="Senior Engineer",
        raw_text="Looking for engineers.",
        source="generator",
    )


class TestManualJobDescription:
    def test_manual_create_returns_201(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.status_code == 201

    def test_manual_response_has_id(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        data = resp.json()
        assert "id" in data

    def test_manual_raw_text_stored(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.json()["raw_text"] == MANUAL_BODY["raw_text"]

    def test_manual_user_id_matches(self, client, user):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.json()["user_id"] == user.id

    def test_manual_missing_text_returns_422(self, client):
        resp = client.post(f"{API}/manual", json={})
        assert resp.status_code == 422

    def test_manual_source_type_is_manual(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.json()["source_type"] == "manual"

    def test_manual_with_company_and_title(self, client):
        body = {
            "raw_text": "Job description text here.",
            "company": "Acme Corp",
            "job_title": "Senior Engineer",
        }
        resp = client.post(f"{API}/manual", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["metadata"]["company"] == "Acme Corp"
        assert data["metadata"]["job_title"] == "Senior Engineer"

    def test_manual_company_only(self, client):
        body = {"raw_text": "Some job text.", "company": "Beta Inc"}
        resp = client.post(f"{API}/manual", json=body)
        assert resp.status_code == 201
        assert resp.json()["metadata"]["company"] == "Beta Inc"

    def test_manual_no_metadata_when_empty(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.json()["metadata"] is None

    def test_manual_created_at_present(self, client):
        resp = client.post(f"{API}/manual", json=MANUAL_BODY)
        assert resp.json()["created_at"] is not None

    def test_manual_raw_text_retrievable_via_get(self, client):
        """Verify raw_text is persisted and retrievable — needed for LLM generation."""
        jd_id = client.post(f"{API}/manual", json=MANUAL_BODY).json()["id"]
        resp = client.get(f"{API}/{jd_id}")
        assert resp.json()["raw_text"] == MANUAL_BODY["raw_text"]

    def test_manual_metadata_retrievable_via_get(self, client):
        """Verify metadata (company, job_title) is persisted and retrievable."""
        body = {
            "raw_text": "Engineer role.",
            "company": "TestCo",
            "job_title": "Staff Engineer",
        }
        jd_id = client.post(f"{API}/manual", json=body).json()["id"]
        data = client.get(f"{API}/{jd_id}").json()
        assert data["metadata"]["company"] == "TestCo"
        assert data["metadata"]["job_title"] == "Staff Engineer"


class TestScrapeJobDescription:
    def test_scrape_success_returns_201(self, client):
        with patch(
            "backend.app.api.job_description.JobScraperService.scrape",
            return_value=_scraped_job_data(),
        ):
            resp = client.post(f"{API}/scrape", json=SCRAPE_BODY)
        assert resp.status_code == 201

    def test_scrape_stores_source_url(self, client):
        with patch(
            "backend.app.api.job_description.JobScraperService.scrape",
            return_value=_scraped_job_data(),
        ):
            resp = client.post(f"{API}/scrape", json=SCRAPE_BODY)
        assert resp.json()["source_url"] == "https://example.com/jobs/123"

    def test_scrape_failure_returns_503(self, client):
        with patch(
            "backend.app.api.job_description.JobScraperService.scrape",
            side_effect=RuntimeError("Playwright not available"),
        ):
            resp = client.post(f"{API}/scrape", json=SCRAPE_BODY)
        assert resp.status_code == 503


class TestListJobDescriptions:
    def test_empty_list(self, client):
        resp = client.get(API)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_create(self, client):
        client.post(f"{API}/manual", json=MANUAL_BODY)
        resp = client.get(API)
        assert len(resp.json()) == 1

    def test_list_summary_fields(self, client):
        client.post(f"{API}/manual", json=MANUAL_BODY)
        item = client.get(API).json()[0]
        assert "id" in item
        assert "created_at" in item


class TestGetJobDescription:
    def test_get_by_id_returns_200(self, client):
        jd_id = client.post(f"{API}/manual", json=MANUAL_BODY).json()["id"]
        resp = client.get(f"{API}/{jd_id}")
        assert resp.status_code == 200

    def test_get_unknown_returns_404(self, client):
        resp = client.get(f"{API}/99999")
        assert resp.status_code == 404


class TestDeleteJobDescription:
    def test_delete_returns_204(self, client):
        jd_id = client.post(f"{API}/manual", json=MANUAL_BODY).json()["id"]
        resp = client.delete(f"{API}/{jd_id}")
        assert resp.status_code == 204

    def test_delete_removes_from_list(self, client):
        jd_id = client.post(f"{API}/manual", json=MANUAL_BODY).json()["id"]
        client.delete(f"{API}/{jd_id}")
        remaining = client.get(API).json()
        assert all(j["id"] != jd_id for j in remaining)

    def test_delete_unknown_returns_404(self, client):
        resp = client.delete(f"{API}/99999")
        assert resp.status_code == 404
