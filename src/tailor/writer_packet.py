"""Build a WriterPacket from a TailoringPlan + source documents.

The WriterPacket is a code-derived JSON object that makes Phase 2 more
deterministic by providing explicit, pre-computed constraints.  No LLM is
involved in its construction.
"""

import json
import re

# Known high-value metrics that must always be preserved regardless of plan.
_BASELINE_METRICS: list[str] = ["1M+", "25%", "20%", "top-5", "#1"]

# Keywords that indicate an integration or extensibility reframe.
_INTEGRATION_KEYWORDS: frozenset[str] = frozenset(
    {
        "integration", "extensibility", "extensible", "modular", "adapter",
        "middleware", "reusable", "ingestion", "etl", "pipeline", "framework",
        "plug-in", "plugin", "connector",
    }
)

# JD keywords that suggest production engineering delivery focus.
# Used to compute jd_is_delivery_oriented in the writer packet.
_DELIVERY_JD_KEYWORDS: frozenset[str] = frozenset({
    "microservices", "microservice",
    "api", "apis", "rest api",
    "cloud", "cloud-native",
    "production", "production-grade",
    "deployment", "deploy",
    "kubernetes", "k8s",
    "docker",
    "scalability", "scalable",
    "distributed system",
    "backend",
    "platform",
})

# Nouns that are dangerous to include unless explicitly present in sources.
_HARDCODED_UNSAFE_NOUNS: list[str] = [
    "low-code", "no-code", "asset discovery", "SNMP", "BGP", "OSPF",
    "ServiceNow", "Cisco", "YANG", "JSON Schema",
]

# Section headers used to delimit resume sections.
_RESUME_SECTION_HEADERS: frozenset[str] = frozenset(
    {
        "Experience", "Education", "Technical Skills", "Skills",
        "Certifications", "Projects", "Publications", "Summary",
        "Professional Summary", "Awards", "References", "Volunteer",
    }
)

