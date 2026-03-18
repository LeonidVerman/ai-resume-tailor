"""
backend/tests/integration/test_generation_flow.py

Happy-path integration test for the full generation flow:
  1. Upload a resume
  2. Submit a job description
  3. Trigger generation (LLM pipeline mocked)
  4. Retrieve the generation run and tailored document
  5. Verify all records are consistent

This test exercises the full API stack with a real SQLite session —
only the LLM call is replaced by a mock.
"""

import io
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

RESUME_API = "/api/v1/resumes"
JD_API = "/api/v1/job-descriptions"
GEN_API = "/api/v1/generations"
DOC_API = "/api/v1/documents"

FAKE_RESUME = (
    "Alice Johnson\n"
    "alice@example.com\n"
    "\n"
    "Summary\n"
    "Experienced full-stack engineer with 8 years building data pipelines.\n"
    "\n"
    "Experience\n"
    "Staff Engineer at Startup Inc (2021-present)\n"
    "- Led backend re-architecture from monolith to microservices\n"
).encode("utf-8")

JD_TEXT = """\
Senior Software Engineer — FinTech Co
We are looking for a senior engineer with distributed systems experience.
Requirements: Python, Kafka, PostgreSQL, 5+ years experience.
"""


def _make_tailor_result(resume: str = "Tailored resume", cover: str = "Cover letter"):
    r = MagicMock()
    r.resume = resume
    r.cover_letter = cover
    return r


def _patch_pipeline(resume="Tailored resume text", cover="Cover letter text"):
    return patch(
        "backend.app.services.generation_service.GenerationService._run_pipeline",
        return_value=(_make_tailor_result(resume, cover), 500, 1000, 0.02, {}),
    )


def _patch_model():
    return patch("tailor.config.SIMPLE_MODEL", "gpt-4o-mini")


class TestFullGenerationFlow:
    def test_happy_path(self, client):
        # Step 1 — upload resume
        resume_resp = client.post(
            f"{RESUME_API}/upload",
            files={"file": ("resume.txt", io.BytesIO(FAKE_RESUME), "text/plain")},
        )
        assert resume_resp.status_code == 201, resume_resp.text
        resume_id = resume_resp.json()["id"]

        # Step 2 — submit job description
        jd_resp = client.post(f"{JD_API}/manual", json={"raw_text": JD_TEXT})
        assert jd_resp.status_code == 201, jd_resp.text
        jd_id = jd_resp.json()["id"]

        # Step 3 — trigger generation
        gen_body = {
            "job_description_id": jd_id,
            "structured_resume_id": resume_id,
            "options": {"mode": "two_phase"},
        }
        with _patch_pipeline(), _patch_model():
            gen_resp = client.post(GEN_API, json=gen_body)
        assert gen_resp.status_code == 201, gen_resp.text
        gen_data = gen_resp.json()
        run_id = gen_data["run_id"]
        doc_id = gen_data["tailored_document_id"]

        assert gen_data["status"] == "succeeded"
        assert run_id
        assert doc_id

        # Step 4 — retrieve generation run detail
        run_detail = client.get(f"{GEN_API}/{run_id}").json()
        assert run_detail["id"] == run_id
        assert run_detail["status"] == "succeeded"
        assert run_detail["job_description_id"] == jd_id

        # Step 5 — retrieve document
        doc_resp = client.get(f"{DOC_API}/{doc_id}")
        assert doc_resp.status_code == 200, doc_resp.text
        doc_data = doc_resp.json()
        assert doc_data["id"] == doc_id
        assert doc_data["generation_run_id"] == run_id

    def test_generation_run_appears_in_list(self, client):
        resume_id = client.post(
            f"{RESUME_API}/upload",
            files={"file": ("r.txt", io.BytesIO(FAKE_RESUME), "text/plain")},
        ).json()["id"]
        jd_id = client.post(f"{JD_API}/manual", json={"raw_text": JD_TEXT}).json()["id"]

        with _patch_pipeline(), _patch_model():
            client.post(
                GEN_API,
                json={
                    "job_description_id": jd_id,
                    "structured_resume_id": resume_id,
                },
            )

        runs = client.get(GEN_API).json()
        assert len(runs) >= 1
        assert runs[0]["status"] == "succeeded"

    def test_resume_list_contains_uploaded(self, client):
        client.post(
            f"{RESUME_API}/upload",
            files={"file": ("r.txt", io.BytesIO(FAKE_RESUME), "text/plain")},
        )
        resumes = client.get(RESUME_API).json()
        assert len(resumes) == 1
        assert resumes[0]["name"] == "Alice Johnson"

    def test_jd_list_contains_submitted(self, client):
        client.post(f"{JD_API}/manual", json={"raw_text": JD_TEXT})
        jds = client.get(JD_API).json()
        assert len(jds) == 1
