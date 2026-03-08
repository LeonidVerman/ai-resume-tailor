"""Backward-compatibility shim.

Implementation moved to tailor.core_generation.mechanism_taxonomy.
All imports from this path continue to work unchanged.
"""

from tailor.core_generation.mechanism_taxonomy import *  # public names
# Re-export private names accessed directly by external callers (tests, writer_packet)
from tailor.core_generation.mechanism_taxonomy import (
    _get_canonical_phrase,
    _normalize_for_dedup,
    _ARCH_KEYWORDS,
    _STRATEGIC_KEYWORDS,
    _OPERATIONAL_KEYWORDS,
)
