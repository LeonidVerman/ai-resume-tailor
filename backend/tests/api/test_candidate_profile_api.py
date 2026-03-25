"""
backend/tests/api/test_candidate_profile_api.py

API tests for GET/POST/PUT /api/v1/candidate-profile.
"""

import pytest

API = "/api/v1/candidate-profile"

MINIMAL_PROFILE_BODY = {
    "profile": {
        "candidate": {"name": "Alice Smith"},
    },
    "profile_version": "1",
}

FULL_PROFILE_BODY = {
    "profile": {
        "candidate_profile_version": "1.0",
        "candidate": {
            "name": "Alice Smith",
            "headline": "Senior Engineer",
            "summary": "Experienced distributed systems engineer.",
        },
        "domains": {"primary": ["fintech"], "secondary": ["healthtech"]},
        "role_fit_themes": ["distributed systems", "leadership"],
    },
    "profile_version": "1",
}


class TestGetCandidateProfile:
    def test_no_profile_returns_404(self, client):
        resp = client.get(API)
        assert resp.status_code == 404

    def test_profile_returned_after_create(self, client):
        client.post(API, json=MINIMAL_PROFILE_BODY)
        resp = client.get(API)
        assert resp.status_code == 200
        data = resp.json()
        assert data["profile"]["candidate"]["name"] == "Alice Smith"


class TestCreateCandidateProfile:
    def test_minimal_create_returns_201(self, client):
        resp = client.post(API, json=MINIMAL_PROFILE_BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert "id" in data
        assert data["profile"]["candidate"]["name"] == "Alice Smith"

    def test_full_profile_stored(self, client):
        resp = client.post(API, json=FULL_PROFILE_BODY)
        assert resp.status_code == 201
        data = resp.json()
        assert data["profile"]["candidate"]["headline"] == "Senior Engineer"
        assert "fintech" in data["profile"]["domains"]["primary"]

    def test_missing_candidate_name_returns_422(self, client):
        body = {"profile": {"candidate": {}}, "profile_version": "1"}
        resp = client.post(API, json=body)
        assert resp.status_code == 422

    def test_missing_profile_key_returns_422(self, client):
        resp = client.post(API, json={"profile_version": "1"})
        assert resp.status_code == 422


class TestUpdateCandidateProfile:
    def test_put_creates_if_missing(self, client):
        resp = client.put(API, json=FULL_PROFILE_BODY)
        assert resp.status_code == 200
        assert resp.json()["profile"]["candidate"]["name"] == "Alice Smith"

    def test_put_updates_existing(self, client):
        client.post(API, json=MINIMAL_PROFILE_BODY)
        updated = {
            "profile": {"candidate": {"name": "Alice Updated"}},
            "profile_version": "1",
        }
        resp = client.put(API, json=updated)
        assert resp.status_code == 200
        assert resp.json()["profile"]["candidate"]["name"] == "Alice Updated"

    def test_user_id_matches(self, client, user):
        resp = client.put(API, json=MINIMAL_PROFILE_BODY)
        assert resp.json()["user_id"] == user.id

    def test_onboarding_completed_false_on_create(self, client):
        resp = client.put(API, json=MINIMAL_PROFILE_BODY)
        assert resp.json()["onboarding_completed"] is False


class TestCompleteOnboarding:
    COMPLETE_API = "/api/v1/candidate-profile/complete-onboarding"

    def test_404_when_no_profile(self, client):
        resp = client.post(self.COMPLETE_API)
        assert resp.status_code == 404

    def test_marks_onboarding_completed(self, client):
        client.put(API, json=MINIMAL_PROFILE_BODY)
        resp = client.post(self.COMPLETE_API)
        assert resp.status_code == 200
        assert resp.json()["onboarding_completed"] is True

    def test_complete_onboarding_sets_prompt_synched_false(self, client, db):
        """complete-onboarding must invalidate the cached candidate prompt."""
        client.put(API, json=MINIMAL_PROFILE_BODY)
        # Manually mark prompt_synched=True so we can verify it is reset.
        from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
        from sqlalchemy import text
        row = db.execute(text("SELECT id FROM candidate_profiles LIMIT 1")).fetchone()
        if row:
            db.execute(
                text("UPDATE candidate_profiles SET prompt_synched = 1 WHERE id = :id"),
                {"id": row[0]},
            )
            db.flush()

        resp = client.post(self.COMPLETE_API)
        assert resp.status_code == 200
        assert resp.json()["onboarding_completed"] is True
        # Verify prompt_synched was reset via the repository.
        if row:
            profile = CandidateProfileRepository(db).get_by_id(row[0])
            assert profile is not None
            assert profile.prompt_synched is False

    def test_frontend_skill_category_roundtrip(self, client):
        body = {
            "profile": {
                "candidate": {"name": "Alice"},
                "technical_skills": {"frontend": ["React", "Next.js"]},
            },
            "profile_version": "1",
        }
        client.put(API, json=body)
        resp = client.get(API)
        assert resp.status_code == 200
        assert "React" in resp.json()["profile"]["technical_skills"]["frontend"]


class TestGenerationOnboardingGate:
    """Generation endpoint returns 403 when onboarding is not complete."""

    def test_generate_blocked_without_onboarding(self, client):
        # No profile at all → 403
        resp = client.post("/api/v1/generations", json={
            "job_description_id": "00000000-0000-0000-0000-000000000001",
            "structured_resume_id": "00000000-0000-0000-0000-000000000002",
        })
        assert resp.status_code == 403
        assert "onboarding" in resp.json()["detail"].lower()

    def test_generate_blocked_with_incomplete_onboarding(self, client):
        # Profile exists but onboarding_completed=False → 403
        client.put(API, json=MINIMAL_PROFILE_BODY)
        resp = client.post("/api/v1/generations", json={
            "job_description_id": "00000000-0000-0000-0000-000000000001",
            "structured_resume_id": "00000000-0000-0000-0000-000000000002",
        })
        assert resp.status_code == 403
