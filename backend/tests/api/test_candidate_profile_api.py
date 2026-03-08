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
