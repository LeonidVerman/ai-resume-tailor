"""Deterministic post-processor for Phase 2 token compliance.

Inspects Phase 2 output after all LLM attempts and injects any remaining
required tokens (skills, metrics, date) into stable locations without
any LLM involvement.

Guarantees
----------
- Missing required skills are injected verbatim into Technical Skills.
- Missing required metrics are appended to the Professional Summary.
- Missing CURRENT_DATE is prepended to the cover letter.
- No token is injected unless it passes the support check against source docs.
- Tokens already present in the output are never duplicated.
- No bullets, roles, employers, titles, or role ordering are changed.
- Output always has exactly two keys: ``resume`` and ``cover_letter``.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Section header sets (mirrors phase2_validator.py)
# ---------------------------------------------------------------------------
_RESUME_SECTION_HEADERS: frozenset[str] = frozenset({
    "Experience", "Education", "Technical Skills", "Skills",
    "Certifications", "Projects", "Publications", "Summary",
    "Professional Summary", "Awards", "References", "Volunteer",
})
_SUMMARY_HEADERS: frozenset[str] = frozenset({"Professional Summary", "Summary"})
_SKILLS_HEADERS: frozenset[str] = frozenset({"Technical Skills", "Skills"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def postprocess_token_compliance(
    output_json: dict,
    writer_packet: dict,
    current_date: str,
    master_resume_text: str = "",
    candidate_profile_text: str = "",
) -> dict:
    """Deterministic post-processor for Phase 2 token compliance.

    Runs after all LLM attempts (writer + repair loop).  Injects any remaining
    required tokens into stable, predictable locations without LLM involvement.

    Parameters
    ----------
    output_json:
        Phase 2 output dict with keys ``resume`` and ``cover_letter``.
    writer_packet:
        WriterPacket dict (must_include_skills, must_keep_metrics, etc.).
    current_date:
        Date string required by the cover-letter validator.
    master_resume_text, candidate_profile_text:
        Source documents for the support check.  When both are empty the
        writer_packet is trusted as the sole source of truth (it was built
        from the same sources at plan-time and contains only supported tokens).

    Returns
    -------
    dict
        Updated ``{"resume": ..., "cover_letter": ...}`` dict.
    """
    resume = output_json.get("resume") or ""
    cover_letter = output_json.get("cover_letter") or ""

    # --- 1. Detect missing tokens ---
    missing_metrics = _compute_missing_metrics(writer_packet, resume)
    skills_text = _extract_skills_section_text(resume)
    missing_skills = _compute_missing_skills(writer_packet, skills_text)

    # --- 2. Support-check filter ---
    missing_metrics_ok = [
        m for m in missing_metrics
        if _is_supported(m, master_resume_text, candidate_profile_text, writer_packet)
    ]
    missing_skills_ok = [
        s for s in missing_skills
        if _is_supported(s, master_resume_text, candidate_profile_text, writer_packet)
    ]
    blocked_metrics = [m for m in missing_metrics if m not in missing_metrics_ok]
    blocked_skills = [s for s in missing_skills if s not in missing_skills_ok]

    if missing_metrics or missing_skills:
        logger.debug(
            "Postprocessor: missing metrics=%s (inject=%s, blocked=%s); "
            "missing skills=%s (inject=%s, blocked=%s)",
            missing_metrics, missing_metrics_ok, blocked_metrics,
            missing_skills, missing_skills_ok, blocked_skills,
        )

    # --- 3. Inject tokens ---
    if missing_metrics_ok:
        resume = _inject_metrics_into_summary(resume, missing_metrics_ok)
    if missing_skills_ok:
        resume = _inject_skills(resume, missing_skills_ok)
    cover_letter = _fix_cover_letter_date(cover_letter, current_date)

    # --- 4. Debug: report final state ---
    skills_text_after = _extract_skills_section_text(resume)
    still_missing_metrics = _compute_missing_metrics(writer_packet, resume)
    still_missing_skills = _compute_missing_skills(writer_packet, skills_text_after)
    if still_missing_metrics or still_missing_skills:
        logger.warning(
            "Postprocessor: still missing after injection — metrics: %s, skills: %s",
            still_missing_metrics, still_missing_skills,
        )
    else:
        logger.debug("Postprocessor: all required tokens satisfied after injection.")

    return {"resume": resume, "cover_letter": cover_letter}


# ---------------------------------------------------------------------------
# Detection helpers (mirror validator logic exactly)
# ---------------------------------------------------------------------------

def _compute_missing_metrics(writer_packet: dict, resume: str) -> list[str]:
    """Return required metrics absent from resume (validator-equivalent logic)."""
    from tailor.phase2_validator import _metric_found  # noqa: PLC0415

    return [
        m for m in writer_packet.get("must_keep_metrics", [])
        if not _metric_found(m, resume)
    ]


def _compute_missing_skills(writer_packet: dict, skills_text: str) -> list[str]:
    """Return required skills absent from the skills section (validator-equivalent logic)."""
    skills_lower = skills_text.lower()
    return [
        s for s in writer_packet.get("must_include_skills", [])
        if s.lower() not in skills_lower
    ]


def _extract_skills_section_text(resume: str) -> str:
    """Extract the text body of the Technical Skills / Skills section."""
    lines = resume.split("\n")
    in_skills = False
    skill_lines: list[str] = []
    for line in lines:
        s = line.strip()
        if s in _SKILLS_HEADERS:
            in_skills = True
            continue
        if in_skills and s in _RESUME_SECTION_HEADERS:
            break
        if in_skills and s:
            skill_lines.append(s)
    return "\n".join(skill_lines)


# ---------------------------------------------------------------------------
# Support check
# ---------------------------------------------------------------------------

def _canonicalize(token: str) -> str:
    """Canonical form: lowercase, hyphens/underscores → space, collapse whitespace."""
    s = token.lower().replace("-", " ").replace("_", " ")
    return re.sub(r"\s+", " ", s).strip()


def _is_supported(
    token: str,
    master_resume_text: str,
    candidate_profile_text: str,
    writer_packet: dict,
) -> bool:
    """Return True if token is supported by source docs or the writer_packet.

    When no source texts are provided, the writer_packet is trusted (it was
    derived from source documents at plan-time and should only contain tokens
    that were verified as supported then).
    """
    if not master_resume_text and not candidate_profile_text:
        return True  # trust writer_packet

    combined = master_resume_text + "\n" + candidate_profile_text

    # Verbatim case-insensitive substring
    if token.lower() in combined.lower():
        return True

    # Canonical match (normalise hyphens, underscores, case)
    if _canonicalize(token) in _canonicalize(combined):
        return True

    # allowed_skill_pool from writer_packet as additional source of truth
    for pooled in writer_packet.get("allowed_skill_pool", []):
        if token.lower() == pooled.lower() or _canonicalize(token) == _canonicalize(pooled):
            return True

    return False


# ---------------------------------------------------------------------------
# Injection helpers
# ---------------------------------------------------------------------------

def _inject_metrics_into_summary(resume: str, metrics: list[str]) -> str:
    """Append missing metric tokens to the Professional Summary section.

    Appends a single parenthetical ``(token1; token2; ...)`` after the last
    non-empty body line of the summary section.

    Fallback: if no summary section is found, inserts the parenthetical before
    the first known section header (preserves name/contact block at top).
    Last resort: appends to end of resume.
    """
    if not metrics:
        return resume

    lines = resume.split("\n")
    payload = "(" + "; ".join(metrics) + ")"

    # Find the summary header
    summary_header_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() in _SUMMARY_HEADERS:
            summary_header_idx = i
            break

    if summary_header_idx is not None:
        # Find last non-empty body line before the next section header
        last_body_idx: int | None = None
        for i in range(summary_header_idx + 1, len(lines)):
            s = lines[i].strip()
            if s in _RESUME_SECTION_HEADERS:
                break
            if s:
                last_body_idx = i

        if last_body_idx is not None:
            lines[last_body_idx] = lines[last_body_idx].rstrip() + " " + payload
            logger.debug(
                "Postprocessor: injected metrics into summary line %d: %s",
                last_body_idx, payload,
            )
            return "\n".join(lines)

    # Fallback: insert before the first known section header
    for i, line in enumerate(lines):
        if line.strip() in _RESUME_SECTION_HEADERS:
            lines.insert(i, payload)
            lines.insert(i, "")
            logger.debug(
                "Postprocessor: injected metrics before section header at line %d: %s",
                i, payload,
            )
            return "\n".join(lines)

    # Last resort: append to end of resume
    lines.append(payload)
    logger.debug("Postprocessor: injected metrics at end of resume: %s", payload)
    return "\n".join(lines)


def _inject_skills(resume: str, skills: list[str]) -> str:
    """Inject missing skill tokens into the Technical Skills / Skills section.

    Strategy (in order of preference):
    1. Append to an existing ``Other:`` line inside the skills block.
    2. Insert a new ``Other: token1, token2`` line at the end of the skills block.
    3. If no skills section exists, append a minimal ``Technical Skills`` section.
    """
    if not skills:
        return resume

    lines = resume.split("\n")
    new_tokens = ", ".join(skills)

    # Locate skills section boundaries
    skills_header_idx: int | None = None
    skills_end_idx = len(lines)
    for i, line in enumerate(lines):
        s = line.strip()
        if s in _SKILLS_HEADERS:
            skills_header_idx = i
        elif skills_header_idx is not None and s in (_RESUME_SECTION_HEADERS - _SKILLS_HEADERS):
            skills_end_idx = i
            break

    if skills_header_idx is not None:
        # Search for an existing "Other:" line
        other_line_idx: int | None = None
        for i in range(skills_header_idx + 1, skills_end_idx):
            if lines[i].strip().lower().startswith("other:"):
                other_line_idx = i
                break

        if other_line_idx is not None:
            existing = lines[other_line_idx].rstrip()
            sep = " " if existing.endswith(",") else ", "
            lines[other_line_idx] = existing + sep + new_tokens
            logger.debug(
                "Postprocessor: appended skills to Other: line %d: %s",
                other_line_idx, new_tokens,
            )
        else:
            # Find last non-empty line in skills block and insert after it
            insert_idx = skills_end_idx
            for i in range(skills_end_idx - 1, skills_header_idx, -1):
                if lines[i].strip():
                    insert_idx = i + 1
                    break
            lines.insert(insert_idx, f"Other: {new_tokens}")
            logger.debug(
                "Postprocessor: inserted new Other: line at %d: %s",
                insert_idx, new_tokens,
            )

        return "\n".join(lines)

    # No skills section found — append a minimal one at end of resume
    lines.append("")
    lines.append("Technical Skills")
    lines.append(f"Other: {new_tokens}")
    logger.debug(
        "Postprocessor: no skills section found; appended minimal section: %s",
        new_tokens,
    )
    return "\n".join(lines)


def _fix_cover_letter_date(cover_letter: str, current_date: str) -> str:
    """Prepend current_date to cover letter if not already present."""
    if not current_date or current_date in cover_letter:
        return cover_letter
    logger.debug(
        "Postprocessor: prepending current_date to cover letter: %s", current_date
    )
    return current_date + "\n\n" + cover_letter
