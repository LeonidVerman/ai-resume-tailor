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
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ledger validation error prefixes (exported for use by llm.py)
# ---------------------------------------------------------------------------

#: Prefix for errors where exact_span is in the doc but differs from target.
#: These errors are judge-eligible after the repair loop.
LEDGER_MISMATCH_ERROR_PREFIX: str = "LEDGER_SPAN_MISMATCH"

#: Prefix for errors where the ledger's exact_span is not found in the document.
LEDGER_SPAN_NOT_FOUND_PREFIX: str = "LEDGER_SPAN_NOT_FOUND"

#: Prefix for errors where a required item has no ledger entry at all.
LEDGER_ENTRY_MISSING_PREFIX: str = "LEDGER_ENTRY_MISSING"

# ---------------------------------------------------------------------------
# Mechanism keyword set — LEGACY FALLBACK only.
#
# Used by _bullet_has_arch_mechanism() when must_surface_arch_mechanisms is
# absent (e.g. old WriterPackets or unit tests that don't populate the field).
# Do NOT add bare tool names here: "aws", "docker", "kubernetes" etc. inflate
# mechanism counts and mask real deficits in role blocks.
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
    # Specific infrastructure keywords (kept — meaningful enough as architecture signals)
    "kafka",
    "redis",
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
# Manager-level density vocabulary
# ---------------------------------------------------------------------------

#: Leadership action verbs whose presence (as the first word of a bullet)
#: signals a leadership-framed bullet for manager-level density checks.
_LEADERSHIP_VERBS: frozenset[str] = frozenset({
    "led", "managed", "mentored", "guided", "coordinated", "partnered", "improved",
})

#: Delivery vocabulary for manager-level JD-matching checks.
_DELIVERY_VOCAB: frozenset[str] = frozenset({
    "backlog", "roadmap", "timeline", "risk",
})


# ---------------------------------------------------------------------------
# Ledger-driven validation helpers
# ---------------------------------------------------------------------------

def _build_ledger_index(evidence_ledger: dict) -> dict[tuple[str, str], dict]:
    """Return ``{(kind, target): entry}`` from ``evidence_ledger.entries``.

    When multiple entries share the same ``(kind, target)``, the last one wins.
    Unknown/empty kinds or targets are silently skipped.
    """
    index: dict[tuple[str, str], dict] = {}
    for entry in evidence_ledger.get("entries", []):
        kind = entry.get("kind", "")
        target = entry.get("target", "")
        if kind and target:
            index[(kind, target)] = entry
    return index


def _check_ledger_honesty(entry: dict, resume: str, cover_letter: str) -> bool:
    """Return True if ``exact_span`` is a substring of the expected document(s).

    Location semantics:
        - ``"resume"``       → span must appear in *resume*.
        - ``"cover_letter"`` → span must appear in *cover_letter*.
        - ``"both"``         → span must appear in at least one of the two.

    An empty ``exact_span`` always returns False.
    Matching is case-insensitive.
    """
    span = entry.get("exact_span", "")
    if not span:
        return False
    location = entry.get("location", "")
    span_lower = span.lower()
    if location == "resume":
        return span_lower in resume.lower()
    if location == "cover_letter":
        return span_lower in cover_letter.lower()
    if location == "both":
        return span_lower in resume.lower() or span_lower in cover_letter.lower()
    return False


