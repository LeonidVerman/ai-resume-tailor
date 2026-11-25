"""
backend/tests/api/test_guest_cleanup.py

Guest generation (issue #155) — Phase 5 retention & metrics tests:
- stale reservation released while a fresh one is kept
- retention purge deletes content rows + entitlement, keeps the tombstoned
  users row (guest_purged_at set), skips claimed/fresh guests
- DELETE /guest/data works for a guest and 403s for regular users
- GET /admin/guest-metrics shape + admin-only
- PUT /admin/generation-config accepts the guest_* fields
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.config import get_settings
from backend.app.db.models.admin_config import AdminConfig
from backend.app.db.models.candidate_profile import CandidateProfile
from backend.app.db.models.generation_run import GenerationRun
from backend.app.db.models.guest import (
    FunnelEvent,
    GuestAbuseEvent,
    GuestEntitlement,
)
from backend.app.db.models.job_description import JobDescription
from backend.app.db.models.structured_resume import StructuredResume
from backend.app.db.models.tailored_document import TailoredDocument
from backend.app.db.models.user import User
from backend.app.dependencies import get_current_user, get_db_session
from backend.app.main import app
from backend.app.services.guest_cleanup_service import GuestCleanupService


class _FakeStorage:
    """StorageService stand-in that records deleted keys."""

    def __init__(self):
        self.deleted: list[str] = []

    def delete_run_objects(self, keys):
        self.deleted.extend(keys)


def _svc(db, storage=None) -> GuestCleanupService:
    return GuestCleanupService(db, get_settings(), storage=storage or _FakeStorage())


def _make_guest(db, uid: str, created_at: datetime | None = None) -> User:
    guest = User(
        id=uid,
        supabase_user_id=uid,
        email=f"guest-{uid}@guest.cvrocket.invalid",
        is_anonymous=True,
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(guest)
    db.flush()
    db.add(GuestEntitlement(user_id=uid, allowed=1))
    db.flush()
    return guest


def _seed_guest_content(db, guest: User):
    """One resume + JD + run + document + unreviewed profile for the guest."""
    resume = StructuredResume(
        user_id=guest.id,
        resume_jsonb={"name": "Guest"},
        source_file_url=f"documents/{guest.id}/templates/1.docx",
    )
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
    doc = TailoredDocument(
        user_id=guest.id,
        generation_run_id=run.id,
        resume_pdf_url=f"documents/{guest.id}/generated/{run.id}/resume.pdf",
        cover_letter_pdf_url=f"documents/{guest.id}/generated/{run.id}/cover_letter.pdf",
    )
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


# ── Stale reservations ──────────────────────────────────────────────────────


class TestStaleReservations:
    def test_stale_released_fresh_kept(self, db):
        now = datetime.now(timezone.utc)
        stale = _make_guest(db, "aaaa1111-0000-0000-0000-000000000001")
        fresh = _make_guest(db, "aaaa1111-0000-0000-0000-000000000002")

        ent_stale = db.get(GuestEntitlement, stale.id)
        ent_stale.reserved = 1
        ent_stale.reserved_at = now - timedelta(minutes=45)
        ent_fresh = db.get(GuestEntitlement, fresh.id)
        ent_fresh.reserved = 1
        ent_fresh.reserved_at = now - timedelta(minutes=5)
        db.flush()

        released = _svc(db).release_stale_reservations()

        assert released == 1
        assert ent_stale.reserved == 0 and ent_stale.reserved_at is None
        assert ent_fresh.reserved == 1 and ent_fresh.reserved_at is not None

    def test_idempotent(self, db):
        guest = _make_guest(db, "aaaa1111-0000-0000-0000-000000000003")
        ent = db.get(GuestEntitlement, guest.id)
        ent.reserved = 1
        ent.reserved_at = datetime.now(timezone.utc) - timedelta(hours=2)
        db.flush()

        assert _svc(db).release_stale_reservations() == 1
        assert _svc(db).release_stale_reservations() == 0


# ── Retention purge ─────────────────────────────────────────────────────────


class TestRetentionPurge:
    OLD = datetime(2026, 1, 1, tzinfo=timezone.utc)

    def test_purge_deletes_content_keeps_tombstone(self, db):
        guest = _make_guest(db, "bbbb2222-0000-0000-0000-000000000001",
                            created_at=self.OLD)
        resume, jd, run, doc, profile = _seed_guest_content(db, guest)
        db.add(GuestAbuseEvent(
            user_id=guest.id, ip_daily_hash="x" * 64,
            event_type="session_created",
        ))
        db.add(FunnelEvent(user_id=guest.id, event_type="guest_session_created"))
        db.flush()

        storage = _FakeStorage()
        result = _svc(db, storage).purge_expired_guests()

        assert result == {"candidates": 1, "purged": 1, "failed": 0}

        # Content rows + entitlement gone
        assert db.get(StructuredResume, resume.id) is None
        assert db.get(JobDescription, jd.id) is None
        assert db.get(GenerationRun, run.id) is None
        assert db.get(TailoredDocument, doc.id) is None
        assert db.get(CandidateProfile, profile.id) is None
        assert db.get(GuestEntitlement, guest.id) is None

        # Tombstone: users row kept, guest_purged_at stamped
        user_row = db.get(User, guest.id)
        assert user_row is not None
        assert user_row.guest_purged_at is not None
        assert user_row.is_anonymous is True

        # Abuse + funnel rows retained
        assert db.execute(
            select(GuestAbuseEvent).where(GuestAbuseEvent.user_id == guest.id)
        ).scalars().first() is not None
        assert db.execute(
            select(FunnelEvent).where(FunnelEvent.user_id == guest.id)
        ).scalars().first() is not None

        # Storage: template + generated artifacts + debug json
        assert f"documents/{guest.id}/templates/1.docx" in storage.deleted
        assert f"documents/{guest.id}/generated/{run.id}/resume.pdf" in storage.deleted
        assert (
            f"documents/{guest.id}/generated/{run.id}/cover_letter.pdf"
            in storage.deleted
        )
        assert f"documents/{guest.id}/generated/{run.id}/debug.json" in storage.deleted

    def test_purge_skips_claimed_fresh_and_already_purged(self, db):
        claimed = _make_guest(db, "bbbb2222-0000-0000-0000-000000000002",
                              created_at=self.OLD)
        claimed.guest_claimed_by = "cccc3333-0000-0000-0000-000000000001"
        fresh = _make_guest(db, "bbbb2222-0000-0000-0000-000000000003")
        purged = _make_guest(db, "bbbb2222-0000-0000-0000-000000000004",
                             created_at=self.OLD)
        purged.guest_purged_at = datetime.now(timezone.utc)
        db.flush()
        _seed_guest_content(db, claimed)
        _seed_guest_content(db, fresh)

        result = _svc(db).purge_expired_guests()

        assert result == {"candidates": 0, "purged": 0, "failed": 0}
        # Claimed and fresh guests keep their content + entitlements
        assert db.get(GuestEntitlement, claimed.id) is not None
        assert db.get(GuestEntitlement, fresh.id) is not None
        assert claimed.guest_purged_at is None
        assert fresh.guest_purged_at is None

    def test_purge_respects_admin_retention_days(self, db):
        db.add(AdminConfig(id=1, guest_retention_days=30))
        db.flush()
        # 10 days old — beyond the default 7 but inside the configured 30
        guest = _make_guest(
            db, "bbbb2222-0000-0000-0000-000000000005",
            created_at=datetime.now(timezone.utc) - timedelta(days=10),
        )
        result = _svc(db).purge_expired_guests()
        assert result["candidates"] == 0
        assert guest.guest_purged_at is None


# ── DELETE /guest/data ──────────────────────────────────────────────────────


GUEST_UID = "dddd4444-0000-0000-0000-000000000001"


@pytest.fixture()
def guest_user(db) -> User:
    return _make_guest(db, GUEST_UID)


@pytest.fixture()
def guest_client(db, guest_user) -> TestClient:
    """TestClient authenticated as an anonymous guest."""

    def _override_db():
        yield db

    app.dependency_overrides[get_db_session] = _override_db
    app.dependency_overrides[get_current_user] = lambda: guest_user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


class TestDeleteGuestData:
    def test_guest_can_delete_data(self, guest_client, db, guest_user, monkeypatch):
        resume, jd, run, doc, profile = _seed_guest_content(db, guest_user)

        deleted_keys: list[str] = []

        class _FakeClient:
            def delete_object(self, key, **kwargs):
                deleted_keys.append(key)

        import backend.app.clients.storage_client as sc
        monkeypatch.setattr(
            sc, "make_storage_client_from_settings", lambda: _FakeClient()
        )

        r = guest_client.delete("/api/v1/guest/data")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["purged"] is True
        assert data["resumes_deleted"] == 1
        assert data["job_descriptions_deleted"] == 1
        assert data["generation_runs_deleted"] == 1
        assert data["documents_deleted"] == 1
        assert data["profiles_deleted"] == 1

        assert db.get(StructuredResume, resume.id) is None
        assert db.get(TailoredDocument, doc.id) is None
        assert db.get(GuestEntitlement, guest_user.id) is None
        assert guest_user.guest_purged_at is not None
        assert f"documents/{guest_user.id}/templates/1.docx" in deleted_keys

        ev = db.execute(
            select(FunnelEvent).where(
                FunnelEvent.event_type == "guest_data_deleted"
            )
        ).scalars().first()
        assert ev is not None and ev.user_id == guest_user.id

    def test_regular_user_rejected(self, client):
        r = client.delete("/api/v1/guest/data")
        assert r.status_code == 403


# ── Admin metrics ───────────────────────────────────────────────────────────


class TestGuestMetrics:
    def _seed_events(self, db):
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        events = [
            ("guest_session_created", now),
            ("guest_session_created", now),
            ("guest_profile_created", now),
            ("guest_generation_started", now),
            ("guest_generation_completed", now),
            ("guest_generation_failed", yesterday),
            ("guest_claimed", yesterday),
            # Outside the 14-day window — must be excluded
            ("guest_session_created", now - timedelta(days=20)),
            # Not a guest funnel field — must be ignored
            ("guest_data_deleted", now),
        ]
        for event_type, created_at in events:
            db.add(FunnelEvent(event_type=event_type, created_at=created_at))
        db.flush()

    def test_metrics_shape_and_counts(self, admin_client, db):
        db.add(AdminConfig(
            id=1, guest_enabled=True, guest_daily_global_cap=50,
            guest_concurrent_cap=3, guest_ip_daily_limit=5,
            guest_retention_days=14,
        ))
        db.flush()
        self._seed_events(db)

        r = admin_client.get("/api/v1/admin/guest-metrics")
        assert r.status_code == 200, r.text
        data = r.json()

        assert len(data["days"]) == 14
        assert data["totals"] == {
            "sessions": 2,
            "profiles": 1,
            "generations_started": 1,
            "generations_completed": 1,
            "generations_failed": 1,
            "claims": 1,
        }
        today = datetime.now(timezone.utc).date().isoformat()
        today_row = next(d for d in data["days"] if d["date"] == today)
        assert today_row["sessions"] == 2
        assert today_row["generations_completed"] == 1

        assert data["config"] == {
            "guest_enabled": True,
            "guest_daily_global_cap": 50,
            "guest_concurrent_cap": 3,
            "guest_ip_daily_limit": 5,
            "guest_retention_days": 14,
        }

    def test_metrics_admin_only(self, client):
        r = client.get("/api/v1/admin/guest-metrics")
        assert r.status_code == 403


class TestGuestConfigUpdate:
    def test_update_guest_fields(self, admin_client, db):
        r = admin_client.put(
            "/api/v1/admin/generation-config",
            json={
                "guest_enabled": True,
                "guest_daily_global_cap": 40,
                "guest_concurrent_cap": 4,
                "guest_ip_daily_limit": 6,
                "guest_retention_days": 10,
            },
        )
        assert r.status_code == 200, r.text
        cfg = db.execute(select(AdminConfig)).scalars().first()
        assert cfg.guest_enabled is True
        assert cfg.guest_daily_global_cap == 40
        assert cfg.guest_concurrent_cap == 4
        assert cfg.guest_ip_daily_limit == 6
        assert cfg.guest_retention_days == 10

    def test_simple_model_still_works_alone(self, admin_client, db):
        r = admin_client.put(
            "/api/v1/admin/generation-config",
            json={"simple_model": "gpt-5.1"},
        )
        assert r.status_code == 200
        cfg = db.execute(select(AdminConfig)).scalars().first()
        assert cfg.simple_model == "gpt-5.1"

    def test_invalid_values_rejected(self, admin_client):
        assert admin_client.put(
            "/api/v1/admin/generation-config",
            json={"guest_daily_global_cap": -1},
        ).status_code == 422
        assert admin_client.put(
            "/api/v1/admin/generation-config",
            json={"guest_retention_days": 0},
        ).status_code == 422
        assert admin_client.put(
            "/api/v1/admin/generation-config", json={}
        ).status_code == 422
