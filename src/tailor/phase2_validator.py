"""Deterministic validator for Phase 2 (Writer) output.

Checks that the resume and cover letter produced by the LLM satisfy the
constraints encoded in the WriterPacket.  All matching is done via simple
substring / keyword heuristics — no NLP.

Density enforcement is priority-based (high / medium / low), not chronological.
Thin or non-repositioning roles (e.g. independent contractor, AI evaluator,
roles with very little source material) receive a relaxed "thin_override"
enforcement tier.  The first two non-thin roles are guaranteed at least
high-priority density enforcement regardless of plan priority assignment.
"""

from __future__ import annotations

import re
from datetime import date

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
# Thin / non-repositioning role detection
# ---------------------------------------------------------------------------

# Role name substrings that indicate a thin or non-repositioning role.
# Keep this list configurable — add / remove patterns as needed.
_THIN_ROLE_NAME_PATTERNS: frozenset[str] = frozenset({
    "independent contractor",
    "contractor",
    "freelance",
    "consultant",
    "mercor",
    "ai lab",
    "model evaluation",
    "train and evaluate",
    "ai evaluator",
    "evaluation",
})

# Source-content thresholds for thinness detection.
_THIN_ROLE_SOURCE_BULLET_THRESHOLD: int = 2    # < N bullets in master resume
_THIN_ROLE_SOURCE_CHAR_THRESHOLD: int = 150    # < M chars of content in master resume

# Density minimums applied when a role is classified as thin_override.
_THIN_OVERRIDE_BULLET_MIN: int = 2
_THIN_OVERRIDE_MECHANISM_MIN: int = 0

# Number of top non-thin roles guaranteed high-priority density enforcement.
_TOP_REPOSITIONING_ROLES_COUNT: int = 2

# Very-old-role relaxation: thin_override roles that ended this many years ago
# (or more) require only 1 bullet instead of _THIN_OVERRIDE_BULLET_MIN.
VERY_OLD_ROLE_YEARS: int = 15

