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

import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

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
_THIN_OVERRIDE_BULLET_MIN: int = 1
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
    - thin_override: 1 bullet / 0 mechanisms (relaxed for thin/non-repositioning roles)
    - high / medium / low: per density_targets in writer_packet
    The first _TOP_REPOSITIONING_ROLES_COUNT non-thin roles are promoted to
    at least high priority for density enforcement.

    Very-old-role relaxation (applies to ALL effective priorities):
    Roles that ended more than VERY_OLD_ROLE_YEARS years ago are relaxed to
    min_bullets=1 and mech_required=0, regardless of their effective priority.

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
    raw_headers = _extract_raw_role_headers(resume)
    roles = _parse_roles(resume)
    skills_text = _extract_skills_section(resume)
    role_date_lines = _parse_role_date_lines(resume)
    role_parsing_debug = [
        {"raw": h, "normalized": normalize_role_header(h)}
        for h in raw_headers
    ]

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

        date_hint = role_date_lines.get(role_header, "")
        very_old = _is_very_old_role(role_header, now, date_hint=date_hint)

        if very_old:
            # Very old roles (ended > VERY_OLD_ROLE_YEARS ago) need only 1 bullet,
            # regardless of their effective priority (thin or otherwise).
            min_bullets = 1
            mech_required = 0
            if effective_priority != "thin_override":
                warnings.append(
                    f"Role {role_header!r} ({effective_priority} priority) is very old "
                    f"(>={VERY_OLD_ROLE_YEARS}y ago); relaxed to min 1 bullet"
                )
        elif effective_priority == "thin_override":
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
            "role_parsing_debug": role_parsing_debug,
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
            current_header = normalize_role_header(s)
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


def _parse_role_date_lines(resume: str) -> dict[str, str]:
    """Return {role_header: date_line_text} for the Experience section.

    When the LLM puts the date range on a separate line (rather than inline
    in the header), ``_parse_roles`` silently drops it.  This function
    captures that dropped line so very-old-role detection can still use it.

    For roles that already carry the date inline in the header, the returned
    value is an empty string (the header itself is sufficient).
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
        return {}

    result: dict[str, str] = {}
    current_header: str | None = None
    awaiting_date: bool = False

    for line in lines[exp_start:exp_end]:
        s = line.strip()
        if not s:
            continue
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            current_header = normalize_role_header(s)
            result[current_header] = ""
            awaiting_date = True  # look for a date on the very next non-empty line
        elif current_header is not None and awaiting_date:
            if _is_date_line(s):
                result[current_header] = s
            awaiting_date = False  # whether or not the line was a date, stop looking

    return result


def _extract_raw_role_headers(resume: str) -> list[str]:
    """Return raw (unnormalized) role headers from the Experience section.

    Used only to build ``role_parsing_debug`` in the ValidationReport.
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
    headers: list[str] = []
    for line in lines[exp_start:exp_end]:
        s = line.strip()
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            headers.append(s)
    return headers


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

def normalize_role_header(header: str) -> str:
    """Normalise a role header for consistent cross-module matching.

    Steps
    -----
    1. Strip leading/trailing whitespace.
    2. Normalise spaces around ``|`` separators (``"A|B"`` → ``"A | B"``).
    3. If the last pipe-separated segment contains a trailing location suffix
       (a comma that appears *after* the last ``|``), strip everything from
       that comma onward.  E.g. ``"Dev | Corp, Vancouver"`` → ``"Dev | Corp"``.
    4. Collapse any remaining multiple internal spaces.

    This is a **pure normalisation** function — it does *not* lowercase the
    result so that display strings remain readable.  Callers that need
    case-insensitive comparison should apply ``.lower()`` themselves.
    """
    if not header:
        return header
    h = header.strip()
    # Normalise pipe spacing
    h = " | ".join([p.strip() for p in h.split("|")])
    # Strip trailing location suffix after last pipe (if any)
    if "|" in h:
        last_pipe = h.rfind("|")
        last_comma = h.rfind(",")
        if last_comma > last_pipe:
            h = h[:last_comma].strip()
    # Collapse internal whitespace
    h = " ".join(h.split())
    return h


