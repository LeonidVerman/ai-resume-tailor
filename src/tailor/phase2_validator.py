"""Backward-compatibility shim.

Implementation moved to tailor.core_generation.phase2_validator.
All imports from this path continue to work unchanged.
"""

from tailor.core_generation.phase2_validator import *  # public names (constants, functions)
# Re-export private names accessed directly by external callers (tests, writer_packet)
from tailor.core_generation.phase2_validator import (
    _build_ledger_index,
    _check_ledger_honesty,
    _check_ledger_requirement,
    _is_thin_or_non_repositioning_role,
    _roles_match,
    _bullet_has_arch_mechanism,
    _bullet_has_mechanism,
    _compute_effective_priorities,
    _extract_earlier_roles_block,
    _extract_raw_role_headers,
    _extract_skills_section,
    _extract_summary,
    _is_date_line,
    _match_role_priority,
    _metric_found,
    _parse_roles,
    _role_found_in_output,
    _tokenize_skills_section,
    _TOP_REPOSITIONING_ROLES_COUNT,
    _MECHANISM_KEYWORDS,
    _HIGH_RISK_TOOL_LEXICON,
    _LEADERSHIP_VERBS,
    _DELIVERY_VOCAB,
    _THIN_ROLE_NAME_PATTERNS,
    _THIN_ROLE_SOURCE_BULLET_THRESHOLD,
    _THIN_ROLE_SOURCE_CHAR_THRESHOLD,
    _THIN_OVERRIDE_BULLET_MIN,
    _THIN_OVERRIDE_MECHANISM_MIN,
)
