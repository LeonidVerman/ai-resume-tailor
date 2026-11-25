"""
backend/tests/api/test_guest_pipeline.py

Guest generation (issue #155) — Phase 2 tests:
- paste-only job descriptions for guests (scrape 403)
- guest resume replacement (one at a time, profile dropped)
- PDF-only downloads for guests
- guest generation caps (global daily / concurrency)
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db.models.admin_config import AdminConfig
from backend.app.db.models.candidate_profile import CandidateProfile
from backend.app.db.models.guest import GuestEntitlement
from backend.app.db.models.structured_resume import StructuredResume
from backend.app.db.models.user import User
from backend.app.dependencies import get_current_user, get_db_session
from backend.app.main import app
from backend.app.services.guest_service import GuestService
from backend.tests.conftest import dumps


GUID = "99999999-9999-9999-9999-999999999999"


@pytest.fixture()
def guest_user(db):
    u = User(
        id=GUID, supabase_user_id=GUID,
        email=f"guest-{GUID}@guest.cvrocket.invalid", is_anonymous=True,
    )
    db.add(u)
    db.flush()
    db.add(GuestEntitlement(user_id=GUID, allowed=1))
    db.flush()
    return u


@pytest.fixture()
def guest_authed_client(db, guest_user):
    """TestClient authenticated AS the guest user."""
    app.dependency_overrides.clear()

    def _db_override():
        yield db

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_current_user] = lambda: guest_user
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


class TestGuestPasteOnly:
    def test_scrape_rejected_for_guest(self, guest_authed_client):
        r = guest_authed_client.post(
            "/api/v1/job-descriptions/scrape",
            json={"url": "https://example.com/job/1"},
        )
        assert r.status_code == 403
        assert "GUEST_PASTE_ONLY" in r.json()["detail"]

    def test_manual_allowed_for_guest(self, guest_authed_client):
        r = guest_authed_client.post(
            "/api/v1/job-descriptions/manual",
            json={
                "title": "Backend Engineer",
                "company": "Acme",
                "raw_text": "Build backend services with Python.",
            },
        )
        assert r.status_code == 201, r.text


class TestGuestResumeReplacement:
    def test_replace_soft_deletes_and_drops_profile(self, db, guest_user):
        r1 = StructuredResume(user_id=GUID, resume_jsonb={}, sha256="a" * 64)
        db.add(r1)
        db.flush()
        db.add(CandidateProfile(
            user_id=GUID, profile_jsonb={}, onboarding_completed=True,
            is_unreviewed=True, source_resume_id=r1.id,
        ))
        db.flush()
        GuestService(db, get_settings()).replace_guest_resumes(GUID)
        assert db.get(StructuredResume, r1.id).delete_flg is True
        prof = db.query(CandidateProfile).filter_by(user_id=GUID).first()
        assert prof is None

    def test_reviewed_profile_never_dropped(self, db, guest_user):
        r1 = StructuredResume(user_id=GUID, resume_jsonb={})
        db.add(r1)
        db.flush()
        db.add(CandidateProfile(
            user_id=GUID, profile_jsonb={}, onboarding_completed=True,
            is_unreviewed=False,
        ))
        db.flush()
        GuestService(db, get_settings()).replace_guest_resumes(GUID)
        assert db.query(CandidateProfile).filter_by(user_id=GUID).count() == 1


class TestGuestGenerationCaps:
    def test_concurrency_cap(self, db, guest_user):
        cfg = AdminConfig(guest_enabled=True, guest_concurrent_cap=1)
        db.add(cfg)
        db.flush()
        svc = GuestService(db, get_settings())
        svc.reserve_generation(GUID)
        with pytest.raises(HTTPException) as exc:
            svc.check_generation_caps()
        assert exc.value.status_code == 429

    def test_daily_global_cap(self, db, guest_user):
        cfg = AdminConfig(guest_enabled=True, guest_daily_global_cap=1)
        db.add(cfg)
        db.flush()
        svc = GuestService(db, get_settings())
        svc.record_abuse("x" * 64, "generation_reserved", user_id=GUID)
        with pytest.raises(HTTPException) as exc:
            svc.check_generation_caps()
        assert exc.value.status_code == 429


class TestGuestDocumentDownload:
    def _make_doc(self, db, user_id):
        from backend.app.db.models.generation_run import GenerationRun
        from backend.app.db.models.tailored_document import TailoredDocument
        run = GenerationRun(
            user_id=user_id, status="completed", run_type="simple",
            started_at=datetime.now(timezone.utc),
        )
        db.add(run)
        db.flush()
        doc = TailoredDocument(
            user_id=user_id, generation_run_id=run.id,
            company_name="Acme", role_title="Engineer",
        )
        db.add(doc)
        db.flush()
        return doc

    def test_txt_and_docx_rejected_pdf_attempted(self, db, guest_user, guest_authed_client):
        doc = self._make_doc(db, GUID)
        for fmt in ("txt", "docx"):
            r = guest_authed_client.get(
                f"/api/v1/documents/{doc.id}/download?part=resume&format={fmt}"
            )
            assert r.status_code == 403, fmt
            assert "REGISTER_TO_UNLOCK" in r.json()["detail"]
        # PDF passes the guest gate (may 404/500 later without storage — the
        # point is it is NOT rejected by the guest format guard)
        r = guest_authed_client.get(
            f"/api/v1/documents/{doc.id}/download?part=resume&format=pdf"
        )
        assert r.status_code != 403 or "REGISTER_TO_UNLOCK" not in (
            r.json().get("detail") or ""
        )