# Regex for date-range lines in master resumes.
# Matches: "Nov 2024 – Sep 2025", "Jan 2001 – Sep 2003", "Aug 1999 – Present"
# Uses both en dash (–) and ASCII hyphen (-).
_DATE_LINE_RE: re.Pattern = re.compile(
    r"^[A-Za-z]{3}\s+\d{4}\s+[–\-]\s+(Present|[A-Za-z]{3}\s+\d{4})$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_writer_packet(
    plan: dict,
    candidate_profile_str: str,
    master_resume_str: str,
    job_description: str,
) -> dict:
    """Derive a WriterPacket from the TailoringPlan and source documents.

    All keys are required in the returned dict.  The packet is passed to the
    Phase 2 writer LLM as an explicit constraints envelope.
    """
    profile = _parse_json_safe(candidate_profile_str)

    jd_keywords_lower = _collect_jd_keywords(plan)
    all_source_skills = _collect_source_skills(profile, master_resume_str)

    must_keep_metrics = _build_must_keep_metrics(plan)
    # Build unsafe nouns first so must_surface_mechanisms can be filtered against them.
    unsafe_jd_nouns = _build_unsafe_jd_nouns(plan)
    must_surface_mechanisms = _build_must_surface_mechanisms(plan, profile, unsafe_jd_nouns)
    must_include_skills = _build_must_include_skills(plan, jd_keywords_lower, all_source_skills)
    allowed_skill_pool = _dedup_preserve_order(all_source_skills)
    do_not_add_terms = _build_do_not_add_terms(plan)
    integration_reframes = _build_integration_reframes(plan)
    role_priorities = _build_role_priorities(plan)
    role_stats = _parse_master_resume_role_stats(master_resume_str)
    role_source_bullet_counts = _build_role_source_bullet_counts(plan, role_stats)
    role_source_char_counts = _build_role_source_char_counts(plan, role_stats)
    jd_is_delivery_oriented = _compute_jd_is_delivery_oriented(plan)

    return {
        "role_level": plan.get("role_level", "senior"),
        "must_keep_metrics": must_keep_metrics,
        "must_surface_mechanisms": must_surface_mechanisms,
        "must_include_skills": must_include_skills,
        "allowed_skill_pool": allowed_skill_pool,
        "do_not_add_terms": do_not_add_terms,
        "unsafe_jd_nouns": unsafe_jd_nouns,
        "integration_extensibility_reframes": integration_reframes,
        "role_priorities": role_priorities,
        "role_source_bullet_counts": role_source_bullet_counts,
        "role_source_char_counts": role_source_char_counts,
        "jd_is_delivery_oriented": jd_is_delivery_oriented,
        "density_targets": {
            "bullet_min_by_priority": {"high": 4, "medium": 3, "low": 1},
            "mechanism_min_by_priority": {"high": 2, "medium": 1, "low": 0},
        },
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _build_must_keep_metrics(plan: dict) -> list[str]:
    metrics: set[str] = set(_BASELINE_METRICS)
    for role_entry in plan.get("resume_strategy", {}).get("experience", []):
        for m in role_entry.get("keep_metrics", []):
            metrics.add(_normalize_plan_metric(m))
    return sorted(metrics)


# Regex to extract the core numeric metric token from a longer phrase.
# Matches: #N, top-N, NM+, N%, Nx  (case-insensitive).
_METRIC_CORE_RE = re.compile(
    r"(?:#\d+|top-\d+|\d+[kKmMgGbB]\+|\d+[kKmMgGbB]%?|\d+\+?%)",
    re.IGNORECASE,
)


def _normalize_plan_metric(s: str) -> str:
    """Reduce a long plan keep_metric phrase to its core token.

    'N% some description'        -> 'N%'
    'NM+ some description'       -> 'NM+'
    '1M+ clients'                -> '1M+'
    'top-5 in Japan'             -> 'top-5'
    Short tokens (<= 10 chars)   -> returned unchanged.
    """
    if len(s) <= 10:
        return s
    m = _METRIC_CORE_RE.search(s)
    if m:
        return m.group()
    return s


def _build_must_surface_mechanisms(
    plan: dict,
    profile: dict,
    unsafe_nouns: list[str] | None = None,
) -> list[str]:
    """Collect architecture mechanisms from profile + plan evidence.

    Any entry that contains an unsafe JD noun as a substring (case-insensitive)
    is dropped to prevent a self-contradictory WriterPacket where the writer is
    simultaneously told "use this phrase verbatim" and "never use this term".
    """
    items: list[str] = []

    # Primary source: candidate_profile architecture_patterns
    for highlight in profile.get("experience_highlights", []):
        for pattern in highlight.get("architecture_patterns", []):
            if isinstance(pattern, str):
                items.append(pattern)

    # Secondary: scalability_reliability_patterns (normalized strings)
    for pattern in profile.get("scalability_reliability_patterns", []):
        if isinstance(pattern, str):
            # Convert underscore-separated keys to readable phrases
            readable = pattern.replace("_", " ")
            items.append(readable)

    # Tertiary: plan evidence_map safe_translation strings.
    # The LLM sometimes returns safe_translation as a plain string instead of
    # a list; handle both to avoid iterating over individual characters.
    for entry in plan.get("evidence_map", []):
        raw = entry.get("safe_translation", [])
        if isinstance(raw, str):
            if raw:
                items.append(raw)
        elif isinstance(raw, list):
            for translation in raw:
                if isinstance(translation, str) and translation:
                    items.append(translation)

    deduped = _dedup_preserve_order(items)

    if not unsafe_nouns:
        return deduped

    unsafe_lower = [n.lower() for n in unsafe_nouns if n]
    return [m for m in deduped if not any(u in m.lower() for u in unsafe_lower)]


def _build_must_include_skills(
    plan: dict,
    jd_keywords_lower: set[str],
    all_source_skills: list[str],
) -> list[str]:
    """Skills that exist in sources AND are called for by the JD."""
    skills: list[str] = []

    for skill in all_source_skills:
        skill_lower = skill.lower()
        # Exact match
        if skill_lower in jd_keywords_lower:
            skills.append(skill)
            continue
        # Substring match in either direction (e.g. "Kafka" <-> "Apache Kafka")
        for kw in jd_keywords_lower:
            if len(kw) > 2 and (kw in skill_lower or skill_lower in kw):
                skills.append(skill)
                break

    # Also include skills the plan explicitly promotes
    for skill in plan.get("resume_strategy", {}).get("skills", {}).get("promote_skills", []):
        skills.append(skill)

    return _dedup_preserve_order(skills)


def _build_do_not_add_terms(plan: dict) -> list[str]:
    terms: list[str] = []
    risk = plan.get("risk_checks", {})
    terms.extend(risk.get("do_not_invent", []))
    terms.extend(
        plan.get("resume_strategy", {}).get("skills", {}).get("do_not_add_skills", [])
    )
    return _dedup_case_insensitive(terms)


def _build_unsafe_jd_nouns(plan: dict) -> list[str]:
    terms: list[str] = list(_HARDCODED_UNSAFE_NOUNS)
    risk = plan.get("risk_checks", {})
    terms.extend(risk.get("likely_hallucination_traps", []))
    terms.extend(risk.get("do_not_invent", []))
    return _dedup_case_insensitive(terms)


def _build_role_priorities(plan: dict) -> dict[str, str]:
    """Return {role_name: priority} from resume_strategy.experience."""
    result: dict[str, str] = {}
    for role_entry in plan.get("resume_strategy", {}).get("experience", []):
        name = role_entry.get("role_name", "")
        priority = role_entry.get("priority", "low")
        if name and isinstance(priority, str):
            result[name] = priority
    return result


def _is_resume_date_line(s: str) -> bool:
    """Return True if the stripped line is a date-range line (e.g. 'Nov 2024 – Sep 2025')."""
    return bool(_DATE_LINE_RE.match(s))


def _parse_master_resume_role_stats(resume_text: str) -> dict[str, dict]:
    """Return {role_header: {"bullet_count": int, "char_count": int}} from master resume.

    Parses the Experience section only.  Two counting modes are used:

    Explicit bullet mode (primary):
        Count lines starting with ``-`` or ``•``.

    Fallback unmarked-bullet mode:
        When a role block has zero explicit bullets but has content lines,
        count each non-empty content line that is not a date-range line.
        This handles resumes that use plain sentences without bullet markers.

    char_count is the total character length of all non-empty content lines
    (excluding the role header itself), regardless of counting mode.
    """
    lines = resume_text.split("\n")
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

    stats: dict[str, dict] = {}
    current_header: str | None = None
    current_content_lines: list[str] = []

    def _commit() -> None:
        if current_header is None:
            return
        explicit = sum(
            1 for s in current_content_lines
            if s.startswith("- ") or s.startswith("• ")
        )
        if explicit > 0:
            bullet_count = explicit
        else:
            # Fallback: every non-date content line counts as a bullet
            bullet_count = sum(
                1 for s in current_content_lines
                if not _is_resume_date_line(s)
            )
        stats[current_header] = {
            "bullet_count": bullet_count,
            "char_count": sum(len(s) for s in current_content_lines),
        }

    for line in lines[exp_start:exp_end]:
        s = line.strip()
        if not s:
            continue
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            _commit()
            current_header = s
            current_content_lines = []
        elif current_header is not None:
            current_content_lines.append(s)

    _commit()
    return stats


def _canonicalize_role_name(name: str) -> str:
    """Normalize a role name for fuzzy matching.

    Lowercases, collapses whitespace around ``|`` separators, and strips
    leading/trailing whitespace.  Handles cases where the plan uses
    ``"Company|Role"`` but the resume header has ``"Company | Role"``.
    """
    s = re.sub(r"\s*\|\s*", "|", name.lower())
    return re.sub(r"\s+", " ", s).strip()


def _build_role_source_bullet_counts(plan: dict, role_stats: dict) -> dict[str, int]:
    """Return {plan_role_name: bullet_count_in_master_resume} via fuzzy header match."""
    result: dict[str, int] = {}
    for role_entry in plan.get("resume_strategy", {}).get("experience", []):
        name = role_entry.get("role_name", "")
        if not name:
            continue
        name_canon = _canonicalize_role_name(name)
        matched_count = 0
        for header, stats in role_stats.items():
            if name_canon in _canonicalize_role_name(header):
                matched_count = stats.get("bullet_count", 0)
                break
        result[name] = matched_count
    return result


def _build_role_source_char_counts(plan: dict, role_stats: dict) -> dict[str, int]:
    """Return {plan_role_name: total_char_count_in_master_resume} via fuzzy header match."""
    result: dict[str, int] = {}
    for role_entry in plan.get("resume_strategy", {}).get("experience", []):
        name = role_entry.get("role_name", "")
        if not name:
            continue
        name_canon = _canonicalize_role_name(name)
        matched_count = 0
        for header, stats in role_stats.items():
            if name_canon in _canonicalize_role_name(header):
                matched_count = stats.get("char_count", 0)
                break
        result[name] = matched_count
    return result


def _build_integration_reframes(plan: dict) -> list[dict]:
    reframes: list[dict] = []
    for role_entry in plan.get("resume_strategy", {}).get("experience", []):
        for reframe in role_entry.get("bullets_to_reframe", []):
            after_intent = reframe.get("after_intent", "").lower()
            if any(kw in after_intent for kw in _INTEGRATION_KEYWORDS):
                reframes.append(
                    {
                        "source_anchor": reframe.get("before", ""),
                        "safe_reframe_intent": reframe.get("after_intent", ""),
                    }
                )
    return reframes


def _compute_jd_is_delivery_oriented(plan: dict) -> bool:
    """Return True if any jd_top_themes keyword matches a delivery-oriented term."""
    for theme in plan.get("jd_top_themes", []):
        for kw in theme.get("keywords", []):
            if isinstance(kw, str) and kw.lower() in _DELIVERY_JD_KEYWORDS:
                return True
    return False


# ---------------------------------------------------------------------------
# Source skill collection
# ---------------------------------------------------------------------------

def _collect_source_skills(profile: dict, master_resume_str: str) -> list[str]:
    """Return all skill tokens from profile + resume Technical Skills section."""
    skills: list[str] = []

    # From candidate_profile.technical_skills (all leaf string values)
    ts = profile.get("technical_skills", {})
    for category_value in ts.values():
        if isinstance(category_value, list):
            for item in category_value:
                if isinstance(item, str) and item:
                    skills.append(item)
        elif isinstance(category_value, str) and category_value:
            skills.append(category_value)

    # Also surface async_messaging patterns from profile
    for pattern in profile.get("technical_skills", {}).get("async_messaging", []):
        if isinstance(pattern, str):
            skills.append(pattern)

    # From MASTER_RESUME Technical Skills section
    skills.extend(_parse_resume_skills_section(master_resume_str))

    return skills


def _collect_jd_keywords(plan: dict) -> set[str]:
    """Return lowercased keyword set from all jd_top_themes."""
    keywords: set[str] = set()
    for theme in plan.get("jd_top_themes", []):
        for kw in theme.get("keywords", []):
            if isinstance(kw, str):
                keywords.add(kw.lower())
    return keywords


def _parse_resume_skills_section(resume_text: str) -> list[str]:
    """Extract individual skill tokens from the Technical Skills section."""
    lines = resume_text.split("\n")
    in_skills = False
    skill_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped in ("Technical Skills", "Skills"):
            in_skills = True
            continue
        if in_skills and stripped in _RESUME_SECTION_HEADERS:
            break
        if in_skills and stripped:
            skill_lines.append(stripped)

    tokens: list[str] = []
    for line in skill_lines:
        # Split on common delimiters
        parts = re.split(r"[,|•·]+", line)
        for part in parts:
            token = part.strip().lstrip("-• ").strip()
            # Reasonable skill name: non-empty, not too long, not a category label
            if token and len(token) < 60 and ":" not in token:
                tokens.append(token)

    return tokens


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_json_safe(text: str) -> dict:
    """Parse JSON string to dict; return empty dict on failure."""
    if not text:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _dedup_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _dedup_case_insensitive(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item.lower() not in seen:
            seen.add(item.lower())
            result.append(item)
    return result
