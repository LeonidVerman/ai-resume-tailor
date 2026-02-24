"""Deterministic validator for Phase 2 (Writer) output.

Checks that the resume and cover letter produced by the LLM satisfy the
constraints encoded in the WriterPacket.  All matching is done via simple
substring / keyword heuristics — no NLP.

Density enforcement is priority-based (high / medium / low), not chronological.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Mechanism keyword set — a bullet counts as containing a mechanism if it
# includes at least one of these strings (case-insensitive substring match).
# ---------------------------------------------------------------------------
_MECHANISM_KEYWORDS: frozenset[str] = frozenset({
    # Architectural patterns
    "horizontal scaling",
    "horizontally scaled",
    "caching",
    "read replica",
    "async messaging",
    "asynchronous messaging",
    "message queue",
    "message broker",
    "event-driven",
    "replication",
    "transactional cache",
    "ci/cd",
    "containerization",
    "containerized",
    "database optimization",
    "api integration",
    "fault tolerance",
    "distributed system",
    # Concrete infrastructure / tools
    "kafka",
    "redis",
    "aws",
    "docker",
    "kubernetes",
    "rest api",
    "restful",
    "websocket",
})

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

    Bullet density and mechanism density are enforced per-role based on the
    priority field in writer_packet.role_priorities, NOT by chronological order.
    """
    errors: list[str] = []
    warnings: list[str] = []

    role_priorities: dict[str, str] = writer_packet.get("role_priorities", {})
    role_source_counts: dict[str, int] = writer_packet.get("role_source_bullet_counts", {})
    density = writer_packet.get("density_targets", {})
    bullet_min_map: dict[str, int] = density.get(
        "bullet_min_by_priority", {"high": 4, "medium": 3, "low": 1}
    )
    mechanism_min_map: dict[str, int] = density.get(
        "mechanism_min_by_priority", {"high": 2, "medium": 1, "low": 0}
    )

    # Parse resume structure once
    roles = _parse_roles(resume)
    skills_text = _extract_skills_section(resume)

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

    # --- 2. Priority-based bullet density + mechanism density ---
    role_bullet_counts: dict[str, int] = {}
    role_mechanism_counts: dict[str, int] = {}

    prev_priority: str | None = None
    for role_header, bullets in roles:
        priority = _match_role_priority(role_header, role_priorities) or "low"
        bullet_count = len(bullets)
        role_bullet_counts[role_header] = bullet_count

        # Thin-role safeguard: downgrade minimum by 1 if source has < 2 bullets.
        source_count = _find_source_bullet_count(role_header, role_source_counts)
        min_bullets = bullet_min_map.get(priority, 1)
        if source_count < 2:
            min_bullets = max(1, min_bullets - 1)

        if bullet_count < min_bullets:
            errors.append(
                f"Role {role_header!r} ({priority} priority) has {bullet_count} "
                f"bullet(s); minimum is {min_bullets}"
            )
        elif bullet_count > 6 and priority in ("high", "medium"):
            warnings.append(
                f"Role {role_header!r} has {bullet_count} bullets; "
                f"consider trimming to 6"
            )

        # Mechanism count check
        mech_required = mechanism_min_map.get(priority, 0)
        mech_count = sum(1 for b in bullets if _bullet_has_mechanism(b))
        role_mechanism_counts[role_header] = mech_count
        if mech_required > 0 and mech_count < mech_required:
            errors.append(
                f"Role {role_header!r} ({priority} priority) has {mech_count} "
                f"mechanism(s) in bullets; minimum is {mech_required}"
            )

        # Advisory: high-priority role appearing after a medium-priority role.
        if prev_priority == "medium" and priority == "high":
            warnings.append(
                f"Advisory: high-priority role {role_header!r} appears after a "
                f"medium-priority role; verify planner intent"
            )
        prev_priority = priority

    # --- 3. Required skills retention ---
    missing_required_skills: list[str] = []
    for skill in writer_packet.get("must_include_skills", []):
        if skill.lower() not in skills_text.lower():
            missing_required_skills.append(skill)
    if missing_required_skills:
        errors.append(f"Missing required skills: {', '.join(missing_required_skills)}")

    # --- 4. Unsafe JD nouns ---
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

    # --- 5. Date correctness ---
    if current_date and current_date not in cover_letter:
        errors.append(f"Cover letter does not contain CURRENT_DATE: {current_date!r}")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "stats": {
            "metrics_found": metrics_found,
            "missing_required_skills": missing_required_skills,
            "unsafe_terms_found": unsafe_terms_found,
            "role_bullet_counts": role_bullet_counts,
            "role_mechanism_counts": role_mechanism_counts,
        },
    }


# ---------------------------------------------------------------------------
# Resume parsing helpers
# ---------------------------------------------------------------------------

def _parse_roles(resume: str) -> list[tuple[str, list[str]]]:
    """Parse resume into [(role_header, [bullet_texts])] for the Experience section.

    Role headers: lines containing '|' that do not start with '-' or '•'.
    Bullets: lines starting with '- ' or '• ', OR plain-text content lines
    (the LLM sometimes omits the dash prefix).  Date-only lines are skipped.
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
            elif not _is_date_line(s):
                # Plain-text content line — LLM omitted the bullet marker.
                current_bullets.append(s)

    if current_header is not None:
        roles.append((current_header, current_bullets))

    return roles


_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _is_date_line(s: str) -> bool:
    """Return True if the line looks like a date/tenure line (e.g. 'Nov 2025 - Present')."""
    return bool(_YEAR_RE.search(s)) and "|" not in s and len(s) < 60


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
    if metric.lower() in text_lower:
        return True
    for variants in _METRIC_VARIANTS.values():
        if metric.lower() in (v.lower() for v in variants):
            return any(v.lower() in text_lower for v in variants)
    return False


def _bullet_has_mechanism(text: str) -> bool:
    """Return True if bullet text contains any mechanism keyword."""
    text_lower = text.lower()
    return any(kw in text_lower for kw in _MECHANISM_KEYWORDS)


def _match_role_priority(
    role_header: str,
    role_priorities: dict[str, str],
) -> str | None:
    """Fuzzy-match a resume role header to a priority from the plan.

    Plan role names are often a prefix of resume headers (which append dates).
    Returns the priority string or None if no match found.
    """
    header_lower = role_header.lower()
    for plan_name, priority in role_priorities.items():
        if plan_name.lower() in header_lower:
            return priority
    return None


def _find_source_bullet_count(
    role_header: str,
    role_source_counts: dict[str, int],
) -> int:
    """Return source bullet count for a resume role header.

    Returns 99 (high) if the role is not found, so no thin-role downgrade is
    applied when the source count is unknown.
    """
    header_lower = role_header.lower()
    for plan_name, count in role_source_counts.items():
        if plan_name.lower() in header_lower:
            return count
    return 99
