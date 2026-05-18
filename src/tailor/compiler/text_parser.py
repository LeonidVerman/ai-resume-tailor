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

# Broad standalone-date pattern that also matches "20XX"/"19XX" placeholder years
# (templates use XX instead of real digits).  Used to detect inter-role date lines
# in experience sections so they are treated as role boundaries, not bullet items.
_MONTH_SHORT = (
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"(?:[a-z]*)\.?"
)
_YEAR_SLOT = r"(?:(?:19|20)\d{2}|(?:19|20)[Xx]{2})"
_DATE_WORD = r"(?:Present|Current|Now|Ongoing)"
_EXP_DATE_RE = re.compile(
    rf"^\s*(?:{_MONTH_SHORT}\s+)?{_YEAR_SLOT}"
    rf"(?:\s*[-–—]\s*(?:(?:{_MONTH_SHORT}\s+)?(?:{_YEAR_SLOT}|{_DATE_WORD})))?"
    r"\s*$",
    re.IGNORECASE,
)

# Bullet prefixes the LLM may emit: hyphen-minus ("- "), Unicode bullet ("• "),
# black-circle ("● "), or en-dash ("– ").  All are 2 characters; stripped[2:]
# removes the prefix cleanly.  The LLM sometimes mirrors the template's own
# bullet character instead of the canonical "- " form.
_BULLET_PREFIXES: tuple[str, ...] = ("- ", "\u2022 ", "\u25cf ", "\u2013 ")

_CURRENT_DATE_RE = re.compile(r"^\s*Current\s+Date\s*:", re.IGNORECASE)

# Section heading words that must never be treated as role-title merge candidates.
# The merger is only intended for job titles ("Web Developer", "Senior Engineer")
# that happen to appear on the line before "Company | Year".  Well-known section
# headings look the same syntactically but must not be merged.
_SECTION_HEADING_WORDS: frozenset[str] = frozenset({
    "experience", "work", "employment", "career",
    "education", "academic", "qualifications",
    "skills", "competencies", "expertise", "technologies",
    "summary", "objective", "profile", "overview", "about",
    "projects", "certifications", "certificates", "licenses",
    "awards", "achievements", "honors", "accomplishments",
    "publications", "research", "patents",
    "volunteer", "volunteering", "community",
    "additional", "other", "extras", "miscellaneous",
    "interests", "hobbies", "activities",
    "languages", "references", "affiliations", "memberships",
    "training", "courses", "coursework",
})


