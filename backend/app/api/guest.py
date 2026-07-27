"""
backend/app/api/guest.py

Guest generation (issue #155) — public endpoints for the /try flow.

POST /guest/session
    Public.  Verifies Turnstile, applies device/IP abuse controls, records
    legal consent, creates the anonymous Supabase identity + local guest
    user + entitlement, and returns the Supabase session tokens.  The
    frontend stores them in the standard token slots so every other API
    call flows through the normal Authorization header.

The guest identity is created here — after Turnstile succeeds and the
visitor initiates the flow — never on page view.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from backend.app.dependencies import DbDep, SettingsDep
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
