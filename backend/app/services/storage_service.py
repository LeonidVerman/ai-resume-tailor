"""
backend/app/services/storage_service.py

Storage service — manages upload and retrieval of generated DOCX/PDF files
via the S3-compatible StorageClient (Phase 5).

Key naming convention
---------------------
  documents/{user_id}/{run_id}/resume.docx
  documents/{user_id}/{run_id}/resume.pdf
  documents/{user_id}/{run_id}/cover_letter.docx

Responsibilities
----------------
- Upload raw bytes (DOCX or PDF) with a deterministic object key
- Return signed download URLs
- Delete objects when a document record is deleted
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_DOCUMENT_PREFIX = "documents"
_URL_EXPIRY_SECONDS = 3600  # 1 hour


class StorageService:
    """
    Upload and retrieve generated documents from object storage.

    Dependencies
    ------------
    storage_client: StorageClient (Phase 5) — wraps boto3 S3-compatible API.
    """

    def __init__(self, storage_client) -> None:
        self._storage = storage_client

    # ── Upload ─────────────────────────────────────────────────────────────

    def upload_resume_docx(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload resume DOCX; returns the storage object key."""
        key = self._key(user_id, run_id, "resume.docx")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded resume.docx key=%s", key)
        return key

    def upload_resume_pdf(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload resume PDF; returns the storage object key."""
        key = self._key(user_id, run_id, "resume.pdf")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded resume.pdf key=%s", key)
        return key

    def upload_cover_letter_docx(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload cover letter DOCX; returns the storage object key."""
        key = self._key(user_id, run_id, "cover_letter.docx")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded cover_letter.docx key=%s", key)
        return key

    def upload_cover_letter_pdf(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload cover letter PDF; returns the storage object key."""
        key = self._key(user_id, run_id, "cover_letter.pdf")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded cover_letter.pdf key=%s", key)
        return key

    # ── Download ───────────────────────────────────────────────────────────

    def get_signed_url(self, key: str, expires_in: int = _URL_EXPIRY_SECONDS) -> str:
        """Return a pre-signed download URL for the given object key."""
        return self._storage.get_signed_url(key, expires_in=expires_in)

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete_run_objects(self, user_id: str, run_id: str) -> None:
        """Delete all objects associated with a generation run."""
        for filename in ("resume.docx", "resume.pdf", "cover_letter.docx", "cover_letter.pdf"):
            key = self._key(user_id, run_id, filename)
            try:
                self._storage.delete_object(key)
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", key, exc)

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _key(user_id: str, run_id: str, filename: str) -> str:
        return f"{_DOCUMENT_PREFIX}/{user_id}/{run_id}/{filename}"
