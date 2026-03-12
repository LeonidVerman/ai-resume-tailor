"""
backend/app/services/candidate_profile_normalizer.py

Normalises candidate profile JSONB documents:
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

_CLAIM_BOUNDARIES_LISTS = ("security_auth",)


def _normalise_section(section: dict, field_names: tuple) -> None:
    for field in field_names:
        if field in section and isinstance(section[field], list):
            section[field] = _normalise_list(section[field])


# ── Public API ───────────────────────────────────────────────────────────────

def normalize_candidate_profile(profile: dict) -> dict:
    """
    Main entry point.

    1. Migrate v1.x → v2.0 structure (idempotent for v2.0 profiles).
    2. Normalise all list fields (token-to-readable, split, dedup).
    3. Return the updated dict (deep copy; original is not mutated).
    """
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
