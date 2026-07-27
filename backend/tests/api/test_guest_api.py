"""
backend/tests/api/test_guest_api.py

Guest generation (issue #155) — Phase 0/1 tests:
- kill switch
- guest session creation (mocked Supabase anonymous sign-in)
- device reuse rejection
- IP daily velocity limit
- atomic entitlement reservation semantics
- device token mint/verify helpers
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.config import get_settings
from backend.app.db.models.admin_config import AdminConfig
from backend.app.db.models.guest import GuestDevice, GuestEntitlement
from backend.app.db.models.user import User
from backend.app.dependencies import get_db_session
from backend.app.main import app
from backend.app.services import guest_service as gs
from backend.tests.conftest import _seed_legal_docs


@pytest.fixture()
def guest_client(db):
    """TestClient with the DB override but NO auth override (public route)."""
    app.dependency_overrides.clear()

    def _db_override():
        yield db

    app.dependency_overrides[get_db_session] = _db_override
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


def _enable_guest(db):
    cfg = AdminConfig(guest_enabled=True)
    db.add(cfg)
    db.flush()
    return cfg


def _seed_docs(db):
    terms, privacy = _seed_legal_docs(db)
    return terms.id, privacy.id


class _FakeSupabase:
    def __init__(self, uid: str):
        self._uid = uid

    def sign_in_anonymous(self):
        return SimpleNamespace(
            user=SimpleNamespace(id=self._uid),
            session=SimpleNamespace(
                access_token="anon-access", refresh_token="anon-refresh"
            ),
        )


def _mock_supabase(monkeypatch, uid="11111111-1111-1111-1111-111111111111"):
    import backend.app.clients.supabase_client as sc
    monkeypatch.setattr(
        sc, "make_supabase_client_from_settings", lambda: _FakeSupabase(uid)
    )


def _session_body(terms_id, privacy_id):
    return {
        "turnstile_token": "test-token",
        "terms_document_id": terms_id,
        "privacy_document_id": privacy_id,
    }


class TestGuestSession:
    def test_kill_switch_off_rejects(self, guest_client, db):
        t, p = _seed_docs(db)
        r = guest_client.post("/api/v1/guest/session", json=_session_body(t, p))
        assert r.status_code == 403

    def test_session_created(self, guest_client, db, monkeypatch):
        _enable_guest(db)
        t, p = _seed_docs(db)
        _mock_supabase(monkeypatch)
        r = guest_client.post("/api/v1/guest/session", json=_session_body(t, p))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["access_token"] == "anon-access"
        user = db.get(User, data["user_id"])
        assert user is not None and user.is_anonymous is True
        assert user.email.endswith("@guest.cvrocket.invalid")
        ent = db.get(GuestEntitlement, data["user_id"])
        assert ent is not None and ent.allowed == 1 and ent.used == 0
        # device cookie set
        assert "cvr_guest_device" in r.cookies

    def test_device_reuse_rejected(self, guest_client, db, monkeypatch):
        _enable_guest(db)
        t, p = _seed_docs(db)
        _mock_supabase(monkeypatch)
        r1 = guest_client.post("/api/v1/guest/session", json=_session_body(t, p))
        assert r1.status_code == 200
        # Mark the device's generation as used
        token = r1.cookies["cvr_guest_device"]
        device = db.get(GuestDevice, gs.device_token_hash(token))
        device.generation_used_at = datetime.now(timezone.utc)
        db.flush()
        _mock_supabase(monkeypatch, uid="22222222-2222-2222-2222-222222222222")
        r2 = guest_client.post(
            "/api/v1/guest/session",
            json=_session_body(t, p),
            cookies={"cvr_guest_device": token},
        )
        assert r2.status_code == 429

    def test_ip_daily_limit(self, guest_client, db, monkeypatch):
        cfg = _enable_guest(db)
        cfg.guest_ip_daily_limit = 2
        t, p = _seed_docs(db)
        for i in range(2):
            _mock_supabase(monkeypatch, uid=f"33333333-3333-3333-3333-33333333333{i}")
            r = guest_client.post(
                "/api/v1/guest/session", json=_session_body(t, p)
            )
            assert r.status_code == 200
            guest_client.cookies.clear()  # new device each time
        _mock_supabase(monkeypatch, uid="44444444-4444-4444-4444-444444444444")
        r = guest_client.post("/api/v1/guest/session", json=_session_body(t, p))
        assert r.status_code == 429


class TestEntitlementReservation:
    def _make_guest(self, db, uid="55555555-5555-5555-5555-555555555555"):
        u = User(
            id=uid, supabase_user_id=uid,
            email=f"guest-{uid}@guest.cvrocket.invalid", is_anonymous=True,
        )
        db.add(u)
        db.flush()
        db.add(GuestEntitlement(user_id=uid, allowed=1))
        db.flush()
        return u

    def test_reserve_finalize(self, db):
        u = self._make_guest(db)
        svc = gs.GuestService(db, get_settings())
        ent = svc.reserve_generation(u.id)
        assert ent.reserved == 1 and ent.reserved_at is not None
        svc.finalize_generation(u.id)
        ent = db.get(GuestEntitlement, u.id)
        assert ent.used == 1 and ent.reserved == 0 and ent.reserved_at is None

    def test_double_reserve_rejected(self, db):
        from fastapi import HTTPException
        u = self._make_guest(db, uid="66666666-6666-6666-6666-666666666666")
        svc = gs.GuestService(db, get_settings())
        svc.reserve_generation(u.id)
        with pytest.raises(HTTPException) as exc:
            svc.reserve_generation(u.id)
        assert exc.value.status_code == 429

    def test_release_returns_credit(self, db):
        u = self._make_guest(db, uid="77777777-7777-7777-7777-777777777777")
        svc = gs.GuestService(db, get_settings())
        svc.reserve_generation(u.id)
        svc.release_generation(u.id)
        ent = db.get(GuestEntitlement, u.id)
        assert ent.used == 0 and ent.reserved == 0
        # credit is available again
        svc.reserve_generation(u.id)

    def test_exhausted_after_use(self, db):
        from fastapi import HTTPException
        u = self._make_guest(db, uid="88888888-8888-8888-8888-888888888888")
        svc = gs.GuestService(db, get_settings())
        svc.reserve_generation(u.id)
        svc.finalize_generation(u.id)
        with pytest.raises(HTTPException) as exc:
            svc.reserve_generation(u.id)
        assert exc.value.status_code == 429


class TestHelpers:
    def test_device_token_roundtrip(self):
        s = get_settings()
        token = gs.mint_device_token(s)
        assert gs.verify_device_token(token, s)
        assert not gs.verify_device_token(token + "x", s)
        assert not gs.verify_device_token("garbage", s)

    def test_ip_daily_hash_stable_within_day(self):
        s = get_settings()
        now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
        assert gs.ip_daily_hash("1.2.3.4", s, now) == gs.ip_daily_hash("1.2.3.4", s, now)
        other_day = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
        assert gs.ip_daily_hash("1.2.3.4", s, now) != gs.ip_daily_hash("1.2.3.4", s, other_day)
        assert gs.ip_daily_hash("1.2.3.4", s, now) != gs.ip_daily_hash("5.6.7.8", s, now)
