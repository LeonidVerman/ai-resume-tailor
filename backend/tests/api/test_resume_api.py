"""
backend/tests/api/test_resume_api.py

API tests for POST /resumes/upload, GET /resumes, GET /resumes/{id}.

Resume upload accepts a DOCX file; in tests we send minimal text-based bytes
because the parser falls back to UTF-8 decode for unknown extensions.
"""

import io
import uuid

import pytest

API = "/api/v1/resumes"

FAKE_RESUME_TEXT = b"Jane Doe\njane@example.com\nSummary\nExperienced engineer."


def _upload_file(client, content: bytes = FAKE_RESUME_TEXT, filename: str = "resume.txt"):
    return client.post(
        f"{API}/upload",
        files={"file": (filename, io.BytesIO(content), "text/plain")},
    )


class TestResumeUpload:
    def test_upload_returns_201(self, client):
        resp = _upload_file(client)
        assert resp.status_code == 201

    def test_upload_response_has_id(self, client):
        resp = _upload_file(client)
        data = resp.json()
        assert "id" in data
        assert "resume" in data

    def test_upload_name_parsed(self, client):
        resp = _upload_file(client)
        data = resp.json()
        # Name parsed from first line
        assert data["resume"]["name"] == "Jane Doe"

    def test_upload_user_id_matches(self, client, user):
        resp = _upload_file(client)
        assert resp.json()["user_id"] == user.id

    def test_upload_file_too_large_returns_413(self, client):
        big = b"A" * (11 * 1024 * 1024)
        resp = _upload_file(client, content=big)
        assert resp.status_code == 413


class TestListResumes:
    def test_empty_list(self, client):
        resp = client.get(API)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_after_upload(self, client):
        _upload_file(client)
        resp = client.get(API)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_list_multiple_uploads(self, client):
        _upload_file(client, filename="r1.txt")
        _upload_file(client, filename="r2.txt")
        resp = client.get(API)
        assert len(resp.json()) == 2

    def test_list_summary_fields(self, client):
        _upload_file(client)
        item = client.get(API).json()[0]
        assert "id" in item
        assert "name" in item
        assert "created_at" in item


class TestGetResume:
    def test_get_by_id_returns_200(self, client):
        upload_resp = _upload_file(client)
        rid = upload_resp.json()["id"]
        resp = client.get(f"{API}/{rid}")
        assert resp.status_code == 200

    def test_get_unknown_id_returns_404(self, client):
        resp = client.get(f"{API}/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_returns_resume_doc(self, client):
        rid = _upload_file(client).json()["id"]
        data = client.get(f"{API}/{rid}").json()
        assert "resume" in data
        assert data["resume"]["name"] == "Jane Doe"
