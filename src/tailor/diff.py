"""Diff analysis between master and LLM-tailored resume sections."""

import re
from difflib import SequenceMatcher

# Minimum similarity ratio to consider two bullets a "changed" pair rather
# than independent additions/removals.
_SIMILARITY_THRESHOLD = 0.45

# Matches a 4-digit year (1900-2099) — used to identify date lines.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

_ALL_SECTION_HEADERS = {
    "Experience", "Professional Experience", "Education", "Technical Skills",
    "Skills", "Skill", "Certifications", "Projects", "Publications", "Volunteer",
    "Awards", "References", "Professional Summary", "Summary",
    "Additional Information",
}

# Precomputed lowercase set for case-insensitive boundary detection.
_ALL_SECTION_HEADERS_LOWER = {h.lower() for h in _ALL_SECTION_HEADERS}

# Headers that mark the start of the Experience section (case-insensitive).
_EXPERIENCE_HEADERS_LOWER = {"experience", "professional experience"}

# Alternate header names accepted for the same logical section.
_SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "Professional Summary": ("Professional Summary", "Summary"),
    "Technical Skills":     ("Technical Skills", "Skills", "Skill"),
}


# ---------------------------------------------------------------------------
# Generic section extraction
# ---------------------------------------------------------------------------

def _extract_section(text: str, *headers: str) -> str | None:
    """Extract and normalize the content of the first matching section header.

    Returns None if no matching header is found or the section is empty.
    """
    lines = text.split("\n")
    start: int | None = None
    end = len(lines)
    found_header: str | None = None

    headers_lower = {h.lower() for h in headers}
    for i, line in enumerate(lines):
        s = line.strip()
        sl = s.lower()
        if start is None and sl in headers_lower:
            start = i + 1
            found_header = s
        elif start is not None and sl in _ALL_SECTION_HEADERS_LOWER and sl != found_header.lower():
            end = i
            break

    if start is None:
        return None

    normalized = [line.strip() for line in lines[start:end]]
    while normalized and not normalized[0]:
        normalized.pop(0)
    while normalized and not normalized[-1]:
        normalized.pop()

    content = "\n".join(normalized)
    return content if content else None


# ---------------------------------------------------------------------------
# Simple (before/after) section diff
# ---------------------------------------------------------------------------

def _diff_simple_section(name: str, master_text: str, tailored_text: str) -> dict | None:
    """Return {"name", "before", "after"} if the section differs, else None."""
    headers = _SECTION_ALIASES.get(name, (name,))
    master   = _extract_section(master_text,   *headers)
    tailored = _extract_section(tailored_text, *headers)

    if master == tailored:   # covers both-None and identical content
        return None

    result: dict = {"name": name}
    if master   is not None:
        result["before"] = master
    if tailored is not None:
        result["after"] = tailored
    return result


# ---------------------------------------------------------------------------
# Experience section — per-role bullet diff
# ---------------------------------------------------------------------------

def _is_date_line(line: str) -> bool:
    s = line.strip()
    return bool(_YEAR_RE.search(s)) and "|" not in s and len(s) < 60


def _parse_experience(text: str, bullets_have_dash: bool) -> list[tuple[str, list[str]]]:
    """Parse Experience section into [(role_header, [bullets])].

    Args:
        text: Plain-text resume (from read_docx or LLM output).
        bullets_have_dash: True for LLM output (bullets start with '- ');
                           False for master resume (bullets are plain lines).
    """
    lines = text.split("\n")

    exp_start: int | None = None
    exp_end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        sl = s.lower()
        if sl in _EXPERIENCE_HEADERS_LOWER:
            exp_start = i
        elif exp_start is not None and sl in _ALL_SECTION_HEADERS_LOWER and sl not in _EXPERIENCE_HEADERS_LOWER:
            exp_end = i
            break

    if exp_start is None:
        return []

    roles: list[tuple[str, list[str]]] = []
    current_role: str | None = None
    current_bullets: list[str] = []

    for line in lines[exp_start + 1:exp_end]:
        s = line.strip()
        if not s:
            continue
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            if current_role is not None:
                roles.append((current_role, current_bullets))
            current_role = s
            current_bullets = []
        elif current_role is not None:
            if bullets_have_dash:
                if s.startswith("- "):
                    current_bullets.append(s[2:].strip())
                elif s.startswith("• "):
                    current_bullets.append(s[2:].strip())
            else:
                if not _is_date_line(s):
                    current_bullets.append(s)

    if current_role is not None:
        roles.append((current_role, current_bullets))
    return roles


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _diff_bullets(master: list[str], tailored: list[str]) -> dict:
    """Compare two bullet lists, returning added / removed / changed."""
    matched_m: set[int] = set()
    matched_t: set[int] = set()
    changed: list[dict] = []

    # Pass 1: exact matches — excluded from diff
    for i, mb in enumerate(master):
        for j, tb in enumerate(tailored):
            if j in matched_t:
                continue
            if mb.strip().lower() == tb.strip().lower():
                matched_m.add(i)
                matched_t.add(j)
                break

    # Pass 2: near-matches → "changed"
    pairs: list[tuple[float, int, int]] = []
    for i, mb in enumerate(master):
        if i in matched_m:
            continue
        for j, tb in enumerate(tailored):
            if j in matched_t:
                continue
            sim = _similarity(mb, tb)
            if sim >= _SIMILARITY_THRESHOLD:
                pairs.append((sim, i, j))

    pairs.sort(reverse=True)
    for sim, i, j in pairs:
        if i not in matched_m and j not in matched_t:
            changed.append({"before": master[i], "after": tailored[j]})
            matched_m.add(i)
            matched_t.add(j)

    removed = [master[i]   for i in range(len(master))   if i not in matched_m]
    added   = [tailored[j] for j in range(len(tailored)) if j not in matched_t]
    return {"added": added, "removed": removed, "changed": changed}


def _diff_experience_section(master_text: str, tailored_text: str) -> dict | None:
    """Return Experience section diff, or None if no differences."""
    master_roles   = _parse_experience(master_text,   bullets_have_dash=False)
    tailored_roles = _parse_experience(tailored_text, bullets_have_dash=True)

    roles_diff: list[dict] = []
    for idx, (_, master_bullets) in enumerate(master_roles):
        if idx >= len(tailored_roles):
            break
        tailored_name, tailored_bullets = tailored_roles[idx]
        bullet_diff = _diff_bullets(master_bullets, tailored_bullets)
        if bullet_diff["added"] or bullet_diff["removed"] or bullet_diff["changed"]:
            entry: dict = {"name": tailored_name}
            for key in ("added", "removed", "changed"):
                if bullet_diff[key]:
                    entry[key] = bullet_diff[key]
            roles_diff.append(entry)

    if not roles_diff:
        return None
    return {"name": "Experience", "roles": roles_diff}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def diff_resume(master_text: str, tailored_text: str) -> list[dict]:
    """Return an array of changed resume sections.

    Each element is one of:
      {"name": "Professional Summary", "before": "...", "after": "..."}
      {"name": "Experience", "roles": [...per-role bullet diffs...]}
      {"name": "Technical Skills",     "before": "...", "after": "..."}

    Sections with no differences are omitted entirely.
    """
    sections: list[dict] = []

    for fn in (
        lambda: _diff_simple_section("Professional Summary", master_text, tailored_text),
        lambda: _diff_experience_section(master_text, tailored_text),
        lambda: _diff_simple_section("Technical Skills", master_text, tailored_text),
    ):
        result = fn()
        if result:
            sections.append(result)

    return sections
