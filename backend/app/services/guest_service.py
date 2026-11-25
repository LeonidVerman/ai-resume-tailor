"""
backend/app/services/guest_service.py

Guest generation (issue #155) — session creation and abuse-control helpers.

Responsibilities
----------------
- Verify the Cloudflare Turnstile token server-side.
- Derive the daily HMAC IP grouping hash (no clear IPs stored).
- Mint / verify signed first-party guest device tokens.
- Create the anonymous Supabase identity + local guest user row +
  entitlement, and record legal consent through the existing legal tables.

The entitlement reservation logic (atomic reserve/finalize/release) also
lives here so the generation endpoint stays thin.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.db.models.guest import (
    FunnelEvent,
    GuestAbuseEvent,
    GuestDevice,
    GuestEntitlement,
)
from backend.app.db.models.user import User

logger = logging.getLogger(__name__)

# Guest local users get a synthetic email (users.email is NOT NULL + unique).
GUEST_EMAIL_DOMAIN = "guest.cvrocket.invalid"

# A reservation older than this is considered abandoned (crashed process)
# and is released by the cleanup job.
STALE_RESERVATION_MINUTES = 30

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _hash_secret(settings: Settings) -> bytes:
    return (settings.guest_hash_secret or settings.secret_key).encode("utf-8")


def ip_daily_hash(ip: str, settings: Settings, when: datetime | None = None) -> str:
    """Daily HMAC-derived IP grouping value (design doc §6)."""
    day = (when or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return hmac.new(
        _hash_secret(settings), f"{ip}|{day}".encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ── Device tokens ──────────────────────────────────────────────────────────

def mint_device_token(settings: Settings) -> str:
    """Random value + HMAC signature, cookie-safe."""
    value = secrets.token_urlsafe(24)
    sig = hmac.new(
        _hash_secret(settings), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return f"{value}.{sig}"


def verify_device_token(token: str, settings: Settings) -> bool:
    try:
        value, sig = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = hmac.new(
        _hash_secret(settings), value.encode("utf-8"), hashlib.sha256
    ).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


def device_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ── Turnstile ──────────────────────────────────────────────────────────────

def verify_turnstile(token: str, remote_ip: str, settings: Settings) -> None:
    """Server-side Turnstile verification.  Raises 403 on failure.

    An empty ``turnstile_secret`` skips verification in development only.
    """
    if not settings.turnstile_secret:
        if settings.app_env == "development":
            logger.warning("Turnstile secret not set — skipping check (development)")
            return
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Human verification is not configured",
        )
    import httpx

    try:
        resp = httpx.post(
            TURNSTILE_VERIFY_URL,
            data={
                "secret": settings.turnstile_secret,
                "response": token,
                "remoteip": remote_ip,
            },
            timeout=10.0,
        )
        outcome = resp.json()
    except Exception as exc:
        logger.error("Turnstile verification request failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Human verification temporarily unavailable",
        )
    if not outcome.get("success"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Human verification failed",
        )


# ── Service ────────────────────────────────────────────────────────────────

class GuestService:
    def __init__(self, db: Session, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    # -- config -------------------------------------------------------------

    def _admin_config(self):
        from backend.app.db.models.admin_config import AdminConfig
        return self._db.execute(select(AdminConfig).limit(1)).scalar_one_or_none()

    def require_enabled(self) -> None:
        cfg = self._admin_config()
        if cfg is None or not cfg.guest_enabled:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Guest generation is not available",
            )

    # -- abuse events / funnel ------------------------------------------------

    def record_abuse(
        self, ip_hash: str, event_type: str, risk_reason: str | None = None,
        user_id: str | None = None,
    ) -> None:
        self._db.add(GuestAbuseEvent(
            user_id=user_id, ip_daily_hash=ip_hash,
            event_type=event_type, risk_reason=risk_reason,
        ))
        self._db.flush()

    def record_funnel(self, event_type: str, user_id: str | None = None,
                      meta: dict | None = None) -> None:
        self._db.add(FunnelEvent(user_id=user_id, event_type=event_type, meta=meta))
        self._db.flush()

    # -- IP velocity ----------------------------------------------------------

    def check_ip_velocity(self, ip_hash: str) -> None:
        cfg = self._admin_config()
        limit = cfg.guest_ip_daily_limit if cfg else 3
        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        count = self._db.execute(
            select(func.count()).select_from(GuestAbuseEvent).where(
                GuestAbuseEvent.ip_daily_hash == ip_hash,
                GuestAbuseEvent.event_type == "session_created",
                GuestAbuseEvent.created_at >= day_start,
            )
        ).scalar_one()
        if count >= limit:
            self.record_abuse(ip_hash, "ip_daily_limit_rejected")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Guest limit reached — create a free account to continue",
            )

    # -- device --------------------------------------------------------------

    def register_device(self, token: str, ip_hash: str) -> GuestDevice:
        """Look up or create the device row.  A device whose guest generation
        was already used cannot start a new guest session."""
        th = device_token_hash(token)
        device = self._db.get(GuestDevice, th)
        now = datetime.now(timezone.utc)
        if device is None:
            device = GuestDevice(
                device_token_hash=th, first_seen_at=now,
            )
            self._db.add(device)
            self._db.flush()
        elif device.generation_used_at is not None:
            self.record_abuse(ip_hash, "device_reuse_rejected")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="This device already used its free guest generation — "
                       "create a free account to continue",
            )
        return device

    # -- session -------------------------------------------------------------

    def create_guest_session(
        self,
        *,
        ip_hash: str,
        device_token: str,
        terms_document_id: int,
        privacy_document_id: int,
        request=None,
    ) -> dict:
        """Create the anonymous Supabase user + local guest row + entitlement.

        Returns {access_token, refresh_token, user_id}.
        """
        from backend.app.clients.supabase_client import (
            make_supabase_client_from_settings,
        )

        supabase = make_supabase_client_from_settings()
        resp = supabase.sign_in_anonymous()
        if resp is None or resp.user is None or resp.session is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not create guest session",
            )
        supabase_id = resp.user.id

        user = User(
            id=supabase_id,
            supabase_user_id=supabase_id,
            email=f"guest-{supabase_id}@{GUEST_EMAIL_DOMAIN}",
            is_anonymous=True,
        )
        self._db.add(user)
        self._db.flush()

        self._db.add(GuestEntitlement(user_id=user.id, allowed=1))

        device = self._db.get(GuestDevice, device_token_hash(device_token))
        if device is not None:
            device.guest_user_id = user.id

        # Consent through the existing legal system (append-only events +
        # user status cache) — same records as registered acceptance, with
        # source_surface marking the guest flow.
        from backend.app.services.legal_service import LegalService
        LegalService(self._db).record_acceptance(
            user_id=user.id,
            terms_document_id=terms_document_id,
            privacy_document_id=privacy_document_id,
            acceptance_method="checkbox",
            source_surface="guest_try",
            request=request,
        )
        self.record_abuse(ip_hash, "session_created", user_id=user.id)
        self.record_funnel("guest_session_created", user_id=user.id)
        self._db.flush()

        return {
            "access_token": resp.session.access_token,
            "refresh_token": resp.session.refresh_token,
            "user_id": user.id,
        }

    # -- guest pipeline helpers (Phase 2) -------------------------------------

    def check_generation_caps(self) -> None:
        """Global daily cap + concurrency cap (circuit breakers)."""
        cfg = self._admin_config()
        daily_cap = cfg.guest_daily_global_cap if cfg else 25
        conc_cap = cfg.guest_concurrent_cap if cfg else 2
        day_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today = self._db.execute(
            select(func.count()).select_from(GuestAbuseEvent).where(
                GuestAbuseEvent.event_type == "generation_reserved",
                GuestAbuseEvent.created_at >= day_start,
            )
        ).scalar_one()
        if today >= daily_cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="The free trial is temporarily at capacity — please "
                       "try again later or create a free account",
            )
        in_flight = self._db.execute(
            select(func.coalesce(func.sum(GuestEntitlement.reserved), 0))
        ).scalar_one()
        if in_flight >= conc_cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="The free trial is busy right now — please try again "
                       "in a minute",
            )

    def mark_device_used(self, user_id: str) -> None:
        device = self._db.execute(
            select(GuestDevice).where(GuestDevice.guest_user_id == user_id)
        ).scalars().first()
        if device is not None and device.generation_used_at is None:
            device.generation_used_at = datetime.now(timezone.utc)
            self._db.flush()

    def replace_guest_resumes(self, user_id: str) -> None:
        """Guest keeps at most ONE resume: soft-delete previous rows and drop
        the stale auto-generated profile so it is rebuilt from the new file."""
        from backend.app.db.models.candidate_profile import CandidateProfile
        from backend.app.db.models.structured_resume import StructuredResume

        stale = self._db.execute(
            select(StructuredResume).where(
                StructuredResume.user_id == user_id,
                StructuredResume.delete_flg.is_(False),
            )
        ).scalars().all()
        for r in stale:
            r.delete_flg = True
        if stale:
            profile = self._db.execute(
                select(CandidateProfile).where(
                    CandidateProfile.user_id == user_id,
                    CandidateProfile.is_unreviewed.is_(True),
                )
            ).scalars().first()
            if profile is not None:
                self._db.delete(profile)
            logger.info(
                "Guest resume replaced user=%s (%d previous soft-deleted)",
                user_id, len(stale),
            )
        self._db.flush()

    def ensure_guest_profile(self, user_id: str, resume_id: int):
        """Auto-accept a resume-derived temporary profile (unreviewed).

        Marks onboarding_completed so the standard generation gate passes;
        the resume remains the authoritative evidence.  Returns the profile.
        """
        from backend.app.db.models.candidate_profile import CandidateProfile
        from backend.app.services.profile_autofill_service import (
            ProfileAutofillService,
        )

        existing = self._db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        ).scalars().first()
        if existing is not None:
            return existing

        draft = ProfileAutofillService(self._db).generate(user_id, resume_id)
        if draft.status != "ready":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Could not extract a candidate profile from this resume",
            )
        now = datetime.now(timezone.utc)
        profile = CandidateProfile(
            user_id=user_id,
            profile_jsonb=draft.draft.model_dump(mode="json"),
            onboarding_completed=True,
            onboarding_completed_at=now,
            is_unreviewed=True,
            source_resume_id=resume_id,
        )
        self._db.add(profile)
        self._db.flush()
        self.record_funnel("guest_profile_created", user_id=user_id)
        return profile

    # -- entitlement reservation (used by the generation endpoint, Phase 2) --

    def reserve_generation(self, user_id: str) -> GuestEntitlement:
        """Atomic credit reservation (design doc §7).  Raises 429 when the
        entitlement is exhausted; the caller MUST later call
        finalize_generation() or release_generation()."""
        ent = self._db.execute(
            select(GuestEntitlement)
            .where(GuestEntitlement.user_id == user_id)
            .with_for_update()
        ).scalar_one_or_none()
        if ent is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No guest entitlement",
            )
        if ent.used + ent.reserved >= ent.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Guest generation already used — create a free account "
                       "to continue",
            )
        now = datetime.now(timezone.utc)
        ent.reserved += 1
        ent.reserved_at = now
        ent.updated_at = now
        self._db.flush()
        return ent

    def finalize_generation(self, user_id: str) -> None:
        ent = self._db.execute(
            select(GuestEntitlement)
            .where(GuestEntitlement.user_id == user_id)
            .with_for_update()
        ).scalar_one()
        now = datetime.now(timezone.utc)
        ent.reserved = max(0, ent.reserved - 1)
        ent.used += 1
        ent.reserved_at = None if ent.reserved == 0 else ent.reserved_at
        ent.updated_at = now
        self._db.flush()

    def release_generation(self, user_id: str) -> None:
        ent = self._db.execute(
            select(GuestEntitlement)
            .where(GuestEntitlement.user_id == user_id)
            .with_for_update()
        ).scalar_one()
        ent.reserved = max(0, ent.reserved - 1)
        ent.reserved_at = None if ent.reserved == 0 else ent.reserved_at
        ent.updated_at = datetime.now(timezone.utc)
        self._db.flush()

    @staticmethod
    def stale_reservation_cutoff() -> datetime:
        return datetime.now(timezone.utc) - timedelta(
            minutes=STALE_RESERVATION_MINUTES
        )

    # -- claim/merge after registration (Phase 4) ------------------------------

    def claim_guest(self, claimer: User, guest_access_token: str) -> dict:
        """Merge a guest account into the freshly registered ``claimer``.

        Possession of a valid guest JWT is the ownership proof (design doc
        Phase 4).  All row moves happen in the caller's request transaction,
        so the merge commits or rolls back as one unit.  R2/storage keys stay
        under the old guest namespace — authorization is row-based.
        """
        from sqlalchemy import update

        from backend.app.clients.supabase_client import (
            make_supabase_client_from_settings,
        )
        from backend.app.db.models.candidate_profile import CandidateProfile
        from backend.app.db.models.generation_run import GenerationRun
        from backend.app.db.models.job_description import JobDescription
        from backend.app.db.models.structured_resume import StructuredResume
        from backend.app.db.models.tailored_document import TailoredDocument

        if getattr(claimer, "is_anonymous", False):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Create a full account to claim guest results",
            )

        supabase = make_supabase_client_from_settings()
        try:
            payload = supabase.verify_token(guest_access_token)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired guest token",
            )

        guest = self._db.execute(
            select(User)
            .where(User.supabase_user_id == payload["id"])
            .with_for_update()
        ).scalar_one_or_none()
        if guest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Guest account not found",
            )
        if not guest.is_anonymous:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not a guest account",
            )
        if guest.id == claimer.id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot claim your own session",
            )
        if guest.guest_claimed_by is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This guest session was already claimed",
            )

        # Reassign content rows guest → claimer (one transaction with the
        # rest of this method; get_db_session commits on request success).
        counts: dict[str, int] = {}
        for key, model in (
            ("resumes_moved", StructuredResume),
            ("job_descriptions_moved", JobDescription),
            ("generation_runs_moved", GenerationRun),
            ("documents_moved", TailoredDocument),
        ):
            result = self._db.execute(
                update(model)
                .where(model.user_id == guest.id)
                .values(user_id=claimer.id)
            )
            counts[key] = result.rowcount or 0

        # Move the auto-accepted guest profile ONLY if the claimer has none;
        # otherwise it stays with the guest row for the retention purge.
        profile_moved = False
        claimer_profile = self._db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == claimer.id)
        ).scalars().first()
        if claimer_profile is None:
            guest_profile = self._db.execute(
                select(CandidateProfile).where(CandidateProfile.user_id == guest.id)
            ).scalars().first()
            if guest_profile is not None:
                guest_profile.user_id = claimer.id
                profile_moved = True

        # Lifetime free-generation accounting: the guest usage now counts
        # against the claimer's 3 lifetime free generations.
        ent = self._db.get(GuestEntitlement, guest.id)
        credits_added = ent.used if ent is not None else 0
        if credits_added:
            claimer.free_generations_used = (
                claimer.free_generations_used or 0
            ) + credits_added

        now = datetime.now(timezone.utc)
        guest.guest_claimed_by = claimer.id
        guest.is_active = False
        guest.updated_at = now

        self.record_funnel(
            "guest_claimed",
            user_id=claimer.id,
            meta={"guest_user_id": guest.id},
        )
        self._db.flush()

        logger.info(
            "Guest %s claimed by %s (resumes=%d jds=%d runs=%d docs=%d "
            "profile_moved=%s credits=%d)",
            guest.id, claimer.id, counts["resumes_moved"],
            counts["job_descriptions_moved"], counts["generation_runs_moved"],
            counts["documents_moved"], profile_moved, credits_added,
        )

        return {
            "guest_user_id": guest.id,
            **counts,
            "profile_moved": profile_moved,
            "credits_added": credits_added,
        }
