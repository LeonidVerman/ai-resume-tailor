"""
backend/app/api/guest.py

Guest generation (issue #155) — public endpoints for the /try flow.

POST /guest/session
    Public.  Verifies Turnstile, applies device/IP abuse controls, records
    legal consent, creates the anonymous Supabase identity + local guest
    user + entitlement, and returns the Supabase session tokens.  The
    frontend stores them in the standard token slots so every other API
    call flows through the normal Authorization header.

POST /guest/claim
    Authenticated (as the NEW registered user).  Merges a guest account —
    proven by possession of its still-valid Supabase JWT — into the caller:
    content rows are reassigned, guest usage is added to the claimer's
    lifetime free-generation counter, and the guest row is deactivated.

DELETE /guest/data
    Authenticated guest only ("Delete my files now", Phase 5).  Immediately
    purges the caller's storage objects and content rows using the same
    logic as the retention job — the users row stays as a tombstone with
    guest_purged_at stamped.

The guest identity is created here — after Turnstile succeeds and the
visitor initiates the flow — never on page view.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from backend.app.dependencies import CurrentUserDep, DbDep, SettingsDep
from backend.app.services.guest_service import (
    GuestService,
    ip_daily_hash,
    mint_device_token,
    verify_device_token,
    verify_turnstile,
)

logger = logging.getLogger(__name__)

router = APIRouter()

DEVICE_COOKIE_NAME = "cvr_guest_device"
DEVICE_COOKIE_MAX_AGE = int(timedelta(days=365).total_seconds())


class GuestSessionRequest(BaseModel):
    turnstile_token: str
    terms_document_id: int
    privacy_document_id: int


class GuestSessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    user_id: str


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@router.post("/session", response_model=GuestSessionResponse)
def create_guest_session(
    body: GuestSessionRequest,
    request: Request,
    response: Response,
    db: DbDep,
    settings: SettingsDep,
):
    service = GuestService(db, settings)

    # 1. Kill switch
    service.require_enabled()

    # 2. Human verification (server-side Turnstile)
    ip = _client_ip(request)
    verify_turnstile(body.turnstile_token, ip, settings)

    # 3. IP velocity (soft daily limit on session creation)
    ip_hash = ip_daily_hash(ip, settings)
    service.check_ip_velocity(ip_hash)

    # 4. Device token: verify existing cookie or mint a new one
    device_token = request.cookies.get(DEVICE_COOKIE_NAME)
    if not device_token or not verify_device_token(device_token, settings):
        device_token = mint_device_token(settings)
    service.register_device(device_token, ip_hash)

    # 5. Create anonymous identity + entitlement + consent records
    session = service.create_guest_session(
        ip_hash=ip_hash,
        device_token=device_token,
        terms_document_id=body.terms_document_id,
        privacy_document_id=body.privacy_document_id,
        request=request,
    )

    response.set_cookie(
        DEVICE_COOKIE_NAME,
        device_token,
        max_age=DEVICE_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.app_env != "development",
        samesite="lax",
    )
    return GuestSessionResponse(**session)


# ── Claim/merge after registration (Phase 4) ───────────────────────────────

class GuestClaimRequest(BaseModel):
    guest_access_token: str


class GuestClaimResponse(BaseModel):
    guest_user_id: str
    resumes_moved: int
    job_descriptions_moved: int
    generation_runs_moved: int
    documents_moved: int
    profile_moved: bool
    credits_added: int


@router.post("/claim", response_model=GuestClaimResponse)
def claim_guest(
    body: GuestClaimRequest,
    user: CurrentUserDep,
    db: DbDep,
    settings: SettingsDep,
):
    """Merge a guest session's content into the authenticated account.

    Deliberately NOT gated by the guest kill switch — claiming already
    generated content must keep working even when new guest sessions are
    disabled.
    """
    result = GuestService(db, settings).claim_guest(user, body.guest_access_token)
    return GuestClaimResponse(**result)


# ── Delete my files now (Phase 5) ──────────────────────────────────────────

class GuestDataDeleteResponse(BaseModel):
    purged: bool
    resumes_deleted: int
    job_descriptions_deleted: int
    generation_runs_deleted: int
    documents_deleted: int
    profiles_deleted: int


@router.delete("/data", response_model=GuestDataDeleteResponse)
def delete_guest_data(
    user: CurrentUserDep,
    db: DbDep,
    settings: SettingsDep,
):
    """Immediately purge the authenticated guest's files and content rows.

    Guest accounts only — registered users manage their data through the
    normal account surfaces.  The users row is kept as a tombstone
    (guest_purged_at stamped), same as the retention job.  Deliberately NOT
    gated by the kill switch — deleting one's data must always work.
    """
    if not getattr(user, "is_anonymous", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only guest accounts can delete data through this endpoint",
        )

    from backend.app.services.guest_cleanup_service import GuestCleanupService

    counts = GuestCleanupService(db, settings).purge_guest(user)
    GuestService(db, settings).record_funnel("guest_data_deleted", user_id=user.id)
    logger.info("Guest data deleted on request user=%s counts=%s", user.id, counts)
    return GuestDataDeleteResponse(purged=True, **counts)
