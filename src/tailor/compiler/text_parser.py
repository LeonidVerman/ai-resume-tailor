"""Parse LLM plain-text resume output into structured sections.

The LLM is expected to output:
  - Section headings (e.g. "Experience", "Technical Skills")
  - Role headers with pipes (e.g. "Senior Engineer | Acme Corp")
  - Date/location meta lines (e.g. "Jan 2022 – Present")
  - Bullet lines prefixed with "- "
  - Plain body lines for non-experience sections

These are stable anchors the LLM is instructed not to change.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _normalize_letter_spaced(s: str) -> str:
    """Collapse letter-spaced headings to plain lowercase words.

    'S U M M A R Y'                 → 'summary'
    'T E C H N I C A L \\xa0 S K I L L S' → 'technical skills'

    Splits on NBSP (\\xa0) first to detect multi-word groups, then checks that
    each group consists entirely of single-character tokens separated by spaces.
    Returns s.lower() unchanged when the pattern does not match.
    """
    word_groups = re.split(r'\xa0+', s.strip())
    words: list[str] = []
    for grp in word_groups:
        parts = [p for p in grp.strip().split() if p]
        if not parts:
            continue
        if all(len(p) == 1 for p in parts):
            words.append(''.join(parts).lower())
        else:
            # Not letter-spaced — bail out
            return s.lower()
    return ' '.join(words) if words else s.lower()


_EXPERIENCE_NAMES: frozenset[str] = frozenset({
    "experience", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "about me", "career summary",
    "executive summary",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees",
})
_ALL_KNOWN: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES | frozenset({
        "certifications", "certification", "licenses", "publications",
        "projects", "volunteer", "volunteering", "awards", "honors",
        "references", "languages", "interests", "activities",
        "leadership", "leadership experience", "additional information",
    })
)


def _classify(heading: str) -> str:
    t = heading.strip().lower()
    if t in _EXPERIENCE_NAMES:
        return "experience"
    if t in _SUMMARY_NAMES:
        return "summary"
    if t in _SKILLS_NAMES:
        return "skills"
    if t in _EDUCATION_NAMES:
        return "education"
    # Try letter-spaced form: "S U M M A R Y" → "summary"
    tn = _normalize_letter_spaced(heading)
    if tn != t:
        if tn in _EXPERIENCE_NAMES:
            return "experience"
        if tn in _SUMMARY_NAMES:
            return "summary"
        if tn in _SKILLS_NAMES:
            return "skills"
        if tn in _EDUCATION_NAMES:
            return "education"
    return "other"


def _is_section_heading(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if s.startswith(("-", "•", "·", "–", "*")):
        return False
    if "|" in s:
        return False
    if len(s) > 60:
        return False
    if s.lower() in _ALL_KNOWN:
        return True
    # Letter-spaced form: "S U M M A R Y", "T E C H N I C A L \xa0 S K I L L S"
    if _normalize_letter_spaced(s) in _ALL_KNOWN:
        return True
    # Tight title-case fallback for exactly 2-word lines (single words like "Python"
    # and 3+ word role titles like "Senior Software Developer" are excluded).
    if any(c in s for c in (",", ";", ":")):
        return False
    if s[-1] in ".!?":
        return False
    words = s.split()
    if len(words) != 2:
        return False
    if not all(w[0].isupper() for w in words if w):
        return False
    if _YEAR_RE.search(s):
        return False
    # Exclude lines where any word has an interior lowercase→uppercase transition
    # (camelCase pattern), which indicates concatenated content rather than a real
    # section heading (e.g. "OrganizationProblem-solving Management" from a template
    # where two skill lines are merged without a separator).
    for word in words:
        # Split on hyphens so "Problem-Solving" isn't falsely flagged.
        for part in word.split("-"):
            if any(part[i].islower() and part[i + 1].isupper() for i in range(len(part) - 1)):
                return False
    return True


def _is_role_header(line: str) -> bool:
    s = line.strip()
    return bool(s) and "|" in s and not s.startswith(("-", "•"))


def _is_meta_line(line: str) -> bool:
    s = line.strip()
    return bool(s) and bool(_YEAR_RE.search(s)) and "|" not in s and not s.startswith("-")


# ---------------------------------------------------------------------------
# Data classes for parsed LLM output
# ---------------------------------------------------------------------------

@dataclass
class LlmRole:
    header: str
    meta_lines: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)


@dataclass
class LlmSection:
    heading: str
    semantic_type: str
    body_lines: list[str] = field(default_factory=list)   # non-experience
    roles: list[LlmRole] = field(default_factory=list)    # experience only


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_llm_output(text: str) -> list[LlmSection]:
    """Parse LLM plain-text resume output into a list of LlmSection objects."""
    lines = text.split("\n")
    sections: list[LlmSection] = []
    current: LlmSection | None = None
    cur_role: LlmRole | None = None
    state = "pre"
    found_section = False  # True once the first known heading has been seen

    def _finish_role() -> None:
        nonlocal cur_role
        if cur_role is not None and current is not None:
            current.roles.append(cur_role)
            cur_role = None

    def _finish_section() -> None:
        if current is not None:
            sections.append(current)

    for line in lines:
        stripped = line.strip()

        # Before the first real section, accept only _ALL_KNOWN headings plus
        # ALL-CAPS 3+-word lines.  The title-case fallback is suppressed to
        # avoid false positives on "Firstname Lastname" at the top of the
        # resume.  ALL-CAPS lines of 3+ words (e.g. "SENIOR SOFTWARE DEVELOPER")
        # are safe to accept: candidate names are rarely 3+ words in ALL-CAPS.
        # After the first section, use the full heuristic so that unusual section
        # names (e.g. "Additional Experience") are recognised.
        if found_section:
            # Inside an experience section that has no role headers yet (body-only
            # experience, e.g. non-standard format with role titles on plain lines),
            # suppress the title-case fallback.  Only _ALL_KNOWN names end the section;
            # this prevents 2-word role titles ("Project Coordinator") from being
            # mis-recognised as section headings.
            in_roleless_experience = (
                current is not None
                and current.semantic_type == "experience"
                and cur_role is None
                and not current.roles
            )
            # Inside a non-experience section that already has body_lines, suppress
            # the 2-word title-case fallback.  Skill/body lines like "Statistical
            # Analysis" or "Product Development" are 2 title-case words and would
            # otherwise be misdetected as new section headings, triggering spurious
            # extra sections and section reordering in apply_tailored.
            in_section_with_body = (
                current is not None
                and cur_role is None
                and bool(current.body_lines)
            )
            # Inside an experience role that already has meta_lines, suppress the
            # title-case fallback.  Sub-titles like "Software Engineer" (a role
            # promotion within a company) have 2 title-case words but are NOT
            # new section headings — they are meta content for the current role.
            in_experience_role_with_meta = (
                cur_role is not None
                and bool(cur_role.meta_lines)
            )
            if in_roleless_experience or in_section_with_body or in_experience_role_with_meta:
                is_heading = bool(stripped) and (
                    stripped.lower() in _ALL_KNOWN
                    or _normalize_letter_spaced(stripped) in _ALL_KNOWN
                )
            else:
                is_heading = _is_section_heading(line)
        else:
            s_lower = stripped.lower()
            is_heading = bool(stripped) and (
                s_lower in _ALL_KNOWN
                or _normalize_letter_spaced(stripped) in _ALL_KNOWN
                or (
                    stripped == stripped.upper()
                    and stripped.replace(" ", "").replace("-", "").isalpha()
                    and len(stripped.split()) >= 3
                    and not _YEAR_RE.search(stripped)
                    and len(stripped) <= 60
                )
            )

        if is_heading:
            found_section = True
            _finish_role()
            _finish_section()
            sem = _classify(stripped)
            current = LlmSection(heading=stripped, semantic_type=sem)
            cur_role = None
            state = "body"
            continue

        if current is None:
            continue  # text before first section heading

        if not stripped:
            continue  # blank lines are ignored

        if current.semantic_type == "experience" and _is_role_header(line):
            _finish_role()
            cur_role = LlmRole(header=stripped)
            state = "role"
            continue

        if state == "body" or cur_role is None:
            text_val = stripped[2:] if stripped.startswith("- ") else stripped
            current.body_lines.append(text_val)
        elif state == "role":
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            elif _is_meta_line(line):
                cur_role.meta_lines.append(stripped)
                state = "meta"
            else:
                cur_role.meta_lines.append(stripped)
                state = "meta"
        elif state == "meta":
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            else:
                # Keep as meta whether or not it looks like a date.
                # Non-"- " content in meta state stays as meta so that
                # plain-text paragraphs (e.g. PDF-sourced achievement lines
                # serialised without "- " prefix) round-trip correctly.
                # Bullets must use explicit "- " prefix to be recognised.
                cur_role.meta_lines.append(stripped)
        elif state == "bullets":
            if stripped.startswith("- "):
                cur_role.bullets.append(stripped[2:])
            else:
                cur_role.bullets.append(stripped)

    _finish_role()
    _finish_section()
    return sections