# Regexes for parsing end dates out of role header strings.
# Matches "Present" or "Current" (case-insensitive).
_PRESENT_RE: re.Pattern[str] = re.compile(r"\b(present|current)\b", re.IGNORECASE)
# Matches any 4-digit year in the 1900s or 2000s.
_YEAR_4_RE: re.Pattern[str] = re.compile(r"\b((?:19|20)\d{2})\b")

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
    now: date | None = None,
) -> dict:
    """Check Phase 2 output against WriterPacket constraints.

    Returns a ValidationReport dict with keys: ok, errors, warnings, stats.
    ``ok`` is True only when there are no errors.

    Density is enforced per effective priority:
    - thin_override: 2 bullets / 0 mechanisms (relaxed for thin/non-repositioning roles)
      Exception: thin_override roles that ended > VERY_OLD_ROLE_YEARS ago require only
      1 bullet (very-old-role relaxation).
    - high / medium / low: per density_targets in writer_packet
    The first _TOP_REPOSITIONING_ROLES_COUNT non-thin roles are promoted to
    at least high priority for density enforcement.

    Parameters
    ----------
    now:
        Reference date for very-old-role detection.  Defaults to today.
    """
    if now is None:
        now = date.today()

    errors: list[str] = []
    warnings: list[str] = []

    role_priorities: dict[str, str] = writer_packet.get("role_priorities", {})
    role_source_counts: dict[str, int] = writer_packet.get("role_source_bullet_counts", {})
    role_source_char_counts: dict[str, int] = writer_packet.get("role_source_char_counts", {})
    jd_is_delivery_oriented: bool = writer_packet.get("jd_is_delivery_oriented", False)
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

    # Compute effective priorities with thin-role override + top-K promotion
    effective_priorities = _compute_effective_priorities(
        roles, role_priorities, role_source_counts, role_source_char_counts,
        jd_is_delivery_oriented,
    )

    # --- 1. Metrics preservation (unchanged) ---
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

    prev_display_priority: str | None = None
    for role_header, bullets in roles:
        effective_priority, thin_reason = effective_priorities.get(role_header, ("low", ""))

        # Warn when thin override is applied
        if effective_priority == "thin_override":
            plan_priority = _match_role_priority(role_header, role_priorities) or "low"
            warnings.append(
                f"Role {role_header!r} treated as thin override for density checks "
                f"(plan priority: {plan_priority}; reason: {thin_reason})"
            )

        bullet_count = len(bullets)
        role_bullet_counts[role_header] = bullet_count

        if effective_priority == "thin_override":
            # Very old thin roles (ended > VERY_OLD_ROLE_YEARS ago) need only 1 bullet.
            if _is_very_old_role(role_header, now):
                min_bullets = 1
            else:
                min_bullets = _THIN_OVERRIDE_BULLET_MIN
            mech_required = _THIN_OVERRIDE_MECHANISM_MIN
        else:
            min_bullets = bullet_min_map.get(effective_priority, 1)
            mech_required = mechanism_min_map.get(effective_priority, 0)

        if bullet_count < min_bullets:
            errors.append(
                f"Role {role_header!r} ({effective_priority} priority) has {bullet_count} "
                f"bullet(s); minimum is {min_bullets}"
            )
        elif bullet_count > 6 and effective_priority == "high":
            warnings.append(
                f"Role {role_header!r} has {bullet_count} bullets; "
                f"consider trimming to 6"
            )

        mech_count = sum(1 for b in bullets if _bullet_has_mechanism(b))
        role_mechanism_counts[role_header] = mech_count
        if mech_required > 0 and mech_count < mech_required:
            errors.append(
                f"Role {role_header!r} ({effective_priority} priority) has {mech_count} "
                f"mechanism(s) in bullets; minimum is {mech_required}"
            )

        # Advisory: high-priority role appearing after a medium-priority role.
        display_priority = "thin" if effective_priority == "thin_override" else effective_priority
        if prev_display_priority == "medium" and display_priority == "high":
            warnings.append(
                f"Advisory: high-priority role {role_header!r} appears after a "
                f"medium-priority role; verify planner intent"
            )
        prev_display_priority = display_priority

    # --- 3. Required skills retention (unchanged) ---
    missing_required_skills: list[str] = []
    for skill in writer_packet.get("must_include_skills", []):
        if skill.lower() not in skills_text.lower():
            missing_required_skills.append(skill)
    if missing_required_skills:
        errors.append(f"Missing required skills: {', '.join(missing_required_skills)}")

    # --- 4. Unsafe JD nouns (unchanged) ---
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

    # --- 5. Date correctness (unchanged) ---
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
# Thin / non-repositioning role helpers
# ---------------------------------------------------------------------------

def _is_thin_or_non_repositioning_role(
    role_name: str,
    source_bullet_count: int,
    source_char_count: int,
    jd_is_delivery_oriented: bool,
) -> tuple[bool, str]:
    """Return (is_thin, reason) for a role.

    Checks (in order):
    A) Role name contains a known thin/non-repositioning pattern.
    B) Source content is thin (few bullets, or very short total text).
    C) Optional — JD is delivery-oriented while role name is evaluation/training.
    """
    name_lower = role_name.lower()

    # A) Name heuristics
    for pattern in _THIN_ROLE_NAME_PATTERNS:
        if pattern in name_lower:
            return True, f"name matches pattern '{pattern}'"

    # B) Content thinness (only when source data is actually known)
    if source_bullet_count < _THIN_ROLE_SOURCE_BULLET_THRESHOLD and source_bullet_count < 99:
        return True, (
            f"only {source_bullet_count} source bullet(s) "
            f"(threshold: {_THIN_ROLE_SOURCE_BULLET_THRESHOLD})"
        )
    if 0 < source_char_count < _THIN_ROLE_SOURCE_CHAR_THRESHOLD:
        return True, (
            f"only {source_char_count} source chars "
            f"(threshold: {_THIN_ROLE_SOURCE_CHAR_THRESHOLD})"
        )

    # C) JD mismatch: delivery-oriented JD + evaluation/training role name
    if jd_is_delivery_oriented:
        eval_patterns = {
            "evaluation", "evaluator", "training", "train", "label", "annotation",
        }
        if any(kw in name_lower for kw in eval_patterns):
            return True, "evaluation/training role in delivery-oriented JD"

    return False, ""


