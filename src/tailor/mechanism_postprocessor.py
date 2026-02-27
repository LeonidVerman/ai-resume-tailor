"""Deterministic mechanism-enforcement post-processor for Phase 2.

Runs after ``postprocess_token_compliance`` and injects named mechanism phrases
from ``arch_mechanisms_primary`` (and ``arch_mechanisms_backstop`` as last resort)
into existing bullet lines for roles that still fall short of their
``mechanism_min_by_priority`` target.

No LLM is involved: all injection is done by appending `` via {phrase}`` to
a chosen existing bullet line.  The cover letter is never modified.

Guarantees
----------
- Only injects phrases from ``writer_packet["arch_mechanisms_primary"]`` +
  ``writer_packet["arch_mechanisms_backstop"]`` (primary pool exhausted first).
- Never duplicates a phrase already present (case-insensitive) in a role block.
- Never adds new lines; only modifies existing bullet lines.
- Only injects into explicit bullet lines (starting with ``- `` or ``•``).
- Phrases are distributed across deficit roles without reuse where possible.
- A second safety-net pass is attempted if the first pass still leaves deficits.
"""

from __future__ import annotations

import logging

from tailor.phase2_validator import (
    _RESUME_SECTION_HEADERS,
    _bullet_has_arch_mechanism,
    _compute_effective_priorities,
    _is_date_line,
    _parse_roles,
    normalize_role_header,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keywords that make a bullet a preferred injection target (it's already
# discussing something performance / infrastructure related, so appending a
# mechanism name reads naturally).
# ---------------------------------------------------------------------------
_MECHANISM_PREF_KEYWORDS: frozenset[str] = frozenset({
    "scalability", "performance", "latency", "reliability", "throughput",
    "caching", "queue", "replication", "distributed", "availability",
})


def _normalize_via_phrase(phrase: str) -> str:
    """Lowercase the first character of a via-clause phrase when safe.

    Preserves ALL-CAPS acronyms ("API", "SQL") and CamelCase proper nouns
    ("PostgreSQL", "MongoDB") by checking for internal uppercase letters in the
    first word.  Title-cased common words ("Horizontal", "Database") are
    lowercased so the injection reads naturally mid-sentence.
    """
    if not phrase or len(phrase) < 2:
        return phrase
    first_word = phrase.split()[0]
    has_internal_upper = any(c.isupper() for c in first_word[1:])
    if phrase[0].isupper() and not has_internal_upper:
        return phrase[0].lower() + phrase[1:]
    return phrase


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def postprocess_mechanism_enforcement(
    output_json: dict,
    writer_packet: dict,
    validation_report: dict | None = None,
) -> dict:
    """Inject missing mechanism phrases into deficit role blocks deterministically.

    Parameters
    ----------
    output_json:
        Phase 2 output with keys ``resume`` and ``cover_letter``.
    writer_packet:
        WriterPacket dict.  Keys used: ``arch_mechanisms_primary``,
        ``arch_mechanisms_backstop`` (falls back to ``must_surface_arch_mechanisms``
        for older packets), ``density_targets``, ``role_priorities``,
        ``role_source_bullet_counts``, ``role_source_char_counts``,
        ``jd_is_delivery_oriented``.
    validation_report:
        Optional ValidationReport from the preceding validator run.  When
        provided, ``stats.role_mechanism_counts`` is used as the baseline
        count instead of re-parsing the resume.

    Returns
    -------
    dict
        Updated ``{"resume": ..., "cover_letter": ...}`` dict.  The cover
        letter is returned unchanged.
    """
    resume = output_json.get("resume") or ""
    cover_letter = output_json.get("cover_letter") or ""

    # Primary pool (profile-derived) is exhausted before backstop (safe_translation).
    # Falls back to the flat must_surface_arch_mechanisms for older packets that
    # do not carry provenance split fields.
    primary: list[str] = writer_packet.get("arch_mechanisms_primary", [])
    backstop: list[str] = writer_packet.get("arch_mechanisms_backstop", [])
    if primary or backstop:
        must_surface: list[str] = primary + backstop
    else:
        must_surface = writer_packet.get("must_surface_arch_mechanisms", [])
    if not must_surface:
        return {"resume": resume, "cover_letter": cover_letter}

    density = writer_packet.get("density_targets", {})
    mechanism_min_map: dict[str, int] = density.get(
        "mechanism_min_by_priority", {"high": 2, "medium": 1, "low": 0}
    )

    role_priorities: dict[str, str] = writer_packet.get("role_priorities", {})
    role_source_counts: dict[str, int] = writer_packet.get("role_source_bullet_counts", {})
    role_source_char_counts: dict[str, int] = writer_packet.get("role_source_char_counts", {})
    jd_is_delivery_oriented: bool = writer_packet.get("jd_is_delivery_oriented", False)

    # 1. Parse roles
    roles = _parse_roles(resume)
    if not roles:
        return {"resume": resume, "cover_letter": cover_letter}

    # 2. Compute effective priorities (thin override + top-K promotion)
    effective_priorities = _compute_effective_priorities(
        roles, role_priorities, role_source_counts, role_source_char_counts,
        jd_is_delivery_oriented,
    )

    # 3. Get current mechanism counts per role
    role_mechanism_counts: dict[str, int] = {}
    if validation_report and "stats" in validation_report:
        stats_counts: dict[str, int] = validation_report["stats"].get(
            "role_mechanism_counts", {}
        )
        for role_header, _ in roles:
            role_mechanism_counts[role_header] = stats_counts.get(role_header, 0)
    else:
        for role_header, bullets in roles:
            role_mechanism_counts[role_header] = sum(
                1 for b in bullets if _bullet_has_arch_mechanism(b, must_surface)
            )

    # 4. Compute deficits; skip thin_override roles
    deficits: dict[str, int] = {}
    for role_header, _ in roles:
        effective_priority, _ = effective_priorities.get(role_header, ("low", ""))
        if effective_priority == "thin_override":
            continue
        min_mech = mechanism_min_map.get(effective_priority, 0)
        current = role_mechanism_counts.get(role_header, 0)
        deficit = max(0, min_mech - current)
        if deficit > 0:
            deficits[role_header] = deficit

    if not deficits:
        return {"resume": resume, "cover_letter": cover_letter}

    logger.debug(
        "Mechanism postprocessor: deficits=%s pool_size=%d",
        deficits, len(must_surface),
    )

    # 5. Find line-index ranges for all experience roles
    lines = resume.split("\n")
    role_ranges: dict[str, tuple[int, int]] = {}
    for role_header, content_start, content_end in _find_experience_role_sections(lines):
        role_ranges[role_header] = (content_start, content_end)

    # 6. Assign phrases to deficit roles (sorted by deficit descending)
    sorted_deficit_roles = sorted(deficits.items(), key=lambda x: x[1], reverse=True)

    assigned: dict[str, list[str]] = {}
    used_globally: set[str] = set()

    for role_header, deficit in sorted_deficit_roles:
        if role_header not in role_ranges:
            continue
        start, end = role_ranges[role_header]
        block_text = " ".join(lines[start:end]).lower()

        role_phrases: list[str] = []

        # First pass: prefer phrases not used in other roles and not in this block
        for phrase in must_surface:
            if len(role_phrases) >= deficit:
                break
            if phrase.lower() not in block_text and phrase not in used_globally:
                role_phrases.append(phrase)

        # Second pass: allow reuse if global pool exhausted, still avoid block duplicates
        if len(role_phrases) < deficit:
            for phrase in must_surface:
                if len(role_phrases) >= deficit:
                    break
                if phrase.lower() not in block_text and phrase not in role_phrases:
                    role_phrases.append(phrase)

        assigned[role_header] = role_phrases
        used_globally.update(role_phrases)

    # 7. Inject phrases into lines
    for role_header, phrases in assigned.items():
        if not phrases:
            continue
        start, end = role_ranges[role_header]
        lines = _inject_phrases_into_role_lines(lines, start, end, phrases)

    new_resume = "\n".join(lines)

    # 8. Post-check safety net: re-parse and do a second pass for any remaining deficits
    roles2 = _parse_roles(new_resume)
    role_mechanism_counts2: dict[str, int] = {
        rh: sum(1 for b in bullets if _bullet_has_arch_mechanism(b, must_surface))
        for rh, bullets in roles2
    }

    remaining_deficits: dict[str, int] = {}
    for role_header, _ in roles2:
        effective_priority, _ = effective_priorities.get(role_header, ("low", ""))
        if effective_priority == "thin_override":
            continue
        min_mech = mechanism_min_map.get(effective_priority, 0)
        current = role_mechanism_counts2.get(role_header, 0)
        deficit = max(0, min_mech - current)
        if deficit > 0:
            remaining_deficits[role_header] = deficit

    if remaining_deficits:
        logger.debug(
            "Mechanism postprocessor: second injection pass for roles: %s",
            list(remaining_deficits.keys()),
        )
        lines2 = new_resume.split("\n")
        role_ranges2: dict[str, tuple[int, int]] = {}
        for rh, cs, ce in _find_experience_role_sections(lines2):
            role_ranges2[rh] = (cs, ce)

        for role_header, deficit in sorted(
            remaining_deficits.items(), key=lambda x: x[1], reverse=True
        ):
            if role_header not in role_ranges2:
                continue
            start, end = role_ranges2[role_header]
            block_text = " ".join(lines2[start:end]).lower()
            extra_phrases = [
                p for p in must_surface if p.lower() not in block_text
            ][:deficit]
            if extra_phrases:
                lines2 = _inject_phrases_into_role_lines(lines2, start, end, extra_phrases)

        new_resume = "\n".join(lines2)

    return {"resume": new_resume, "cover_letter": cover_letter}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _find_experience_role_sections(
    lines: list[str],
) -> list[tuple[str, int, int]]:
    """Return ``[(role_header, content_start_idx, content_end_idx)]`` for Experience.

    Both indices are absolute positions into ``lines``.  ``content_start_idx``
    is the line immediately after the role header; ``content_end_idx`` is
    exclusive (the line of the next role header or the end of the Experience
    section, whichever comes first).

    Mirrors ``_parse_roles()`` logic: role headers are lines that contain '|'
    and do not start with '-' or '•'.
    """
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

    result: list[tuple[str, int, int]] = []
    current_header: str | None = None
    current_header_line: int = -1

    for i in range(exp_start, exp_end):
        s = lines[i].strip()
        if "|" in s and not s.startswith("-") and not s.startswith("•"):
            if current_header is not None:
                result.append((current_header, current_header_line + 1, i))
            current_header = normalize_role_header(s)
            current_header_line = i

    if current_header is not None:
        result.append((current_header, current_header_line + 1, exp_end))

    return result


def _is_bullet_line(line: str) -> bool:
    """Return True only for explicit bullet-marker lines (``-`` or ``•``).

    Injection targets must have an explicit bullet prefix so that prose lines,
    date lines, and section headers are never modified.
    """
    s = line.strip()
    return s.startswith("- ") or s.startswith("• ")


# Markers that indicate a bullet already carries an injected technique clause.
# Prefer bullets without these to avoid awkward stacking ("via X via Y").
_VIA_CLAUSE_MARKERS: tuple[str, ...] = (" via ", " using ", " through ")


def _has_via_clause(line: str) -> bool:
    """Return True if ``line`` already contains a via/using/through clause."""
    line_lower = line.lower()
    return any(marker in line_lower for marker in _VIA_CLAUSE_MARKERS)


def _inject_phrases_into_role_lines(
    lines: list[str],
    role_start: int,
    role_end: int,
    phrases: list[str],
) -> list[str]:
    """Append mechanism phrases to existing bullet lines within ``[role_start, role_end)``.

    For each phrase (skipped when already present in the block):

    - **Preferred target**: an unmodified bullet whose text contains a keyword
      from ``_MECHANISM_PREF_KEYWORDS`` (suggests the bullet already discusses
      something performance/infrastructure related).
    - **Fallback**: any unmodified bullet.
    - **Last resort**: the first bullet (even if already modified).

    Injection: ``lines[target] = lines[target].rstrip() + " via {phrase}"``.
    Tracks modified line indices to spread phrases across different bullets.

    Parameters
    ----------
    lines:
        Full resume line list (modified **in place** and returned).
    role_start, role_end:
        Absolute slice into ``lines`` covering the role's content (not the
        header line itself).
    phrases:
        Ordered list of phrases to inject.

    Returns
    -------
    list[str]
        The same ``lines`` object with injected content.
    """
    modified_indices: set[int] = set()

    for phrase in phrases:
        # Recompute block text each iteration so we catch the last injection
        block_text = " ".join(lines[role_start:role_end]).lower()

        if phrase.lower() in block_text:
            continue  # already present, skip

        target: int | None = None

        # Pass 1: unmodified + preferred keyword + no existing via-clause
        for i in range(role_start, role_end):
            if not _is_bullet_line(lines[i]):
                continue
            line_lower = lines[i].lower()
            if (
                i not in modified_indices
                and any(kw in line_lower for kw in _MECHANISM_PREF_KEYWORDS)
                and not _has_via_clause(lines[i])
            ):
                target = i
                break

        # Pass 2: unmodified + no existing via-clause (any bullet)
        if target is None:
            for i in range(role_start, role_end):
                if (
                    _is_bullet_line(lines[i])
                    and i not in modified_indices
                    and not _has_via_clause(lines[i])
                ):
                    target = i
                    break

        # Pass 3: any unmodified bullet (may already have a via-clause)
        if target is None:
            for i in range(role_start, role_end):
                if _is_bullet_line(lines[i]) and i not in modified_indices:
                    target = i
                    break

        # Pass 4: last resort — first bullet regardless of modification state
        if target is None:
            for i in range(role_start, role_end):
                if _is_bullet_line(lines[i]):
                    target = i
                    break

        if target is None:
            continue  # no bullet lines in this role at all

        bullet = lines[target].rstrip().rstrip(".,;:")
        lines[target] = bullet + f" via {_normalize_via_phrase(phrase)}"
        modified_indices.add(target)

    return lines
