"""Diff analysis between master and LLM-tailored resume sections."""

import re
from difflib import SequenceMatcher

# Minimum similarity ratio to consider two bullets a "changed" pair rather
# than independent additions/removals.
_SIMILARITY_THRESHOLD = 0.45

# Matches a 4-digit year (1900-2099) — used to identify date lines.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

_SECTION_HEADERS = {
    "Education", "Technical Skills", "Skills", "Certifications",
    "Projects", "Publications", "Volunteer", "Awards", "References",
    "Professional Summary", "Summary",
}


def _is_date_line(line: str) -> bool:
    s = line.strip()
    return bool(_YEAR_RE.search(s)) and "|" not in s and len(s) < 60


def _parse_experience(text: str, bullets_have_dash: bool) -> list[tuple[str, list[str]]]:
    """Parse the Experience section into [(role_header, [bullets])].

    Args:
        text: Plain-text resume (from read_docx or LLM output).
        bullets_have_dash: True for LLM output (bullets start with '- ');
                           False for master resume (bullets are plain lines).
    """
    lines = text.split("\n")

    exp_start = None
    exp_end = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if s == "Experience":
            exp_start = i
        elif exp_start is not None and s in _SECTION_HEADERS:
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
            # Role header
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
                # date lines and other non-bullet lines are skipped
            else:
                # Master format: skip date-like lines; everything else is a bullet
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

    # Pass 1: exact matches — kept as-is, excluded from diff
    for i, mb in enumerate(master):
        for j, tb in enumerate(tailored):
            if j in matched_t:
                continue
            if mb.strip().lower() == tb.strip().lower():
                matched_m.add(i)
                matched_t.add(j)
                break

    # Pass 2: near-matches above threshold → "changed"
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


def diff_resume_experience(master_text: str, tailored_text: str) -> dict:
    """Return a structured diff of the Experience section.

    Roles are matched by position (same order is assumed).
    Only roles with at least one difference are included.
    """
    master_roles   = _parse_experience(master_text,   bullets_have_dash=False)
    tailored_roles = _parse_experience(tailored_text, bullets_have_dash=True)

    roles_diff: list[dict] = []
    for idx, (_, master_bullets) in enumerate(master_roles):
        if idx >= len(tailored_roles):
            break
        tailored_name, tailored_bullets = tailored_roles[idx]

        bullet_diff = _diff_bullets(master_bullets, tailored_bullets)
        if bullet_diff["added"] or bullet_diff["removed"] or bullet_diff["changed"]:
            entry = {"name": tailored_name}
            for key in ("added", "removed", "changed"):
                if bullet_diff[key]:
                    entry[key] = bullet_diff[key]
            roles_diff.append(entry)

    return {
        "experience": {
            "name": "Experience",
            "roles": roles_diff,
        }
    }