def preprocess_resume_text(text: str) -> str:
    """Normalize LLM resume text before parsing.

    Two transformations applied in order:

    1. Remove "Current Date: \u2026" lines (the LLM sometimes appends a timestamp
       that must never appear in rendered output).

    2. Merge "title-line\\ncompany|date-line" pairs into a single
       "title | company | date" role-header line so that ``parse_llm_output``
       detects them as proper pipe-delimited role headers.

       The LLM occasionally formats roles as::

           Web Developer
           Liceria & Co. | 2019 \u2013 Present

       instead of the canonical pipe-delimited form.  Without this merge
       "Web Developer" lands in the section's ``body_lines`` and
       "Liceria & Co. | 2019 \u2013 Present" becomes the role header, losing the
       job title.

    The transformation is idempotent: lines already containing ``|`` are never
    treated as merge candidates, so calling this twice is safe.
    """
    lines = text.splitlines()

    # 1. Drop "Current Date:" lines
    lines = [line for line in lines if not _CURRENT_DATE_RE.match(line)]

    # 2. Merge role title + company|date onto one line
    result: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        first_word = stripped.split()[0].lower() if stripped else ""
        is_title_candidate = (
            bool(stripped)
            and "|" not in stripped
            and not any(stripped.startswith(p) for p in _BULLET_PREFIXES)
            and stripped != stripped.upper()        # not an ALL-CAPS section heading
            and first_word not in _SECTION_HEADING_WORDS  # not a known section heading
            and len(stripped) <= 60
            and stripped[-1:] not in ".!?,:;"       # not end-of-sentence/list
        )

        if is_title_candidate:
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                next_stripped = lines[j].strip()
                if "|" in next_stripped and _YEAR_RE.search(next_stripped):
                    result.append(f"{stripped} | {next_stripped}")
                    result.extend(lines[i + 1:j])   # preserve intervening blanks
                    i = j + 1
                    continue

        result.append(line)
        i += 1

    return "\n".join(result)


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
    "experience", "experiences", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "about me", "career summary",
    "executive summary",
    # Non-canonical labels used in resume templates as summary containers:
    "professional overview", "general info", "general information",
    # Generic intro section names:
    "about",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "skill", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees",
})
_CERTIFICATIONS_NAMES: frozenset[str] = frozenset({
    "certifications", "certification", "licenses", "license",
    "certifications and training", "training and certifications",
    "training", "courses", "professional development",
})
_LANGUAGES_NAMES: frozenset[str] = frozenset({
    "languages", "language skills",
})
_WEBSITES_NAMES: frozenset[str] = frozenset({
    "websites", "profiles", "social profiles", "links",
    "portfolio", "web profiles", "online profiles",
})
_ALL_KNOWN: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES
    | _CERTIFICATIONS_NAMES | _LANGUAGES_NAMES | _WEBSITES_NAMES
    | frozenset({
        "publications", "projects", "volunteer", "volunteering",
        "awards", "honors", "references", "interests", "activities",
        "leadership", "leadership experience", "additional information",
        # Common named sections that some templates include as separate headings.
        # Recognising "communication" prevents it from being absorbed as a body
        # line of a preceding skills section when it appears alone (e.g. after
        # "Technical Skills" body lines in a template that has a separate
        # Communication section).
        "communication",
        # Contact and affiliation sections: LLMs sometimes append these under an
        # "ADDITIONAL" filler heading after "TECHNICAL SKILLS", causing the updater
        # to inject them into skill slots.  Treating them as known section headings
        # makes the parser break at those boundaries instead.
        "contact information", "contact info", "affiliations",
        # "Additional" (bare word) used by some LLMs as a catch-all section after
        # Technical Skills — must be a section boundary, not a body line.
        "additional",
        # "My Qualifications" / "Personal References" appear in template 23 and
        # similar.  Treating them as known section boundaries prevents their content
        # from being absorbed into the preceding section (e.g. GENERAL INFO).
        # Not added to _SKILLS_NAMES so they stay semantic=other in LLM output —
        # this lets "TECHNICAL SKILLS" (semantic=skills) match the template's
        # MY QUALIFICATIONS slot via semantic-type matching in Pass 2.
        "my qualifications", "personal references", "personal reference",
        # Accomplishment / achievement sections (e.g. sample 32 "Accomplishments")
        # and combined skills headings ("Skills and Abilities").
        "accomplishments", "achievement", "achievements",
        "skills and abilities",
    })
)


