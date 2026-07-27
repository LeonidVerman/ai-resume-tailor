"""
backend/app/services/guest_cleanup_service.py

Guest generation (issue #155, Phase 5) — retention cleanup.

Responsibilities
----------------
- Release stale entitlement reservations (reserved > 0 and reserved_at older
  than the 30-minute stale window) so a crashed generation never permanently
  consumes the guest credit.
- Purge unclaimed anonymous users older than the admin-configured retention
  window: delete their storage objects (resume templates + generated
  artifacts) and content rows, delete the entitlement row, and KEEP the
  users row as a tombstone (guest_purged_at stamped).  Legal acceptance,
  abuse events, funnel events and device rows are retained.

The same per-guest purge powers the immediate "Delete my files now"
endpoint (DELETE /guest/data).

Crash safety: the scheduled purge commits per guest and logs-and-continues
on failure, so one broken guest never blocks the rest of the run.  All
operations are idempotent — a purged guest is excluded from the candidate
query by guest_purged_at.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.config import Settings
from backend.app.db.models.guest import GuestEntitlement
from backend.app.db.models.user import User
from backend.app.services.guest_service import GuestService

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 7


class GuestCleanupService:
    def __init__(self, db: Session, settings: Settings, storage=None) -> None:
        self._db = db
        self._settings = settings
        self._storage = storage  # StorageService (injectable for tests)

    def _storage_service(self):
        if self._storage is None:
            from backend.app.clients.storage_client import (
                make_storage_client_from_settings,
            )
            from backend.app.services.storage_service import StorageService
            self._storage = StorageService(make_storage_client_from_settings())
        return self._storage

    # ── Stale reservations ─────────────────────────────────────────────────

    def release_stale_reservations(self) -> int:
        """Release reservations older than the 30-minute stale window.

        Returns the number of entitlements released.
        """
        cutoff = GuestService.stale_reservation_cutoff()
        stale = self._db.execute(
            select(GuestEntitlement).where(
                GuestEntitlement.reserved > 0,
                GuestEntitlement.reserved_at < cutoff,
            )
        ).scalars().all()
        now = datetime.now(timezone.utc)
        for ent in stale:
            logger.warning(
                "Releasing stale guest reservation user=%s reserved=%d "
                "reserved_at=%s", ent.user_id, ent.reserved, ent.reserved_at,
            )
            ent.reserved = 0
            ent.reserved_at = None
            ent.updated_at = now
        self._db.flush()
        return len(stale)

    # ── Retention purge ────────────────────────────────────────────────────

    def _retention_days(self) -> int:
        from backend.app.db.models.admin_config import AdminConfig
        cfg = self._db.execute(select(AdminConfig).limit(1)).scalar_one_or_none()
        return cfg.guest_retention_days if cfg else DEFAULT_RETENTION_DAYS

    def purge_expired_guests(self) -> dict:
        """Purge unclaimed anonymous users older than the retention window.

        Commits per guest (crash-safe); a failing guest is logged and
        skipped so it is retried on the next run.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days())
        candidates = self._db.execute(
            select(User).where(
                User.is_anonymous.is_(True),
                User.guest_claimed_by.is_(None),
                User.guest_purged_at.is_(None),
                User.created_at < cutoff,
            )
        ).scalars().all()
        purged = 0
        failed = 0
        for guest in candidates:
            try:
                self.purge_guest(guest)
                self._db.commit()
                purged += 1
            except Exception:
                logger.exception(
                    "Guest purge failed user=%s — continuing with next guest",
                    guest.id,
                )
                self._db.rollback()
                failed += 1
        return {"candidates": len(candidates), "purged": purged, "failed": failed}

    def purge_guest(self, guest: User) -> dict:
        """Delete one guest's storage objects + content rows; keep the users
        row as a tombstone (guest_purged_at stamped).

        Kept on purpose: users row (id, timestamps, legal fields),
        legal acceptance rows, guest_abuse_events, funnel_events,
        guest_devices.  Deleted: storage objects, structured_resumes,
        job_descriptions, generation_runs, tailored_documents,
        candidate_profiles, guest_entitlements.

        Does NOT commit — the caller owns the transaction.
        """
        from backend.app.db.models.candidate_profile import CandidateProfile
        from backend.app.db.models.generation_run import GenerationRun
        from backend.app.db.models.job_description import JobDescription
        from backend.app.db.models.structured_resume import StructuredResume
        from backend.app.db.models.tailored_document import TailoredDocument

        uid = guest.id

        resumes = self._db.execute(
            select(StructuredResume).where(StructuredResume.user_id == uid)
        ).scalars().all()
        docs = self._db.execute(
            select(TailoredDocument).where(TailoredDocument.user_id == uid)
        ).scalars().all()
        runs = self._db.execute(
            select(GenerationRun).where(GenerationRun.user_id == uid)
        ).scalars().all()
        jds = self._db.execute(
            select(JobDescription).where(JobDescription.user_id == uid)
        ).scalars().all()
        profiles = self._db.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == uid)
        ).scalars().all()

        # Storage objects: templates + generated artifacts + debug JSON.
        # delete_run_objects tolerates missing objects (logs and continues).
        storage_keys: list[str] = [r.source_file_url for r in resumes]
        for d in docs:
            storage_keys += [
                d.resume_docx_url, d.resume_pdf_url,
                d.cover_letter_docx_url, d.cover_letter_pdf_url,
            ]
        for run in runs:
            storage_keys.append(f"documents/{uid}/generated/{run.id}/debug.json")
        storage_keys = [k for k in storage_keys if k]
        if storage_keys:
            self._storage_service().delete_run_objects(storage_keys)

        # Content rows — children before parents (documents reference runs,
        # profiles reference resumes).
        for obj in docs:
            self._db.delete(obj)
        for obj in runs:
            self._db.delete(obj)
        for obj in jds:
            self._db.delete(obj)
        for obj in profiles:
            self._db.delete(obj)
        for obj in resumes:
            self._db.delete(obj)

        ent = self._db.get(GuestEntitlement, uid)
        if ent is not None:
            self._db.delete(ent)

        now = datetime.now(timezone.utc)
        guest.guest_purged_at = now
        guest.updated_at = now
        self._db.flush()

        counts = {
            "resumes_deleted": len(resumes),
            "job_descriptions_deleted": len(jds),
            "generation_runs_deleted": len(runs),
            "documents_deleted": len(docs),
            "profiles_deleted": len(profiles),
        }
        logger.info(
            "Guest purged user=%s resumes=%d jds=%d runs=%d docs=%d "
            "profiles=%d storage_objects=%d",
            uid, counts["resumes_deleted"], counts["job_descriptions_deleted"],
            counts["generation_runs_deleted"], counts["documents_deleted"],
            counts["profiles_deleted"], len(storage_keys),
        )
        return counts


