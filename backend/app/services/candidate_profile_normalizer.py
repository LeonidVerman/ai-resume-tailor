"""
backend/app/services/candidate_profile_normalizer.py

Normalises candidate profile JSONB documents.  Two passes are applied in order:

Pass 1 — sanitization (runs first, before any other processing):
  - strips accidental boundary quotes and escaped-quote artifacts
  - strips trailing comma artifacts left by copy/paste
  - preserves internal (meaningful) quotes
  - drops empty strings from list fields after sanitization

Pass 2 — token normalization:
  - converts underscore-separated machine tokens to human-readable phrases
  - splits malformed combined items (several space-separated tokens each
    containing underscores) into individual list entries
  - deduplicates list entries (case-insensitive, preserves first occurrence)
  - migrates v1.x structure (authz_authn_experience,
    scalability_reliability_patterns) into the v2.0 layout
"""

from __future__ import annotations

import copy
import re

# ── Pass 1: String sanitization ──────────────────────────────────────────────

def sanitize_profile_string(value: str) -> str:
    """
    Conservative sanitization for a single candidate profile string value.

    Rules applied in order:
    1. Strip surrounding whitespace.
    2. Strip trailing comma artifacts (e.g. "foo," → "foo").
    3. Strip escaped-quote boundary artifacts: \"foo\", foo\", \"foo.
    4. Strip trailing comma again (exposed after step 3).
    5. Strip raw boundary quotes:
       - Both ends, no internal quote: "foo" → foo   (Case C)
       - Both ends, with internal quotes: "He said "hi"" → He said "hi"  (Case D)
       - Leading only, no other quotes: "foo → foo   (Case A)
       - Trailing only, no other quotes: foo" → foo  (Case B)
       - Internal only: OAuth2 "style → preserved  (Case E)
    6. Strip trailing comma one final time (exposed after step 5).
    7. Strip whitespace.

    Internal quotes are never removed.  Non-string values are returned
    unchanged so this function is safe to call on any scalar.
    """
    if not isinstance(value, str):
        return value

    s = value.strip()
    if not s:
        return s

    # Step 2: trailing comma artifact
    if s.endswith(","):
        s = s[:-1].strip()

    # Step 3: escaped-quote boundary artifacts  (\")
    changed = True
    while changed:
        changed = False
        if s.startswith('\\"'):
            s = s[2:].strip()
            changed = True
        if s.endswith('\\"'):
            s = s[:-2].strip()
            changed = True

    # Step 4: trailing comma re-exposed
    if s.endswith(","):
        s = s[:-1].strip()

    # Step 5: raw boundary quotes
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        # Remove outer wrapper; the inner content is the real value
        s = s[1:-1].strip()
    elif s.startswith('"') and '"' not in s[1:]:
        # Unbalanced leading quote only
        s = s[1:].strip()
    elif s.endswith('"') and '"' not in s[:-1]:
        # Unbalanced trailing quote only
        s = s[:-1].strip()
    # else: internal quote(s) only — preserve as-is (Case E)

    # Step 6: trailing comma one last time
    if s.endswith(","):
        s = s[:-1].strip()

    return s


def sanitize_profile_value(value):
    """
    Recursively sanitize a profile value.

    - str  → sanitize_profile_string; returned as-is if empty
    - list → sanitize each element; drop empty strings; recurse into dicts
    - dict → sanitize_profile_object
    - other (int, bool, None, …) → pass through unchanged
    """
    if isinstance(value, str):
        return sanitize_profile_string(value)
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, str):
                cleaned = sanitize_profile_string(item)
                if cleaned:
                    result.append(cleaned)
            elif isinstance(item, dict):
                result.append(sanitize_profile_object(item))
            else:
                result.append(item)
        return result
    if isinstance(value, dict):
        return sanitize_profile_object(value)
    return value


def sanitize_profile_object(obj: dict) -> dict:
    """Return a new dict with all nested string values sanitized."""
    return {key: sanitize_profile_value(val) for key, val in obj.items()}


# ── Proper-noun / acronym map ────────────────────────────────────────────────

