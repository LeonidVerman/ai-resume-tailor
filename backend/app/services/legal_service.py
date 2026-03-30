"""
backend/app/services/legal_service.py

Legal consent service — reads document registry, records acceptance events,
and checks user compliance.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.db.models.legal import (
    LegalAcceptanceEvent,
    LegalDocument,
    UserLegalStatus,
)
from backend.app.schemas.legal import (
    LegalCurrentResponse,
    LegalDocumentAcceptanceInfo,
    LegalDocumentInfo,
    LegalStatusResponse,
)

logger = logging.getLogger(__name__)

DOC_TYPE_TERMS = "terms_of_service"
DOC_TYPE_PRIVACY = "privacy_notice"


# ── Hashing helpers ───────────────────────────────────────────────────────────

def canonicalize_legal_text(text: str) -> str:
    """Normalize line endings and trim trailing whitespace per line."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text.strip() + "\n"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── Service ───────────────────────────────────────────────────────────────────

class LegalService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ── Document registry ─────────────────────────────────────────────────────

    def get_current_documents(self) -> LegalCurrentResponse:
        """Return the currently active terms and privacy documents."""
        terms = self._get_active_doc(DOC_TYPE_TERMS)
        privacy = self._get_active_doc(DOC_TYPE_PRIVACY)
        return LegalCurrentResponse(
            terms=_doc_to_info(terms),
            privacy=_doc_to_info(privacy),
        )

    def _get_active_doc(self, doc_type: str) -> LegalDocument:
        doc = (
            self._db.execute(
                select(LegalDocument)
                .where(LegalDocument.doc_type == doc_type)
                .where(LegalDocument.status == "active")
                .order_by(LegalDocument.effective_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if doc is None:
            raise RuntimeError(f"No active legal document found for type: {doc_type}")
        return doc

    def get_document_by_id(self, doc_id: int) -> LegalDocument | None:
        return self._db.get(LegalDocument, doc_id)

    # ── User status ───────────────────────────────────────────────────────────

    def get_user_status(self, user_id: str) -> LegalStatusResponse:
        """Return user's acceptance state for both current documents."""
        terms = self._get_active_doc(DOC_TYPE_TERMS)
        privacy = self._get_active_doc(DOC_TYPE_PRIVACY)
        status = self._get_or_none_status(user_id)

        terms_info = _build_acceptance_info(status, DOC_TYPE_TERMS, terms)
        privacy_info = _build_acceptance_info(status, DOC_TYPE_PRIVACY, privacy)

        compliant = (
            (terms_info.is_current or not terms.requires_reaccept)
            and (privacy_info.is_current or not privacy.requires_reaccept)
        )

        return LegalStatusResponse(
            compliant=compliant,
            terms=terms_info,
            privacy=privacy_info,
        )

    def is_compliant(self, user_id: str) -> bool:
        """Fast check — True if user has accepted both current required documents."""
        try:
            return self.get_user_status(user_id).compliant
        except Exception:
            return False

    # ── Acceptance ────────────────────────────────────────────────────────────

    def record_acceptance(
        self,
        user_id: str,
        terms_document_id: int,
        privacy_document_id: int,
        acceptance_method: str,
        source_surface: str,
        request: Request | None = None,
    ) -> None:
        """Validate docs and record two acceptance events in one transaction."""
        terms_doc = self.get_document_by_id(terms_document_id)
        privacy_doc = self.get_document_by_id(privacy_document_id)

        if terms_doc is None or terms_doc.doc_type != DOC_TYPE_TERMS or terms_doc.status != "active":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid or inactive terms document ID.")
        if privacy_doc is None or privacy_doc.doc_type != DOC_TYPE_PRIVACY or privacy_doc.status != "active":
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid or inactive privacy document ID.")

        ip: str | None = None
        ua: str | None = None
        request_id: str | None = None
        if request is not None:
            # Respect forwarded headers from reverse proxy
            ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else None)
            if ip and "," in ip:
                ip = ip.split(",")[0].strip()
            ua = request.headers.get("User-Agent")
            request_id = request.headers.get("X-Request-Id")

        now = datetime.now(timezone.utc)

        self._db.add(LegalAcceptanceEvent(
            user_id=user_id,
            doc_type=DOC_TYPE_TERMS,
            legal_document_id=terms_doc.id,
            version=terms_doc.version,
            content_sha256=terms_doc.content_sha256,
            accepted_at=now,
            acceptance_method=acceptance_method,
            source_surface=source_surface,
            ip_address=ip,
            user_agent=ua,
            request_id=request_id,
        ))
        self._db.add(LegalAcceptanceEvent(
            user_id=user_id,
            doc_type=DOC_TYPE_PRIVACY,
            legal_document_id=privacy_doc.id,
            version=privacy_doc.version,
            content_sha256=privacy_doc.content_sha256,
            accepted_at=now,
            acceptance_method=acceptance_method,
            source_surface=source_surface,
            ip_address=ip,
            user_agent=ua,
            request_id=request_id,
        ))

        # Upsert convenience cache
        status = self._get_or_none_status(user_id)
        if status is None:
            status = UserLegalStatus(user_id=user_id)
            self._db.add(status)
        status.accepted_terms_document_id = terms_doc.id
        status.accepted_terms_at = now
        status.accepted_privacy_document_id = privacy_doc.id
        status.accepted_privacy_at = now
        status.updated_at = now

        self._db.flush()

        logger.info(
            "legal_acceptance_recorded user_id=%s terms_doc_id=%s terms_version=%s "
            "privacy_doc_id=%s privacy_version=%s method=%s surface=%s",
            user_id, terms_doc.id, terms_doc.version,
            privacy_doc.id, privacy_doc.version,
            acceptance_method, source_surface,
        )

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_or_none_status(self, user_id: str) -> UserLegalStatus | None:
        return self._db.get(UserLegalStatus, user_id)


# ── Module-level helpers ──────────────────────────────────────────────────────

def _doc_to_info(doc: LegalDocument) -> LegalDocumentInfo:
    return LegalDocumentInfo(
        id=doc.id,
        doc_type=doc.doc_type,
        version=doc.version,
        title=doc.title,
        file_path=doc.file_path,
        content_sha256=doc.content_sha256,
        effective_at=doc.effective_at,
        requires_reaccept=doc.requires_reaccept,
    )


def _build_acceptance_info(
    status: UserLegalStatus | None,
    doc_type: str,
    current_doc: LegalDocument,
) -> LegalDocumentAcceptanceInfo:
    if status is None:
        return LegalDocumentAcceptanceInfo(accepted=False, is_current=False)

    if doc_type == DOC_TYPE_TERMS:
        accepted_id = status.accepted_terms_document_id
        accepted_at = status.accepted_terms_at
    else:
        accepted_id = status.accepted_privacy_document_id
        accepted_at = status.accepted_privacy_at

    if accepted_id is None:
        return LegalDocumentAcceptanceInfo(accepted=False, is_current=False)

    return LegalDocumentAcceptanceInfo(
        accepted=True,
        version=current_doc.version if accepted_id == current_doc.id else None,
        accepted_at=accepted_at,
        is_current=(accepted_id == current_doc.id),
    )