def _roles_match(name_a: str, name_b: str) -> bool:
    """Return True if two role name strings refer to the same role.

    Handles abbreviated vs. full multi-part job titles produced when the
    Phase 1 planner truncates secondary / tertiary title components.

    Example match::

        "VP / Director of Software Development | CardinalChain Software Inc"
        "VP / Director of Software Development / Lead Software Developer | CardinalChain Software Inc"

    Algorithm
    ---------
    1. Direct substring test — covers the common case where one name is a
       clean prefix/suffix of the other (e.g. plan name vs. name + date).
    2. Pipe-part test — split on ``|`` and compare the first (title) and
       last (company) segment independently in both directions.  This
       handles compound titles that the LLM abbreviated.
    """
    a = normalize_role_header(name_a).lower()
    b = normalize_role_header(name_b).lower()

    # Case 1: direct substring match (original behaviour)
    if a in b or b in a:
        return True

    # Case 2: pipe-part match for abbreviated multi-part titles
    a_parts = [p.strip() for p in a.split("|")]
    b_parts = [p.strip() for p in b.split("|")]

    if len(a_parts) >= 2 and len(b_parts) >= 2:
        title_match = a_parts[0] in b_parts[0] or b_parts[0] in a_parts[0]
        if title_match:
            # Company may not be the last segment when a date is appended
            # (e.g. "Title | Company | 2020 - Present").  Check a's company
            # against all non-title segments of b, and vice versa.
            a_company = a_parts[-1]
            company_match = (
                any(a_company in bp or bp in a_company for bp in b_parts[1:])
                or any(bp in a_parts[-1] or a_parts[-1] in bp for bp in b_parts[1:])
            )
            if company_match:
                return True

    return False


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
    for plan_name, priority in role_priorities.items():
        if _roles_match(plan_name, role_header):
            return priority
    return None


def _find_source_bullet_count(
    role_header: str,
    role_source_counts: dict[str, int],
) -> int:
    """Return source bullet count; 99 if unknown (prevents false thin detection)."""
    for plan_name, count in role_source_counts.items():
        if _roles_match(plan_name, role_header):
            return count
    return 99


def _find_source_char_count(
    role_header: str,
    role_source_char_counts: dict[str, int],
) -> int:
    """Return source char count; 99999 if unknown (prevents false thin detection)."""
    for plan_name, count in role_source_char_counts.items():
        if _roles_match(plan_name, role_header):
            return count
    return 99999


# ---------------------------------------------------------------------------
# Very-old-role helpers
# ---------------------------------------------------------------------------

def _parse_role_end_year(role_header: str, date_hint: str = "") -> int | None:
    """Extract end year from a role header date range (or a separate date hint line).

    Handles formats such as:
    - "Senior Engineer | Acme Corp | 2017 - 2020"       (date inline in header)
    - "QA Engineer | ZAO Comita | 2004 - 2009"
    - "Engineer | Beta Corp | Nov 2009 – Jan 2011"
    - "Engineer | Corp | 2020 - Present"
    - header="Software Engineer | Borland", date_hint="2004 - 2008"  (date on separate line)

    Returns None when the role is ongoing (Present/Current) or when no
    parseable date range is found in either the header or the hint.
    The last 4-digit year found is treated as the end year.
    """
    # Check both the trailing header segment and the separate date hint.
    segments = role_header.split("|")
    date_part = segments[-1].strip() if len(segments) >= 2 else role_header

    # If "Present" / "Current" appears in either source, treat as ongoing.
    if _PRESENT_RE.search(date_part) or (date_hint and _PRESENT_RE.search(date_hint)):
        return None

    # Collect years from the header date segment first, then fall back to hint.
    years = [int(m.group()) for m in _YEAR_4_RE.finditer(date_part)]
    if not years and date_hint:
        years = [int(m.group()) for m in _YEAR_4_RE.finditer(date_hint)]
    if not years:
        return None

    return years[-1]  # last year in the range is the end year


def _is_very_old_role(role_header: str, now: date, date_hint: str = "") -> bool:
    """Return True if the role ended more than VERY_OLD_ROLE_YEARS years ago."""
    end_year = _parse_role_end_year(role_header, date_hint=date_hint)
    if end_year is None:
        return False
    return (now.year - end_year) > VERY_OLD_ROLE_YEARS