# ── Scheduler entry points (module-level so APScheduler can serialize) ─────

def run_stale_reservation_release(database_url: str, settings: Settings) -> int:
    """Scheduled job: release stale reservations (every 15 minutes)."""
    from backend.app.db.session import get_session_factory
    db = get_session_factory(database_url)()
    try:
        released = GuestCleanupService(db, settings).release_stale_reservations()
        db.commit()
        logger.info("Guest cleanup run: released %d stale reservation(s)", released)
        return released
    except Exception:
        db.rollback()
        logger.exception("Guest cleanup: stale reservation release failed")
        return 0
    finally:
        db.close()


def run_retention_purge(database_url: str, settings: Settings) -> dict:
    """Scheduled job: daily purge run.

    Per the implementation plan, every purge run first releases stale
    reservations, then purges unclaimed guests past the retention window.
    """
    from backend.app.db.session import get_session_factory
    db = get_session_factory(database_url)()
    try:
        service = GuestCleanupService(db, settings)
        released = service.release_stale_reservations()
        db.commit()
        result = service.purge_expired_guests()  # commits per guest
        logger.info(
            "Guest retention purge run: released=%d candidates=%d purged=%d "
            "failed=%d",
            released, result["candidates"], result["purged"], result["failed"],
        )
        return result
    except Exception:
        db.rollback()
        logger.exception("Guest cleanup: retention purge run failed")
        return {"candidates": 0, "purged": 0, "failed": 0}
    finally:
        db.close()