def _compute_effective_priorities(
    roles: list[tuple[str, list[str]]],
    role_priorities: dict[str, str],
    role_source_counts: dict[str, int],
    role_source_char_counts: dict[str, int],
    jd_is_delivery_oriented: bool,
) -> dict[str, tuple[str, str]]:
    """Return {role_header: (effective_priority, thin_reason)} for all roles.

    - Thin roles receive "thin_override".
    - The first _TOP_REPOSITIONING_ROLES_COUNT non-thin roles are promoted to
      at least "high" priority for density enforcement.
    - Remaining non-thin roles keep their plan priority.
    """
    # First pass: classify each role
    thin_flags: dict[str, tuple[bool, str]] = {}
    for role_header, _ in roles:
        source_count = _find_source_bullet_count(role_header, role_source_counts)
        source_chars = _find_source_char_count(role_header, role_source_char_counts)
        is_thin, reason = _is_thin_or_non_repositioning_role(
            role_header, source_count, source_chars, jd_is_delivery_oriented,
        )
        thin_flags[role_header] = (is_thin, reason)

    # Identify first K non-thin roles for high-priority promotion
    non_thin_headers = [h for h, _ in roles if not thin_flags[h][0]]
    top_repositioning: set[str] = set(non_thin_headers[:_TOP_REPOSITIONING_ROLES_COUNT])

    # Second pass: assign effective priority
    result: dict[str, tuple[str, str]] = {}
    for role_header, _ in roles:
        is_thin, reason = thin_flags[role_header]
        if is_thin:
            result[role_header] = ("thin_override", reason)
        elif role_header in top_repositioning:
            result[role_header] = ("high", "")
        else:
            plan_priority = _match_role_priority(role_header, role_priorities) or "low"
            result[role_header] = (plan_priority, "")

    return result


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
    """Fuzzy-match a resume role header to a priority from the plan."""
    header_lower = role_header.lower()
    for plan_name, priority in role_priorities.items():
        if plan_name.lower() in header_lower:
            return priority
    return None


def _find_source_bullet_count(
    role_header: str,
    role_source_counts: dict[str, int],
) -> int:
    """Return source bullet count; 99 if unknown (prevents false thin detection)."""
    header_lower = role_header.lower()
    for plan_name, count in role_source_counts.items():
        if plan_name.lower() in header_lower:
            return count
    return 99


def _find_source_char_count(
    role_header: str,
    role_source_char_counts: dict[str, int],
) -> int:
    """Return source char count; 99999 if unknown (prevents false thin detection)."""
    header_lower = role_header.lower()
    for plan_name, count in role_source_char_counts.items():
        if plan_name.lower() in header_lower:
            return count
    return 99999


# ---------------------------------------------------------------------------
# Very-old-role helpers
# ---------------------------------------------------------------------------

def _parse_role_end_year(role_header: str) -> int | None:
    """Extract end year from a role header date range.

    Handles formats such as:
    - "Senior Engineer | Acme Corp | 2017 - 2020"
    - "QA Engineer | ZAO Comita | 2004 - 2009"
    - "Engineer | Beta Corp | Nov 2009 – Jan 2011"
    - "Engineer | Corp | 2020 - Present"

    Returns None when the role is ongoing (Present/Current) or when no
    parseable date range is found.  Uses the last ``|``-delimited segment
    as the date field, then collects all 4-digit years; the last year is
    treated as the end year.
    """
    # Focus on the trailing date segment (after the last pipe).
    segments = role_header.split("|")
    date_part = segments[-1].strip() if len(segments) >= 2 else role_header

    # Ongoing roles — no end year.
    if _PRESENT_RE.search(date_part):
        return None

    years = [int(m.group()) for m in _YEAR_4_RE.finditer(date_part)]
    if not years:
        return None

    return years[-1]  # last year in the range is the end year


def _is_very_old_role(role_header: str, now: date) -> bool:
    """Return True if the role ended more than VERY_OLD_ROLE_YEARS years ago."""
    end_year = _parse_role_end_year(role_header)
    if end_year is None:
        return False
    return (now.year - end_year) > VERY_OLD_ROLE_YEARS
