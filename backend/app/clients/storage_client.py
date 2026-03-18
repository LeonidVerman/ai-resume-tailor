"""
backend/app/clients/storage_client.py

S3-compatible object storage client (targets Cloudflare R2).

Responsibilities
----------------
- upload bytes / file-like objects
- generate pre-signed download URLs
- delete objects
- list objects (for future admin/maintenance use)

The interface is intentionally generic (no R2-specific logic) so it works
with any S3-compatible endpoint (AWS S3, MinIO, R2, etc.).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import BinaryIO

logger = logging.getLogger(__name__)


# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class UploadResult:
    bucket: str
    key: str
    url: str        # public URL (if bucket is public) or empty string
    etag: str = ""


# ── Client ─────────────────────────────────────────────────────────────────

class StorageClient:
    """
    Thin S3-compatible storage client backed by boto3.

    Parameters
    ----------
    endpoint_url:
        S3-compatible endpoint (e.g. ``https://<account>.r2.cloudflarestorage.com``).
        Leave empty for standard AWS S3.
    access_key_id / secret_access_key:
        Credentials.
    bucket:
        Default bucket name.
    region:
        AWS / R2 region string.
    public_base_url:
        Optional public CDN prefix for constructing object URLs without signing.
        If set, ``upload_bytes`` returns ``<public_base_url>/<key>`` as the URL.
    """

    def __init__(
        self,
        endpoint_url: str = "",
        access_key_id: str = "",
        secret_access_key: str = "",
        bucket: str = "",
        region: str = "auto",
        public_base_url: str = "",
    ) -> None:
        self._endpoint_url = endpoint_url or None
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._bucket = bucket
        self._region = region
        self._public_base_url = public_base_url.rstrip("/")
        self._s3: object | None = None  # lazy boto3 client

    # ── Internal ───────────────────────────────────────────────────────────

    def _get_s3(self):
        """Return (and lazily create) the boto3 S3 client."""
        if self._s3 is None:
            import boto3
            kwargs: dict = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key
            self._s3 = boto3.client("s3", **kwargs)
        return self._s3

    def _resolve_bucket(self, bucket: str | None) -> str:
        b = bucket or self._bucket
        if not b:
            raise ValueError("No bucket specified and no default bucket configured.")
        return b

    # ── Public API ─────────────────────────────────────────────────────────

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> UploadResult:
        """Upload raw bytes to storage under the given key."""
        import io
        return self.upload_fileobj(
            io.BytesIO(data),
            key,
            content_type=content_type,
            bucket=bucket,
        )

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> UploadResult:
        """Upload a file-like object to storage."""
        resolved_bucket = self._resolve_bucket(bucket)
        s3 = self._get_s3()

        logger.debug("Uploading to s3://%s/%s (%s)", resolved_bucket, key, content_type)

        response = s3.put_object(
            Bucket=resolved_bucket,
            Key=key,
            Body=fileobj,
            ContentType=content_type,
        )
        etag = response.get("ETag", "").strip('"')

        if self._public_base_url:
            url = f"{self._public_base_url}/{key}"
        else:
            url = ""

        logger.debug("Upload complete key=%s etag=%s", key, etag)
        return UploadResult(bucket=resolved_bucket, key=key, url=url, etag=etag)

    def get_signed_url(
        self,
        key: str,
        *,
        expires_in: int = 3600,
        bucket: str | None = None,
    ) -> str:
        """Generate a pre-signed download URL valid for ``expires_in`` seconds."""
        resolved_bucket = self._resolve_bucket(bucket)
        s3 = self._get_s3()
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": resolved_bucket, "Key": key},
            ExpiresIn=expires_in,
        )
        logger.debug("Generated signed URL for key=%s expires_in=%ds", key, expires_in)
        return url

    def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        """Delete an object from storage."""
        resolved_bucket = self._resolve_bucket(bucket)
        s3 = self._get_s3()
        s3.delete_object(Bucket=resolved_bucket, Key=key)
        logger.debug("Deleted s3://%s/%s", resolved_bucket, key)

    def get_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        """Download an object and return its raw bytes."""
        resolved_bucket = self._resolve_bucket(bucket)
        s3 = self._get_s3()
        logger.debug("Downloading s3://%s/%s", resolved_bucket, key)
        response = s3.get_object(Bucket=resolved_bucket, Key=key)
        return response["Body"].read()

    def object_exists(self, key: str, *, bucket: str | None = None) -> bool:
        """Return True if an object with the given key exists."""
        import botocore.exceptions
        resolved_bucket = self._resolve_bucket(bucket)
        s3 = self._get_s3()
        try:
            s3.head_object(Bucket=resolved_bucket, Key=key)
            return True
        except botocore.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise


# ── Local filesystem client ────────────────────────────────────────────────

class LocalStorageClient:
    """
    Local filesystem storage backend with the same public API as StorageClient.

    Stores files under ``root_path/{key}``.  Parent directories are created
    automatically on upload.  Intended for development and testing only.
    """

    def __init__(self, root_path: str) -> None:
        import os
        self._root = os.path.abspath(root_path)

    def _full_path(self, key: str) -> str:
        import os
        # Normalise forward slashes on Windows.
        return os.path.join(self._root, *key.split("/"))

    def upload_bytes(
        self,
        data: bytes,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> UploadResult:
        """Write bytes to the local filesystem."""
        import io
        return self.upload_fileobj(io.BytesIO(data), key, content_type=content_type, bucket=bucket)

    def upload_fileobj(
        self,
        fileobj: BinaryIO,
        key: str,
        *,
        content_type: str = "application/octet-stream",
        bucket: str | None = None,
    ) -> UploadResult:
        """Write a file-like object to the local filesystem."""
        import os
        path = self._full_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(fileobj.read())
        logger.debug("LocalStorage: wrote %s", path)
        return UploadResult(bucket="local", key=key, url="", etag="")

    def get_bytes(self, key: str, *, bucket: str | None = None) -> bytes:
        """Read an object from the local filesystem and return its raw bytes."""
        path = self._full_path(key)
        with open(path, "rb") as f:
            return f.read()

    def get_signed_url(self, key: str, *, expires_in: int = 3600, bucket: str | None = None) -> str:
        """Not supported for local storage."""
        raise NotImplementedError("Signed URLs are not supported by LocalStorageClient")

    def delete_object(self, key: str, *, bucket: str | None = None) -> None:
        """Delete a file from the local filesystem (no-op if missing)."""
        import os
        path = self._full_path(key)
        try:
            os.remove(path)
            logger.debug("LocalStorage: deleted %s", path)
        except FileNotFoundError:
            pass

    def object_exists(self, key: str, *, bucket: str | None = None) -> bool:
        """Return True if the file exists on the local filesystem."""
        import os
        return os.path.isfile(self._full_path(key))


# ── Factory ────────────────────────────────────────────────────────────────

def make_storage_client_from_settings() -> "StorageClient | LocalStorageClient":
    """Create a storage client from application settings."""
    from backend.app.config import get_settings
    s = get_settings()
    if s.storage_type == "local":
        return LocalStorageClient(root_path=s.storage_local_path)
    return StorageClient(
        endpoint_url=s.storage_endpoint,
        access_key_id=s.storage_access_key_id,
        secret_access_key=s.storage_secret_access_key,
        bucket=s.storage_bucket,
    )