def _check_ledger_requirement(
    kind: str,
    target: str,
    ledger_index: dict[tuple[str, str], dict],
    resume: str,
    cover_letter: str,
) -> dict[str, Any]:
    """Evaluate one required item against the ledger and return a result dict.

    Return keys
    -----------
    satisfied : bool
        True when honesty passes **and** ``exact_span`` case-insensitively
        matches ``target`` (fully resolved — no judge needed).
    mismatch : bool
        True when honesty passes but ``exact_span != target`` (judge-eligible).
    error : str
        Non-empty when neither satisfied nor mismatch.
    entry_id : str
        Ledger entry ``id`` (empty when no entry).
    location : str
        Ledger entry ``location`` (empty when no entry).
    span : str
        ``exact_span`` from ledger (empty when no entry).
    """
    entry = ledger_index.get((kind, target))
    if entry is None:
        return {
            "satisfied": False,
            "mismatch": False,
            "error": (
                f"{LEDGER_ENTRY_MISSING_PREFIX} for {kind} '{target}': "
                f"no ledger entry provided"
            ),
            "entry_id": "",
            "location": "",
            "span": "",
        }

    location = entry.get("location", "")
    exact_span = entry.get("exact_span", "")
    entry_id = entry.get("id", f"{kind}_{target}")

    if location == "missing":
        return {
            "satisfied": False,
            "mismatch": False,
            "error": (
                f"Required {kind.replace('_', ' ')} '{target}' is missing per ledger"
            ),
            "entry_id": entry_id,
            "location": location,
            "span": exact_span,
        }

    if not _check_ledger_honesty(entry, resume, cover_letter):
        return {
            "satisfied": False,
            "mismatch": False,
            "error": (
                f"{LEDGER_SPAN_NOT_FOUND_PREFIX}: exact_span {exact_span!r} "
                f"for {kind} '{target}' not found in {location}"
            ),
            "entry_id": entry_id,
            "location": location,
            "span": exact_span,
        }

    # Honesty check passes — compare wording
    if exact_span.lower() == target.lower():
        return {
            "satisfied": True,
            "mismatch": False,
            "error": "",
            "entry_id": entry_id,
            "location": location,
            "span": exact_span,
        }

    # Honesty passes but wording differs → judge-eligible mismatch
    return {
        "satisfied": False,
        "mismatch": True,
        "error": "",
        "entry_id": entry_id,
        "location": location,
        "span": exact_span,
    }


# ---------------------------------------------------------------------------
# Post-judge application
# ---------------------------------------------------------------------------

def apply_judge_to_validation(
    report: dict,
    judge_verdicts: dict[str, bool],
) -> dict:
    """Return an updated ValidationReport with judge-approved mismatches cleared.

    Parameters
    ----------
    report:
        The ValidationReport produced by ``validate_phase2_output``.
    judge_verdicts:
        ``{candidate_id: verdict_bool}`` from the judge.
        ``True`` means the mismatch is semantically approved.

    Returns
    -------
    An updated copy of ``report`` with approved ``LEDGER_SPAN_MISMATCH`` errors
    removed.  ``ok`` is recomputed.  Each ``judge_candidates`` entry gains a
    ``judge_verdict`` key with the corresponding boolean (or ``None``).
    """
    if not judge_verdicts:
        return report

    approved_ids = {cid for cid, ok in judge_verdicts.items() if ok}
    if not approved_ids:
        return report

    _prefix_bracket = LEDGER_MISMATCH_ERROR_PREFIX + "["
    new_errors: list[str] = []
    for err in report.get("errors", []):
        if err.startswith(_prefix_bracket):
            try:
                bracket_end = err.index("]")
                error_id = err[len(_prefix_bracket): bracket_end]
                if error_id in approved_ids:
                    continue  # judge approved — remove this error
            except ValueError:
                pass  # malformed prefix — keep the error
        new_errors.append(err)

    updated_candidates = [
        {**c, "judge_verdict": judge_verdicts.get(c.get("id", ""))}
        for c in report.get("judge_candidates", [])
    ]

    updated = dict(report)
    updated["errors"] = new_errors
    updated["ok"] = len(new_errors) == 0
    updated["judge_candidates"] = updated_candidates
    return updated


# ---------------------------------------------------------------------------
# Role-preservation helpers (A2)
# ---------------------------------------------------------------------------