_PROPER_NOUN_MAP: dict[str, str] = {
    "redis": "Redis",
    "kafka": "Kafka",
    "rabbitmq": "RabbitMQ",
    "aws": "AWS",
    "oauth2": "OAuth2",
    "oidc": "OIDC",
    "saml": "SAML",
    "jwt": "JWT",
    "rbac": "RBAC",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "api": "API",
    "llm": "LLM",
    "rag": "RAG",
    "ai": "AI",
    "sql": "SQL",
    "rest": "REST",
    "graphql": "GraphQL",
    "websockets": "WebSockets",
    "grpc": "gRPC",
    "ci": "CI",
    "cd": "CD",
    "cicd": "CI/CD",
    "vba": "VBA",
    "css": "CSS",
    "html": "HTML",
    "xml": "XML",
    "json": "JSON",
    "yaml": "YAML",
}

_CONNECTOR_WORDS = frozenset({
    "a", "an", "the", "and", "or", "via", "from", "in", "on",
    "to", "for", "of", "with", "by", "without", "between", "as",
    "into", "through", "under", "over",
})

_ALL_CAPS_RE = re.compile(r"^[A-Z][A-Z0-9]*$")


# ── Token helpers ────────────────────────────────────────────────────────────

def _word_case(word: str, position: int) -> str:
    """Apply correct casing to a single word extracted from an underscore token."""
    lower = word.lower()
    if lower in _PROPER_NOUN_MAP:
        return _PROPER_NOUN_MAP[lower]
    if _ALL_CAPS_RE.match(word):
        return word  # preserve existing ALL-CAPS (e.g. "RAG", "LLM")
    if position == 0:
        return lower.capitalize()
    return lower  # connectors and regular words stay lowercase


def _token_to_readable(token: str) -> str:
    """Convert an underscore-separated machine token to a human-readable phrase."""
    if "_" not in token:
        return token
    parts = [p for p in token.split("_") if p]
    return " ".join(_word_case(p, i) for i, p in enumerate(parts))


def _is_combined(item: str) -> bool:
    """Return True if item is space-separated where ≥2 parts contain underscores."""
    if " " not in item or "_" not in item:
        return False
    parts = item.split()
    return sum(1 for p in parts if "_" in p) >= 2


def _normalise_item(item: str) -> str:
    return _token_to_readable(item.strip())


def _normalise_list(items: list) -> list[str]:
    """
    1. Split combined items (≥2 space-separated underscore-tokens) into entries.
    2. Convert each item through _normalise_item.
    3. Deduplicate case-insensitively, preserving first occurrence.
    """
    expanded: list[str] = []
    for item in items:
        if not isinstance(item, str):
            continue
        if _is_combined(item):
            expanded.extend(p for p in item.split() if p)
        else:
            expanded.append(item)

    seen: set[str] = set()
    result: list[str] = []
    for item in expanded:
        normalised = _normalise_item(item)
        key = normalised.lower()
        if key not in seen:
            seen.add(key)
            result.append(normalised)
    return result


# ── V1.x → V2.0 migration ────────────────────────────────────────────────────

def _migrate_v1_to_v2(profile: dict) -> dict:
    """
    Migrate v1.0/v1.1 profile structure to v2.0.  Idempotent: if the old
    top-level keys are absent the function is a no-op for those fields.

    Moves:
    - authz_authn_experience.implemented  → technical_skills.security_auth_patterns
    - authz_authn_experience.notes        → claim_boundaries.security_auth
    - authz_authn_experience.scalability_challenges
                                          → experience_highlights[0].constraints_and_tradeoffs
    - root scalability_reliability_patterns
                                          → technical_skills.scalability_reliability_patterns
    """
    profile = copy.deepcopy(profile)

    ts = profile.setdefault("technical_skills", {})
    claim = profile.setdefault("claim_boundaries", {})
    highlights = profile.get("experience_highlights", [])

    authz = profile.pop("authz_authn_experience", None)
    if authz and isinstance(authz, dict):
        implemented = authz.get("implemented") or []
        notes = authz.get("notes") or []
        scalability_challenges = authz.get("scalability_challenges") or []

        existing_sap = ts.get("security_auth_patterns") or []
        ts["security_auth_patterns"] = existing_sap + implemented

        existing_sec_auth = claim.get("security_auth") or []
        claim["security_auth"] = existing_sec_auth + notes

        if scalability_challenges and highlights:
            h = highlights[0]
            existing_ct = h.get("constraints_and_tradeoffs") or []
            h["constraints_and_tradeoffs"] = existing_ct + scalability_challenges

    srp = profile.pop("scalability_reliability_patterns", None)
    if srp and isinstance(srp, list):
        existing_srp = ts.get("scalability_reliability_patterns") or []
        ts["scalability_reliability_patterns"] = existing_srp + srp

    profile["candidate_profile_version"] = "2.0"
    return profile


