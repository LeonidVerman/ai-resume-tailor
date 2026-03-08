"""
backend/app/schemas/common.py

Shared Pydantic utilities and reusable models used across schema modules.
"""

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

# ── Base config ────────────────────────────────────────────────────────────

class APIModel(BaseModel):
    """Base for all API request/response models.

    - from_attributes=True so models can be built from SQLAlchemy ORM objects.
    - populate_by_name=True so both alias and field name are accepted.
    """
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# ── Generic paginated list ─────────────────────────────────────────────────

T = TypeVar("T")


class PaginatedResponse(APIModel, Generic[T]):
    items: list[T]
    total: int
    limit: int
    offset: int


# ── Common field shapes ────────────────────────────────────────────────────

class TimestampFields(APIModel):
    created_at: datetime


class MutableTimestampFields(APIModel):
    created_at: datetime
    updated_at: datetime


# ── Standard error envelope ────────────────────────────────────────────────

class ErrorDetail(APIModel):
    code: str
    message: str


class ErrorResponse(APIModel):
    error: ErrorDetail
