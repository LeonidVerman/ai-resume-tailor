"""Core generation module — single-pass document tailoring."""

from .llm import (
    TailorResult,
    get_client,
    extract_metadata_ai,
    tailor_documents,
)

__all__ = [
    "TailorResult",
    "get_client",
    "extract_metadata_ai",
    "tailor_documents",
]
