"""Deterministic validator for Phase 2 (Writer) output.

Checks that the resume and cover letter produced by the LLM satisfy the
constraints encoded in the WriterPacket.  All matching is done via simple
substring / token heuristics — no NLP.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Mechanism synonym table
# key: short canonical anchor  ->  acceptable variants (all lowercase)
# ---------------------------------------------------------------------------
_MECHANISM_SYNONYMS: dict[str, list[str]] = {
    "horizontal scaling": [
        "horizontal scaling",
        "horizontally scaled",
        "horizontal scale-out",
        "horizontally scaling",
        "horizontal scalability",
    ],
    "read replicas": [
        "read replica",
        "read replicas",
        "replica routing",
        "database read replica",
        "read-replica",
        "replicas for read",
    ],
    "caching": [
        "caching layer",
        "caching layers",
        "multi-layer caching",
        "multiple caching",
        "transactional cache",
        "tiered caching",
        "cache pressure",
        "redis cache",
        "in-memory cache",
        "multi-layer cache",
    ],
    "async messaging": [
        "async messaging",
        "asynchronous messaging",
        "message queue",
        "message-driven",
        "event-driven",
        "async message",
        "decoupled via queue",
        "queue-based",
        "message broker",
        "async queue",
    ],
    "redis": [
        "redis session",
        "session validation via redis",
        "distributed session validation",
        "redis-backed session",
        "redis",
        "session via redis",
    ],
    "stateless": [
        "stateless service",
        "stateless services",
        "stateless design",
        "stateless",
        "stateless architecture",
    ],
    "idempotent": [
        "idempotent processing",
        "idempotent",
        "retry-safe",
        "idempotency",
        "idempotent design",
    ],
}

# ---------------------------------------------------------------------------
# Metric variant table
# key: canonical metric string -> acceptable alternatives (all lowercase)
# ---------------------------------------------------------------------------
_METRIC_VARIANTS: dict[str, list[str]] = {
    "1M+": ["1m+", "1 million+", "over 1 million", "1m+ users", "million users"],
    "25%": ["25%", "25 percent", "25-percent"],
    "20%": ["20%", "20 percent", "20-percent"],
    "top-5": ["top-5", "top 5", "#1"],
    "#1": ["#1", "number one", "top-5", "top 5", "number 1"],
}

# Section headers for resume parsing.
_RESUME_SECTION_HEADERS: frozenset[str] = frozenset(
    {
        "Experience", "Education", "Technical Skills", "Skills",
        "Certifications", "Projects", "Publications", "Summary",
        "Professional Summary", "Awards", "References", "Volunteer",
    }
)

# Stop-words for token-based mechanism matching.
_STOP_WORDS: frozenset[str] = frozenset(
    {"for", "the", "and", "via", "with", "of", "to", "in", "by", "a", "an",
     "get", "db", "from", "on", "at", "as", "its", "our"}
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_phase2_output(
    writer_packet: dict,
    resume: str,
    cover_letter: str,
    current_date: str,
) -> dict:
    """Check Phase 2 output against WriterPacket constraints.

    Returns a ValidationReport dict with keys: ok, errors, warnings, stats.
    ``ok`` is True only when there are no errors.
    """
    errors: list[str] = []
    warnings: list[str] = []

    density = writer_packet.get("density_targets", {})
    top_n: int = density.get("top_roles", 2)
    bullet_min: int = density.get("bullets_per_top_role_min", 4)
    bullet_max: int = density.get("bullets_per_top_role_max", 6)
    mechanisms_min: int = density.get("mechanisms_in_top_roles_min", 4)

    # Parse resume structure once
    roles = _parse_roles(resume)
    skills_text = _extract_skills_section(resume)
    top_roles_text = _get_top_roles_text(roles, top_n)
    combined_top = "\n".join(top_roles_text)

    # --- 1. Metrics preservation ---
    metrics_found: list[str] = []
    missing_metrics: list[str] = []
    for metric in writer_packet.get("must_keep_metrics", []):
        if _metric_found(metric, resume):
            metrics_found.append(metric)
        else:
            missing_metrics.append(metric)
    if missing_metrics:
        errors.append(f"Missing required metrics: {', '.join(missing_metrics)}")

    # --- 2. Architecture materialisation ---
    mechanisms_found: list[str] = []
    missing_mechanisms: list[str] = []
    for mechanism in writer_packet.get("must_surface_mechanisms", []):
        if _mechanism_found(mechanism, combined_top):
            mechanisms_found.append(mechanism)
        else:
            missing_mechanisms.append(mechanism)

    if len(mechanisms_found) < mechanisms_min:
        errors.append(
            f"Insufficient mechanisms in top {top_n} roles: "
            f"found {len(mechanisms_found)}, required {mechanisms_min}. "
            f"Missing examples: {', '.join(missing_mechanisms[:3]) or 'none listed'}"
        )

    # Each top role must have >= 2 mechanisms
    for i, role_text in enumerate(top_roles_text):
        role_header = roles[i][0] if i < len(roles) else f"Role {i+1}"
        role_mech_count = sum(
            1
            for m in writer_packet.get("must_surface_mechanisms", [])
            if _mechanism_found(m, role_text)
        )
        if role_mech_count < 2:
            errors.append(
                f"Top role {i+1} ({role_header!r}) has only {role_mech_count} "
                f"mechanism(s); at least 2 required"
            )

    # --- 3. Bullet density ---
    top_role_bullet_counts: dict[str, int] = {}
    for i, (role_header, bullets) in enumerate(roles[:top_n]):
        count = len(bullets)
        top_role_bullet_counts[role_header] = count
        if count < bullet_min:
            errors.append(
                f"Top role {i+1} ({role_header!r}) has {count} bullet(s); "
                f"minimum is {bullet_min}"
            )
        elif count > bullet_max:
            warnings.append(
                f"Top role {i+1} ({role_header!r}) has {count} bullet(s); "
                f"maximum is {bullet_max}"
            )

    # --- 4. Required skills retention ---
    missing_required_skills: list[str] = []
    for skill in writer_packet.get("must_include_skills", []):
        if skill.lower() not in skills_text.lower():
            missing_required_skills.append(skill)
    if missing_required_skills:
        errors.append(f"Missing required skills: {', '.join(missing_required_skills)}")

    # --- 5. Unsafe JD nouns ---
    allowed_pool_lower = {s.lower() for s in writer_packet.get("allowed_skill_pool", [])}
    unsafe_terms_found: list[str] = []
    for term in writer_packet.get("unsafe_jd_nouns", []):
        term_lower = term.lower()
        if term_lower in resume.lower() and term_lower not in allowed_pool_lower:
            unsafe_terms_found.append(term)
    if unsafe_terms_found:
        errors.append(
            f"Unsafe JD nouns found in resume: {', '.join(unsafe_terms_found)}"
        )

    # --- 6. Date correctness ---
    if current_date and current_date not in cover_letter:
        errors.append(f"Cover letter does not contain CURRENT_DATE: {current_date!r}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "metrics_found": metrics_found,
            "mechanisms_found": mechanisms_found,
            "missing_mechanisms": missing_mechanisms,
            "missing_required_skills": missing_required_skills,
            "unsafe_terms_found": unsafe_terms_found,
            "top_role_bullet_counts": top_role_bullet_counts,
        },
    }


# ---------------------------------------------------------------------------
# Resume parsing helpers
# ---------------------------------------------------------------------------

def _parse_roles(resume: str) -> list[tuple[str, list[str]]]:
    """Parse resume into [(role_header, [bullet_texts])] for the Experience section.

    Role headers: lines containing '|' that do not start with '-' or '•'.
    Bullets: lines starting with '- ' or '• '.
    """
    lines = resume.split("\n")

    # Locate Experience section
    exp_start: int | None = None
    exp_end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "Experience":
            exp_start = i + 1
        elif exp_start is not None and s in _RESUME_SECTION_HEADERS and s != "Experience":
            exp_end = i
            break

    if exp_start is None:
        return []

    roles: list[tuple[str, list[str]]] = []
    current_header: str | None = None
    current_bullets: list[str] = []

    for line in lines[exp_start:exp_end]:
        s = line.strip()
        if not s:
            continue
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            if current_header is not None:
                roles.append((current_header, current_bullets))
            current_header = s
            current_bullets = []
        elif current_header is not None:
            if s.startswith("- "):
                current_bullets.append(s[2:].strip())
            elif s.startswith("• "):
                current_bullets.append(s[2:].strip())

    if current_header is not None:
        roles.append((current_header, current_bullets))

    return roles


def _get_top_roles_text(roles: list[tuple[str, list[str]]], top_n: int) -> list[str]:
    result: list[str] = []
    for header, bullets in roles[:top_n]:
        result.append(header + "\n" + "\n".join(bullets))
    return result


def _extract_skills_section(resume: str) -> str:
    """Return the raw text of the Technical Skills (or Skills) section."""
    lines = resume.split("\n")
    in_skills = False
    skill_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if s in ("Technical Skills", "Skills"):
            in_skills = True
            continue
        if in_skills and s in _RESUME_SECTION_HEADERS:
            break
        if in_skills and s:
            skill_lines.append(s)
    return "\n".join(skill_lines)


# ---------------------------------------------------------------------------
# Matching helpers
# ---------------------------------------------------------------------------

def _metric_found(metric: str, text: str) -> bool:
    """Return True if the metric or any of its known variants appears in text."""
    text_lower = text.lower()
    # Direct match
    if metric.lower() in text_lower:
        return True
    # Variant table
    for variants in _METRIC_VARIANTS.values():
        if metric.lower() in (v.lower() for v in variants):
            return any(v.lower() in text_lower for v in variants)
    return False


def _mechanism_found(mechanism: str, text: str) -> bool:
    """Return True if the mechanism (or a synonym) appears in text.

    Strategy:
    1. Direct substring match of the full mechanism string.
    2. Synonym table lookup by finding a matching canonical key.
    3. Token fallback: majority of meaningful tokens present.
    """
    text_lower = text.lower()
    mechanism_lower = mechanism.lower()

    # 1. Direct
    if mechanism_lower in text_lower:
        return True

    # 2. Synonym table — find which canonical key this mechanism belongs to
    for canonical, variants in _MECHANISM_SYNONYMS.items():
        if canonical in mechanism_lower or any(v in mechanism_lower for v in variants):
            if any(v in text_lower for v in variants):
                return True
            break

    # 3. Token fallback
    tokens = [
        w for w in re.split(r"\W+", mechanism_lower)
        if w and w not in _STOP_WORDS and len(w) > 3
    ]
    if not tokens:
        return False
    matched = sum(1 for t in tokens if t in text_lower)
    return matched >= max(1, round(len(tokens) * 0.6))