def _classify(heading: str) -> str:
    """Classify an LLM section heading to a semantic type.

    Returns one of: experience | summary | skills | education |
    certifications | languages | websites | other.
    """
    t = heading.strip().lower()
    if t in _EXPERIENCE_NAMES:
        return "experience"
    if t in _SUMMARY_NAMES:
        return "summary"
    if t in _SKILLS_NAMES:
        return "skills"
    if t in _EDUCATION_NAMES:
        return "education"
    if t in _CERTIFICATIONS_NAMES:
        return "certifications"
    if t in _LANGUAGES_NAMES:
        return "languages"
    if t in _WEBSITES_NAMES:
        return "websites"
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
        if tn in _CERTIFICATIONS_NAMES:
            return "certifications"
        if tn in _LANGUAGES_NAMES:
            return "languages"
        if tn in _WEBSITES_NAMES:
            return "websites"
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
    text = preprocess_resume_text(text)
    lines = text.split("\n")
    sections: list[LlmSection] = []
    current: LlmSection | None = None
    cur_role: LlmRole | None = None
    state = "pre"
    found_section = False  # True once the first known heading has been seen
    # Capture first long non-contact prose paragraph seen before any section heading.
    # Some templates (e.g. "OFFICE MANAGER") put the summary paragraph before the
    # first recognized heading; this preserves it for synthetic summary injection.
    _pre_section_prose: str = ""
    # Pending meta-lines collected from standalone date lines that appear between
    # experience roles.  The date belongs to the NEXT role (not the current one),
    # so it is buffered here and flushed into the next LlmRole's meta_lines when
    # the next role header is seen.  This prevents "December 20XX–November 20XX"
    # from being appended as a bullet of the preceding role.
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
            # Capture the first long non-contact prose line before any heading as a
            # potential summary.  Safe criteria: ≥60 chars, has spaces, no email/URL/pipe,
            # not a bullet, not starting with digit (avoids phone numbers and dates).
            if (
                not _pre_section_prose
                and stripped
                and len(stripped) >= 60
                and " " in stripped
                and "@" not in stripped
                and "://" not in stripped
                and "|" not in stripped
                and stripped[0] not in ("-", "•", "·", "–", "*")
                and not stripped[0].isdigit()
            ):
                _pre_section_prose = stripped
            continue  # text before first section heading

        if not stripped:
            continue  # blank lines are ignored

        if current.semantic_type == "experience" and _is_role_header(line):
            if cur_role is not None and not cur_role.bullets and not cur_role.meta_lines:
                # Two consecutive pipe-format role headers with no content between them.
                # The LLM sometimes writes "Company | Date" on one line and
                # "Title | Department" on the next.  Treat the second header as
                # meta info for the current role rather than starting a phantom role.
                cur_role.meta_lines.append(stripped)
                state = "meta"
            else:
                _finish_role()
                cur_role = LlmRole(header=stripped)
                state = "role"
            continue

        if state == "body" or cur_role is None:
            text_val = stripped[2:] if stripped.startswith("- ") else stripped
            current.body_lines.append(text_val)
        elif state == "role":
            if any(stripped.startswith(p) for p in _BULLET_PREFIXES):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            elif _is_meta_line(line):
                cur_role.meta_lines.append(stripped)
                state = "meta"
            else:
                cur_role.meta_lines.append(stripped)
                state = "meta"
        elif state == "meta":
            if any(stripped.startswith(p) for p in _BULLET_PREFIXES):
                cur_role.bullets.append(stripped[2:])
                state = "bullets"
            else:
                # Keep as meta whether or not it looks like a date.
                # Non-bullet content in meta state stays as meta so that
                # plain-text paragraphs (e.g. PDF-sourced achievement lines
                # serialised without a bullet prefix) round-trip correctly.
                cur_role.meta_lines.append(stripped)
        elif state == "bullets":
            if any(stripped.startswith(p) for p in _BULLET_PREFIXES):
                cur_role.bullets.append(stripped[2:])
            elif (
                current is not None
                and current.semantic_type == "experience"
                and _EXP_DATE_RE.match(stripped)
            ):
                # Standalone date line appearing between roles (e.g. the LLM echoes
                # "December 20XX–November 20XX" at the start of the next role's block).
                # Finish the current role and discard the date: it is already present
                # in the template's left-column table cell (role_meta para_id) and does
                # not need to be passed through the updater.  Adding it as a bullet
                # causes it to appear twice in the rendered output.
                _finish_role()
                state = "body"  # wait for next role header
            else:
                cur_role.bullets.append(stripped)

    _finish_role()
    _finish_section()

    # If pre-section prose was captured and no summary section exists, inject a
    # synthetic "Professional Summary" section at position 0 so the updater can
    # place it in the template's intro-prose slot.
    if _pre_section_prose and not any(s.semantic_type == "summary" for s in sections):
        sections.insert(0, LlmSection(
            heading="Professional Summary",
            semantic_type="summary",
            body_lines=[_pre_section_prose],
        ))

    return sections