# ── Field layout for normalisation ───────────────────────────────────────────

_TOP_LEVEL_LISTS = ("role_fit_themes",)

_DOMAINS_LISTS = ("primary", "secondary")

_EXPERIENCE_LISTS = (
    "impact", "team_context", "architecture_patterns",
    "constraints_and_tradeoffs", "skills_applied", "security_auth_patterns",
)

_TECHNICAL_SKILLS_LISTS = (
    "languages", "backend_systems", "datastores", "infra_devops",
    "api_patterns", "async_messaging", "observability",
    "security_auth_patterns", "scalability_reliability_patterns",
)

_LEADERSHIP_SCOPE_LISTS = ("style_keywords",)
_LEADERSHIP_LISTS = ("practices", "risk_management")

_AI_TOOLING_LISTS = ("hands_on_tools", "usage_patterns", "principles", "concepts_familiarity")

_CONSTRAINTS_LISTS = ("work_context", "communication", "resume_constraint")

_CLAIM_BOUNDARIES_LISTS = ("security_auth", "domain_limits", "employment_constraints")


def _normalise_section(section: dict, field_names: tuple) -> None:
    for field in field_names:
        if field in section and isinstance(section[field], list):
            section[field] = _normalise_list(section[field])


# ── Public API ───────────────────────────────────────────────────────────────

def normalize_candidate_profile(profile: dict) -> dict:
    """
    Main entry point.

    1. Sanitize all string values (boundary quotes, escaped quotes, comma artifacts).
    2. Migrate v1.x → v2.0 structure (idempotent for v2.0 profiles).
    3. Normalise all list fields (token-to-readable, split, dedup).
    4. Return the updated dict (original is not mutated).
    """
    profile = sanitize_profile_object(profile)
    profile = _migrate_v1_to_v2(profile)

    for field in _TOP_LEVEL_LISTS:
        if field in profile and isinstance(profile[field], list):
            profile[field] = _normalise_list(profile[field])

    if isinstance(profile.get("domains"), dict):
        _normalise_section(profile["domains"], _DOMAINS_LISTS)

    for h in profile.get("experience_highlights", []):
        if isinstance(h, dict):
            _normalise_section(h, _EXPERIENCE_LISTS)

    if isinstance(profile.get("technical_skills"), dict):
        _normalise_section(profile["technical_skills"], _TECHNICAL_SKILLS_LISTS)

    leadership = profile.get("leadership", {})
    if isinstance(leadership, dict):
        if isinstance(leadership.get("scope"), dict):
            _normalise_section(leadership["scope"], _LEADERSHIP_SCOPE_LISTS)
        _normalise_section(leadership, _LEADERSHIP_LISTS)

    if isinstance(profile.get("ai_tooling_practice"), dict):
        _normalise_section(profile["ai_tooling_practice"], _AI_TOOLING_LISTS)

    if isinstance(profile.get("constraints_and_preferences"), dict):
        _normalise_section(profile["constraints_and_preferences"], _CONSTRAINTS_LISTS)

    if isinstance(profile.get("claim_boundaries"), dict):
        _normalise_section(profile["claim_boundaries"], _CLAIM_BOUNDARIES_LISTS)

    return profile
