"""
backend/app/services/storage_service.py

Storage service — manages upload and retrieval of resume templates and
generated DOCX/PDF files via the S3-compatible StorageClient.

Key naming convention
---------------------
  documents/{user_id}/templates/{resume_id}.docx
  documents/{user_id}/generated/{run_id}/resume.docx
  documents/{user_id}/generated/{run_id}/resume.pdf
  documents/{user_id}/generated/{run_id}/cover_letter.docx
  documents/{user_id}/generated/{run_id}/cover_letter.pdf

Responsibilities
----------------
- Upload raw bytes (DOCX or PDF) with a deterministic object key
- Return raw bytes for a given key (always-proxy pattern)
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
    Upload and retrieve documents from object storage.

    Dependencies
    ------------
    storage_client: StorageClient or LocalStorageClient — wraps storage API.
    """

    def __init__(self, storage_client) -> None:
        self._storage = storage_client

    # ── Upload ─────────────────────────────────────────────────────────────

    def upload_resume_template(self, user_id: str, resume_id: str, data: bytes) -> str:
        """Upload a resume template DOCX; returns the storage object key."""
        key = f"{_DOCUMENT_PREFIX}/{user_id}/templates/{resume_id}.docx"
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded resume template key=%s", key)
        return key

    def upload_resume_docx(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload resume DOCX; returns the storage object key."""
        key = self._generated_key(user_id, run_id, "resume.docx")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded resume.docx key=%s", key)
        return key

    def upload_resume_pdf(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload resume PDF; returns the storage object key."""
        key = self._generated_key(user_id, run_id, "resume.pdf")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded resume.pdf key=%s", key)
        return key

    def upload_cover_letter_docx(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload cover letter DOCX; returns the storage object key."""
        key = self._generated_key(user_id, run_id, "cover_letter.docx")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded cover_letter.docx key=%s", key)
        return key

    def upload_cover_letter_pdf(self, user_id: str, run_id: str, data: bytes) -> str:
        """Upload cover letter PDF; returns the storage object key."""
        key = self._generated_key(user_id, run_id, "cover_letter.pdf")
        self._storage.upload_bytes(data, key)
        logger.info("Uploaded cover_letter.pdf key=%s", key)
        return key

    # ── Download ───────────────────────────────────────────────────────────

    def get_bytes(self, key: str) -> bytes:
        """Return the raw bytes for the given object key."""
        return self._storage.get_bytes(key)

    def get_signed_url(self, key: str, expires_in: int = _URL_EXPIRY_SECONDS) -> str:
        """Return a pre-signed download URL for the given object key."""
        return self._storage.get_signed_url(key, expires_in=expires_in)

    # ── Delete ─────────────────────────────────────────────────────────────

    def delete_run_objects(self, keys: list[str]) -> None:
        """Delete generated objects by their explicit storage keys.

        Accepts the keys stored in tailored_document.*_url columns rather than
        recomputing them from IDs — this keeps deletion correct regardless of
        whether the table's PK type has changed since the objects were uploaded.
        """
        for key in keys:
            if not key:
                continue
            try:
                self._storage.delete_object(key)
            except Exception as exc:
                logger.warning("Failed to delete %s: %s", key, exc)

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _generated_key(user_id: str, run_id: str, filename: str) -> str:
        return f"{_DOCUMENT_PREFIX}/{user_id}/generated/{run_id}/{filename}"
