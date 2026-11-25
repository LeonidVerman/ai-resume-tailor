"""
backend/tests/api/test_guest_claim.py

Guest generation (issue #155) — Phase 4 claim/merge tests:
- successful claim moves resumes/JDs/runs/documents and sums credits
- profile moved only when the claimer has none
- second claim returns 409
- claiming a non-anonymous target is rejected
- claiming BY an anonymous user is rejected
- invalid guest token returns 401
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.db.models.candidate_profile import CandidateProfile
from backend.app.db.models.generation_run import GenerationRun
from backend.app.db.models.guest import FunnelEvent, GuestEntitlement
from backend.app.db.models.job_description import JobDescription
from backend.app.db.models.structured_resume import StructuredResume
from backend.app.db.models.tailored_document import TailoredDocument
from backend.app.db.models.user import User
from backend.app.dependencies import get_current_user, get_db_session
from backend.app.main import app

GUEST_UID = "aaaaaaaa-1111-2222-3333-444444444444"


class _FakeSupabase:
    """Mocked Supabase client: verify_token resolves the guest JWT."""

    def __init__(self, uid: str | None = None, fail: bool = False):
        self._uid = uid
        self._fail = fail

    def verify_token(self, token: str) -> dict:
        if self._fail:
            raise ValueError("Invalid or expired token")
        return {"id": self._uid, "email": None}


def _mock_supabase(monkeypatch, uid: str | None = GUEST_UID, fail: bool = False):
    import backend.app.clients.supabase_client as sc
    monkeypatch.setattr(
        sc, "make_supabase_client_from_settings",
        lambda: _FakeSupabase(uid=uid, fail=fail),
    )


def _make_guest(db, uid: str = GUEST_UID, used: int = 1) -> User:
    guest = User(
        id=uid,
        supabase_user_id=uid,
        email=f"guest-{uid}@guest.cvrocket.invalid",
        is_anonymous=True,
    )
    db.add(guest)
    db.flush()
    db.add(GuestEntitlement(user_id=uid, allowed=1, used=used))
    db.flush()
    return guest


def _seed_guest_content(db, guest: User):
    """One resume + JD + run + document + unreviewed profile for the guest."""
    resume = StructuredResume(user_id=guest.id, resume_jsonb={"name": "Guest"})
    db.add(resume)
    db.flush()
    jd = JobDescription(user_id=guest.id, raw_text="A job description")
    db.add(jd)
    db.flush()
    run = GenerationRun(
        user_id=guest.id,
        job_description_id=jd.id,
        run_type="single_pass",
        status="succeeded",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()
    doc = TailoredDocument(user_id=guest.id, generation_run_id=run.id)
    db.add(doc)
    db.flush()
    profile = CandidateProfile(
        user_id=guest.id,
        profile_jsonb={"full_name": "Guest"},
        onboarding_completed=True,
        is_unreviewed=True,
        source_resume_id=resume.id,
    )
    db.add(profile)
    db.flush()
    return resume, jd, run, doc, profile


def _claim(client: TestClient, token: str = "guest-jwt"):
    return client.post("/api/v1/guest/claim", json={"guest_access_token": token})


class TestGuestClaim:
    def test_claim_moves_content_and_sums_credits(
        self, client, db, user, monkeypatch
    ):
        guest = _make_guest(db, used=1)
        resume, jd, run, doc, profile = _seed_guest_content(db, guest)
        _mock_supabase(monkeypatch)

        assert user.free_generations_used == 0
        r = _claim(client)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["guest_user_id"] == guest.id
        assert data["resumes_moved"] == 1
        assert data["job_descriptions_moved"] == 1
        assert data["generation_runs_moved"] == 1
        assert data["documents_moved"] == 1
        assert data["profile_moved"] is True
        assert data["credits_added"] == 1

        # Rows now belong to the claimer
        assert db.get(StructuredResume, resume.id).user_id == user.id
        assert db.get(JobDescription, jd.id).user_id == user.id
        assert db.get(GenerationRun, run.id).user_id == user.id
        assert db.get(TailoredDocument, doc.id).user_id == user.id
        assert db.get(CandidateProfile, profile.id).user_id == user.id

        # Credits: guest used=1 → claimer free_generations_used += 1
        assert user.free_generations_used == 1

        # Guest row deactivated + marked claimed
        assert guest.guest_claimed_by == user.id
        assert guest.is_active is False

        # Funnel event recorded for the claimer
        ev = db.execute(
            select(FunnelEvent).where(FunnelEvent.event_type == "guest_claimed")
        ).scalars().first()
        assert ev is not None and ev.user_id == user.id
        assert ev.meta == {"guest_user_id": guest.id}

    def test_profile_stays_when_claimer_has_one(
        self, client, db, user, monkeypatch
    ):
        # Claimer already has a profile
        db.add(CandidateProfile(
            user_id=user.id,
            profile_jsonb={"full_name": "Registered"},
            onboarding_completed=True,
        ))
        db.flush()
        guest = _make_guest(db)
        *_, profile = _seed_guest_content(db, guest)
        _mock_supabase(monkeypatch)

        r = _claim(client)
        assert r.status_code == 200, r.text
        assert r.json()["profile_moved"] is False
        # Guest profile left with the guest for purging
        assert db.get(CandidateProfile, profile.id).user_id == guest.id

    def test_second_claim_conflicts(self, client, db, user, monkeypatch):
        guest = _make_guest(db)
        _seed_guest_content(db, guest)
        _mock_supabase(monkeypatch)

        assert _claim(client).status_code == 200
        r2 = _claim(client)
        assert r2.status_code == 409

    def test_non_anonymous_target_rejected(self, client, db, monkeypatch):
        target_uid = "bbbbbbbb-1111-2222-3333-444444444444"
        target = User(
            id=target_uid,
            supabase_user_id=target_uid,
            email="registered-target@example.com",
            is_anonymous=False,
        )
        db.add(target)
        db.flush()
        _mock_supabase(monkeypatch, uid=target_uid)

        r = _claim(client)
        assert r.status_code == 400

    def test_claim_by_anonymous_user_rejected(self, db, monkeypatch):
        claimer_uid = "cccccccc-1111-2222-3333-444444444444"
        anon_claimer = User(
            id=claimer_uid,
            supabase_user_id=claimer_uid,
            email=f"guest-{claimer_uid}@guest.cvrocket.invalid",
            is_anonymous=True,
        )
        db.add(anon_claimer)
        db.flush()
        guest = _make_guest(db)
        _mock_supabase(monkeypatch)

        def _override_db():
            yield db

        app.dependency_overrides[get_db_session] = _override_db
        app.dependency_overrides[get_current_user] = lambda: anon_claimer
        try:
            with TestClient(app) as c:
                r = _claim(c)
            assert r.status_code == 403
        finally:
            app.dependency_overrides.clear()
        # Guest untouched
        assert guest.guest_claimed_by is None

    def test_invalid_guest_token_rejected(self, client, db, monkeypatch):
        _make_guest(db)
        _mock_supabase(monkeypatch, fail=True)
        r = _claim(client)
        assert r.status_code == 401

    def test_unknown_guest_returns_404(self, client, db, monkeypatch):
        _mock_supabase(monkeypatch, uid="dddddddd-1111-2222-3333-444444444444")
        r = _claim(client)
        assert r.status_code == 404