def _extract_earlier_roles_block(resume: str) -> list[str]:
    """Return the collapsed role lines from an "Earlier roles" block, if present.

    Looks for a line equal to "Earlier roles" in the Experience section, then
    collects subsequent non-empty lines that contain "|" (the collapsed entries
    in "Company | Title | Years" format) until the next known section header.
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

    earlier_lines: list[str] = []
    in_earlier_block = False
    for line in lines[exp_start:exp_end]:
        s = line.strip()
        if not s:
            continue
        if s == "Earlier roles":
            in_earlier_block = True
            continue
        if in_earlier_block:
            if s in _RESUME_SECTION_HEADERS:
                break
            if "|" in s:
                earlier_lines.append(s)
    return earlier_lines


def _role_found_in_output(
    master_role: str,
    output_role_headers: list[str],
    earlier_lines: list[str],
) -> bool:
    """Return True if a master-resume role is represented in the output.

    Two checks (either is sufficient):
    1. Fuzzy header match — ``_roles_match`` against any full role header.
    2. Earlier-roles block match — meaningful name parts appear in the
       collapsed-block text (handles format differences like Company | Title | Years
       vs Title | Company | Date).
    """
    # 1. Full role header match
    for header in output_role_headers:
        if _roles_match(master_role, header):
            return True

    # 2. Earlier-roles block — extract non-year name parts and look for them
    if not earlier_lines:
        return False

    earlier_block_lower = " ".join(earlier_lines).lower()
    master_parts = [
        p.strip()
        for p in normalize_role_header(master_role).split("|")
    ]
    for part in master_parts:
        # Strip 4-digit year tokens from the part before substring checking
        part_clean = re.sub(r"\b(19|20)\d{2}\b", "", part).strip()
        if len(part_clean) > 3 and part_clean.lower() in earlier_block_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_phase2_output(
    writer_packet: dict,
    resume: str,
    cover_letter: str,
    current_date: str,
    now: date | None = None,
    evidence_ledger: dict | None = None,
) -> dict:
    """Check Phase 2 output against WriterPacket constraints.

    Returns a ValidationReport dict with keys:
        ok, errors, warnings, stats, repair_brief, judge_candidates, ledger_findings.
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
    evidence_ledger:
        Optional evidence ledger produced by the Phase 2 writer.  When
        provided, required-skill and required-metric checks use ledger-driven
        validation (allowing rewording) instead of verbatim substring search.
        Unsafe-noun enforcement is always verbatim regardless of the ledger.
    """
    if now is None:
        now = date.today()

    errors: list[str] = []
    warnings: list[str] = []

    # --- Ledger setup ---
    judge_candidates: list[dict] = []
    ledger_index: dict[tuple[str, str], dict] = {}
    if evidence_ledger is not None:
        ledger_index = _build_ledger_index(evidence_ledger)
    ledger_findings: dict = {
        "ledger_present": evidence_ledger is not None,
        "entry_count": len(ledger_index),
    }

    role_level: str = writer_packet.get("role_level", "senior")
    role_priorities: dict[str, str] = writer_packet.get("role_priorities", {})
    role_source_counts: dict[str, int] = writer_packet.get("role_source_bullet_counts", {})
    role_source_char_counts: dict[str, int] = writer_packet.get("role_source_char_counts", {})
    jd_is_delivery_oriented: bool = writer_packet.get("jd_is_delivery_oriented", False)
    arch_mechanisms: list[str] = writer_packet.get("must_surface_arch_mechanisms", [])
    arch_mechanisms_primary: list[str] = writer_packet.get("arch_mechanisms_primary", arch_mechanisms)
    arch_mechanisms_backstop: list[str] = writer_packet.get("arch_mechanisms_backstop", [])
    allowance_map: dict[str, int] = writer_packet.get("role_density_shortfall_allowance", {})
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

    # --- 1. Metrics preservation ---
    metrics_found: list[str] = []
    missing_metrics: list[str] = []
    if evidence_ledger is not None:
        for metric in writer_packet.get("must_keep_metrics", []):
            res = _check_ledger_requirement(
                "required_metric", metric, ledger_index, resume, cover_letter
            )
            if res["satisfied"]:
                metrics_found.append(metric)
            elif res["mismatch"]:
                entry_id = res["entry_id"]
                errors.append(
                    f"{LEDGER_MISMATCH_ERROR_PREFIX}[{entry_id}]: "
                    f"target '{metric}', span '{res['span']}' "
                    f"in {res['location']} (judge-eligible)"
                )
                judge_candidates.append({
                    "id": entry_id,
                    "kind": "required_metric",
                    "target": metric,
                    "location": res["location"],
                    "exact_span": res["span"],
                })
                missing_metrics.append(metric)
            else:
                errors.append(res["error"])
                missing_metrics.append(metric)
    else:
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

    repair_roles: list[dict] = []
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

        allow = _find_role_allowance(role_header, allowance_map)
        effective_min_bullets = max(1, min_bullets - allow)
        if bullet_count < effective_min_bullets:
            error_msg = (
                f"Role {role_header!r} ({effective_priority} priority) has {bullet_count} "
                f"bullet(s); minimum is {min_bullets}"
            )
            if allow:
                error_msg += f" (allowance {allow} applied → effective {effective_min_bullets})"
            errors.append(error_msg)
        elif bullet_count > 6 and effective_priority == "high":
            warnings.append(
                f"Role {role_header!r} has {bullet_count} bullets; "
                f"consider trimming to 6"
            )

        mech_count = sum(1 for b in bullets if _bullet_has_arch_mechanism(b, arch_mechanisms))
        role_mechanism_counts[role_header] = mech_count
        if mech_required > 0 and mech_count < mech_required:
            errors.append(
                f"Role {role_header!r} ({effective_priority} priority) has {mech_count} "
                f"mechanism(s) in bullets; minimum is {mech_required}"
            )

        # A3T1: mechanism placement — at least 1 mechanism in first 2 bullets
        # of high-priority roles (warning only; escalate to error once stable).
        if effective_priority == "high" and mech_required > 0 and len(bullets) >= 1:
            first_two_mech = sum(
                1 for b in bullets[:2] if _bullet_has_arch_mechanism(b, arch_mechanisms)
            )
            if first_two_mech == 0:
                warnings.append(
                    f"Role {role_header!r}: no mechanism found in first 2 bullets; "
                    f"consider leading with a mechanism bullet"
                )

        # Accumulate per-role repair data for the machine-readable repair brief.
        repair_roles.append({
            "role_header": role_header,
            "effective_priority": effective_priority,
            "bullets": {
                "have": bullet_count,
                "need": min_bullets,
                "allowance": allow,
                "effective_min": effective_min_bullets,
                "deficit": max(0, effective_min_bullets - bullet_count),
            },
            "mechanisms": {
                "have": mech_count,
                "need": mech_required,
                "deficit": max(0, mech_required - mech_count),
            },
            "allowed_phrases": {
                "arch_mechanisms_primary": arch_mechanisms_primary,
                "arch_mechanisms_backstop": arch_mechanisms_backstop,
                "strategic_signals": (
                    writer_packet.get("must_surface_strategic_signals", [])
                    if role_level == "director" else []
                ),
                "operational_signals": (
                    writer_packet.get("must_surface_operational_signals", [])
                    if role_level == "director" else []
                ),
            },
            "action_plan": {
                "add_bullets": max(0, effective_min_bullets - bullet_count),
                "rewrite_bullets_for_mechanisms": max(0, mech_required - mech_count),
                "split_bullets": 1 if max(0, effective_min_bullets - bullet_count) > 0 else 0,
            },
        })

        # Advisory: high-priority role appearing after a medium-priority role.
        display_priority = "thin" if effective_priority == "thin_override" else effective_priority
        if prev_display_priority == "medium" and display_priority == "high":
            warnings.append(
                f"Advisory: high-priority role {role_header!r} appears after a "
                f"medium-priority role; verify planner intent"
            )
        prev_display_priority = display_priority

    # --- 3. Required skills retention ---
    missing_required_skills: list[str] = []
    if evidence_ledger is not None:
        for skill in writer_packet.get("must_include_skills", []):
            res = _check_ledger_requirement(
                "required_skill", skill, ledger_index, resume, cover_letter
            )
            if res["satisfied"]:
                pass  # fully resolved
            elif res["mismatch"]:
                entry_id = res["entry_id"]
                errors.append(
                    f"{LEDGER_MISMATCH_ERROR_PREFIX}[{entry_id}]: "
                    f"target '{skill}', span '{res['span']}' "
                    f"in {res['location']} (judge-eligible)"
                )
                judge_candidates.append({
                    "id": entry_id,
                    "kind": "required_skill",
                    "target": skill,
                    "location": res["location"],
                    "exact_span": res["span"],
                })
                missing_required_skills.append(skill)
            else:
                errors.append(res["error"])
                missing_required_skills.append(skill)
    else:
        for skill in writer_packet.get("must_include_skills", []):
            if skill.lower() not in skills_text.lower():
                missing_required_skills.append(skill)
        if missing_required_skills:
            errors.append(f"Missing required skills: {', '.join(missing_required_skills)}")

    # --- 4. Unsafe JD nouns ---
    # Primary enforcement: always verbatim.  The ledger adds an extra honesty
    # check: if the ledger claims an unsafe noun is present, that is also an error.
    allowed_pool_lower = {s.lower() for s in writer_packet.get("allowed_skill_pool", [])}
    unsafe_terms_found: list[str] = []
    for term in writer_packet.get("unsafe_jd_nouns", []):
        term_lower = term.lower()
        verbatim_present = term_lower in resume.lower() and term_lower not in allowed_pool_lower
        if verbatim_present:
            unsafe_terms_found.append(term)
        # Ledger safety check: if ledger entry claims the noun is present, flag it.
        if evidence_ledger is not None:
            unsafe_entry = ledger_index.get(("unsafe_noun_check", term))
            if (
                unsafe_entry is not None
                and unsafe_entry.get("location", "missing") != "missing"
            ):
                errors.append(
                    f"LEDGER_UNSAFE_PRESENT: ledger claims unsafe noun '{term}' "
                    f"is at {unsafe_entry.get('location')} (must be absent)"
                )
                if term not in unsafe_terms_found:
                    unsafe_terms_found.append(term)
    if unsafe_terms_found:
        errors.append(
            f"Unsafe JD nouns found in resume: {', '.join(unsafe_terms_found)}"
        )

    # --- 5. Date correctness (unchanged) ---
    cover_letter_date_missing: bool = bool(current_date and current_date not in cover_letter)
    if cover_letter_date_missing:
        errors.append(f"Cover letter does not contain CURRENT_DATE: {current_date!r}")

    # --- 6. Soft director checks (warnings only; upgrade to errors once stable) ---
    strategic_signal_count: int = 0
    operational_signal_count: int = 0
    if role_level == "director":
        strategic_signals: list[str] = writer_packet.get("must_surface_strategic_signals", [])
        operational_signals: list[str] = writer_packet.get("must_surface_operational_signals", [])
        strategic_signal_count = _count_signal_matches(resume, strategic_signals)
        operational_signal_count = _count_signal_matches(resume, operational_signals)

        min_strategic = 2
        min_operational = 1
        if strategic_signal_count < min_strategic:
            warnings.append(
                f"Director role: only {strategic_signal_count} strategic signal(s) detected "
                f"in resume (recommended: {min_strategic}). "
                f"Consider adding leadership/vision framing."
            )
        if operational_signal_count < min_operational:
            warnings.append(
                f"Director role: only {operational_signal_count} operational signal(s) detected "
                f"in resume (recommended: {min_operational}). "
                f"Consider adding process/quality/delivery framing."
            )

    # --- 7. Manager-level leadership/delivery density (warnings only) ---
    manager_leadership_count: int = 0
    if role_level == "manager":
        for role_header, bullets in roles:
            eff_priority, _ = effective_priorities.get(role_header, ("low", ""))
            if eff_priority == "high":
                for bullet in bullets:
                    first_word = (
                        bullet.strip().split()[0].lower().rstrip(",")
                        if bullet.strip() else ""
                    )
                    if first_word in _LEADERSHIP_VERBS:
                        manager_leadership_count += 1

        if manager_leadership_count < 2:
            warnings.append(
                f"Manager role: only {manager_leadership_count} leadership-verb bullet(s) "
                f"in high-priority roles (recommended: ≥2). "
                f"Consider using Led / Managed / Mentored / Guided."
            )
        if jd_is_delivery_oriented:
            delivery_found = any(v in resume.lower() for v in _DELIVERY_VOCAB)
            if not delivery_found:
                warnings.append(
                    "Manager role with delivery-oriented JD: "
                    "no delivery vocabulary (backlog/roadmap/timeline/risk) found in resume."
                )

    # --- 8. Role preservation: every master-resume role must appear in output ---
    master_role_names: list[str] = writer_packet.get("master_resume_role_names", [])
    missing_roles: list[str] = []
    if master_role_names:
        output_role_headers = [h for h, _ in roles]
        earlier_lines = _extract_earlier_roles_block(resume)
        for master_role in master_role_names:
            if not _role_found_in_output(master_role, output_role_headers, earlier_lines):
                missing_roles.append(master_role)
        if missing_roles:
            errors.append(f"MISSING_ROLE: {'; '.join(missing_roles)}")

    # --- 9. JD vocabulary anchoring ---
    jd_vocab_must_embed: list[str] = writer_packet.get("jd_vocab_must_embed", [])
    vocab_anchors_found: int = 0
    if jd_vocab_must_embed:
        resume_lower = resume.lower()
        vocab_anchors_found = sum(
            1 for anchor in jd_vocab_must_embed if anchor.lower() in resume_lower
        )
        if vocab_anchors_found < 2:
            errors.append(
                f"JD_VOCAB_MISSING: found {vocab_anchors_found}/2 required anchors "
                f"from jd_vocab_must_embed"
            )

    repair_brief: dict = {
        "global_issues": {
            "missing_metrics": missing_metrics,
            "missing_skills": missing_required_skills,
            "unsafe_nouns_in_resume": unsafe_terms_found,
            "cover_letter_date_missing": cover_letter_date_missing,
            "missing_roles": missing_roles,
            "missing_vocab_anchors": (
                [a for a in jd_vocab_must_embed if a.lower() not in resume.lower()]
                if jd_vocab_must_embed else []
            ),
        },
        "roles": repair_roles,
    }

    ledger_findings["judge_candidate_count"] = len(judge_candidates)

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
            "strategic_signal_count": strategic_signal_count,
            "operational_signal_count": operational_signal_count,
            "manager_leadership_count": manager_leadership_count,
            "vocab_anchors_found": vocab_anchors_found,
        },
        "repair_brief": repair_brief,
        "judge_candidates": judge_candidates,
        "ledger_findings": ledger_findings,
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


def _bullet_has_arch_mechanism(text: str, arch_mechanisms: list[str]) -> bool:
    """Return True if bullet text contains an arch mechanism.

    Primary check: verbatim substring match against the WriterPacket's
    ``must_surface_arch_mechanisms`` list (case-insensitive).
    Fallback: the general ``_MECHANISM_KEYWORDS`` set (backward compat when
    the arch list is empty or missing).
    """
    text_lower = text.lower()
    if arch_mechanisms:
        if any(phrase.lower() in text_lower for phrase in arch_mechanisms):
            return True
    return any(kw in text_lower for kw in _MECHANISM_KEYWORDS)


def _count_signal_matches(resume: str, signals: list[str]) -> int:
    """Count how many distinct signals from the list appear in the resume.

    Matching is case-insensitive substring.  Each unique phrase is counted
    at most once regardless of how many times it appears.
    """
    if not signals:
        return 0
    text_lower = resume.lower()
    return sum(1 for phrase in signals if phrase.lower() in text_lower)


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


def _find_role_allowance(
    role_header: str,
    allowance_map: dict[str, int],
) -> int:
    """Return bullet-count shortfall allowance for a role; 0 if not specified."""
    for plan_name, allow in allowance_map.items():
        if _roles_match(plan_name, role_header):
            return allow
    return 0


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
