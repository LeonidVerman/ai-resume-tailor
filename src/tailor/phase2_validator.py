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

from tailor.cover_letter import parse_cover_letter, parse_cover_letter_body

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

#: Error code when a role appears in output that is not in the master resume role list.
EMPLOYER_INTEGRITY_NEW_ROLE: str = "EMPLOYER_INTEGRITY_NEW_ROLE"

#: Error code when an output role's date range differs from the master resume.
DATE_INTEGRITY_MODIFIED: str = "DATE_INTEGRITY_MODIFIED"

#: Narrative validator error codes.
NARRATIVE_SUMMARY_THEME_MISSING: str = "NARRATIVE_SUMMARY_THEME_MISSING"
NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED: str = "NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED"
DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET: str = "DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET"
DOMAIN_TRANSLATION_ANCHOR_NOT_MET: str = "DOMAIN_TRANSLATION_ANCHOR_NOT_MET"
DOMAIN_TRANSLATION_FIRST_K_NOT_MET: str = "DOMAIN_TRANSLATION_FIRST_K_NOT_MET"

#: Skill graph validator error codes.
SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION: str = "SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION"
SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION: str = "SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION"

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

    # Also filter structured_errors for approved mismatches.
    new_structured: list[dict] = [
        e for e in report.get("structured_errors", [])
        if not (
            e.get("code", "").startswith(LEDGER_MISMATCH_ERROR_PREFIX)
            and e.get("payload", {}).get("entry_id", "") in approved_ids
        )
    ]

    updated = dict(report)
    updated["errors"] = new_errors
    updated["structured_errors"] = new_structured
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
# Employer integrity helpers
# ---------------------------------------------------------------------------

def _normalize_date_str(d: str) -> str:
    """Normalise a date-range string for comparison.

    Collapses spacing and unifies dash variants (–, —, -) so that
    "Nov 2024 – Sep 2025" and "Nov 2024 - Sep 2025" compare equal.
    """
    d = re.sub(r"\s*[–\-—]\s*", " - ", d)
    return " ".join(d.split()).lower()


def _employer_integrity_match(output_header: str, master_header: str) -> bool:
    """Return True when *output_header* is an acceptable variant of *master_header*.

    Stricter than ``_roles_match``: the company part (last non-date pipe-segment)
    must be an exact normalised match so that subtle company-name changes (e.g.
    "CardinalChain Software" vs "CardinalChain Software Inc") are flagged.
    The title part (first pipe-segment) allows substring matching to accommodate
    plan-abbreviated multi-component titles.

    Date segments (parts containing a 4-digit year) are stripped before comparison
    so that inline date suffixes in output headers (e.g. "| Nov 2024 – Sep 2025")
    do not cause false positives.

    Falls back to a bidirectional substring test when either header has fewer
    than two non-date pipe-separated segments.
    """
    out_norm = normalize_role_header(output_header).lower()
    master_norm = normalize_role_header(master_header).lower()

    if out_norm == master_norm:
        return True

    def _non_date_parts(norm: str) -> list[str]:
        return [p.strip() for p in norm.split(" | ") if not _YEAR_RE.search(p)]

    out_parts = _non_date_parts(out_norm)
    master_parts = _non_date_parts(master_norm)

    if len(out_parts) < 2 or len(master_parts) < 2:
        out_clean = " | ".join(out_parts)
        master_clean = " | ".join(master_parts)
        return out_clean in master_clean or master_clean in out_clean

    # Company (last non-date segment) must match exactly.
    if out_parts[-1] != master_parts[-1]:
        return False

    # Title (first segment) allows substring — the plan may abbreviate long titles.
    return out_parts[0] in master_parts[0] or master_parts[0] in out_parts[0]


def _earlier_role_entry_matches_master(
    entry_header: str,
    master_role_names: list[str],
) -> bool:
    """Return True when a compressed "Earlier roles" entry corresponds to a master role.

    "Earlier roles" entries use a reversed format (Company | Title | Year), so
    ``_employer_integrity_match`` can fail.  This function instead finds the
    first meaningful part (the company, which is typically the first segment
    after stripping years and dashes) and checks whether it appears as a
    substring in any normalised master role name.

    Returns True conservatively when no meaningful part can be extracted (so as
    not to generate false-positive violations for degenerate entries).
    """
    parts = [p.strip() for p in entry_header.split(" | ")]
    for part in parts:
        # Strip year tokens and dash characters, then collapse whitespace.
        clean = re.sub(r"\b(19|20)\d{2}\b", "", part)
        clean = re.sub(r"[–\-—]", " ", clean).strip()
        clean = " ".join(clean.split())
        if len(clean) <= 3:
            continue
        # First meaningful part is typically the company name — check it.
        clean_lower = clean.lower()
        for master in master_role_names:
            if clean_lower in normalize_role_header(master).lower():
                return True
        return False  # First meaningful part not found in any master role.
    return True  # Nothing meaningful to evaluate; conservatively allow.


# ---------------------------------------------------------------------------
# Narrative validator helpers
# ---------------------------------------------------------------------------

def _extract_summary(resume: str) -> str:
    """Return the text of the Professional Summary / Summary section."""
    lines = resume.split("\n")
    in_summary = False
    result: list[str] = []
    for line in lines:
        s = line.strip()
        if s in ("Professional Summary", "Summary"):
            in_summary = True
            continue
        if in_summary:
            if s in _RESUME_SECTION_HEADERS:
                break
            if s:
                result.append(s)
    return " ".join(result)


def _extract_anchor_role_bullets(
    roles: list[tuple[str, list[str]]],
    anchor_role_id: str,
    first_k: int,
) -> list[str]:
    """Return the first *first_k* bullets from the anchor role (fuzzy name match)."""
    for role_header, bullets in roles:
        if _roles_match(role_header, anchor_role_id):
            return bullets[:first_k]
    return []


def _count_signature_hits(text: str, signature_terms: list[str]) -> int:
    """Count how many signature terms appear in *text* (case-insensitive substring)."""
    text_norm = " ".join(text.lower().split())
    return sum(1 for term in signature_terms if term.lower() in text_norm)


# ---------------------------------------------------------------------------
# Cover-letter validator helpers (V1–V4)
# ---------------------------------------------------------------------------

def _split_cover_letter_paragraphs(text: str) -> list[str]:
    """Split cover letter text into paragraphs at blank lines."""
    return [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]


def _validate_cl_paragraph_count(
    paragraphs: list[str],
    bridge_required: bool,
) -> list[str]:
    """V1: Ensure correct paragraph count.

    When bridge_sentence_required=true → exactly 4 paragraphs required.
    Otherwise               → 3 or 4 paragraphs are accepted.

    Error includes diagnostic payload: expected, actual, body_preview,
    body_paragraphs_lengths for easier repair-loop debugging.
    """
    n = len(paragraphs)
    lengths = [len(p) for p in paragraphs]
    body_preview = " | ".join(paragraphs)[:200]

    if bridge_required:
        if n != 4:
            return [
                f"{CL_PARAGRAPH_COUNT_INVALID}: expected=4, actual={n}, "
                f"body_preview={body_preview!r}, "
                f"body_paragraphs_lengths={lengths}"
            ]
    else:
        if n not in (3, 4):
            return [
                f"{CL_PARAGRAPH_COUNT_INVALID}: expected=3-4, actual={n}, "
                f"body_preview={body_preview!r}, "
                f"body_paragraphs_lengths={lengths}"
            ]
    return []


def _validate_cl_required_spans(
    paragraphs: list[str],
    cover_letter_plan: dict,
) -> list[str]:
    """V2: Validate required_exact_span values appear verbatim in the correct paragraph.

    P1 → paragraph 2 (1-based index 2, 0-based index 1).
    P2 → paragraph 3 (1-based index 3, 0-based index 2).

    Error payload includes:
    - required_exact_span: the span that was expected
    - expected_paragraph_index: 1-based index where it should appear
    - found_in_paragraph_index: 1-based index where it was actually found
      (or null if not found anywhere in the body)

    When bridge_sentence_required=true and fewer than 4 paragraphs exist,
    also emits CL_BRIDGE_MISSING.
    """
    errors: list[str] = []
    proof_points = cover_letter_plan.get("proof_points", [])
    bridge_required: bool = cover_letter_plan.get("bridge_sentence_required", False)

    _para_map: dict[str, int] = {"P1": 1, "P2": 2}  # 0-based indices
    _error_map: dict[str, str] = {
        "P1": CL_P1_SPAN_MISSING,
        "P2": CL_P2_SPAN_MISSING,
    }

    for proof in proof_points:
        pid = proof.get("proof_id", "")
        span = proof.get("required_exact_span", "")
        if not span or pid not in _para_map:
            continue
        expected_0 = _para_map[pid]
        expected_1based = expected_0 + 1

        # Check correct paragraph
        if expected_0 < len(paragraphs) and span in paragraphs[expected_0]:
            continue  # found in correct location

        # Search for span elsewhere in the body
        found_1based: int | None = None
        for i, para in enumerate(paragraphs):
            if span in para:
                found_1based = i + 1
                break

        errors.append(
            f"{_error_map[pid]}: "
            f"required_exact_span={span!r}, "
            f"expected_paragraph_index={expected_1based}, "
            f"found_in_paragraph_index={found_1based}"
        )

    if bridge_required and len(paragraphs) < 4:
        errors.append(
            f"{CL_BRIDGE_MISSING}: bridge_sentence_required=true "
            f"but paragraph 4 is missing"
        )

    return errors


_CL_PARA_LOC_RE: re.Pattern[str] = re.compile(r"^paragraph\[(\d+)\]$")


def _parse_cl_ledger_location(location: str) -> int | None:
    """Parse 'paragraph[N]' → N (1-based integer). Returns None on failure."""
    m = _CL_PARA_LOC_RE.match(location.strip())
    return int(m.group(1)) if m else None


def _validate_cl_ledger(
    cover_letter: str,
    paragraphs: list[str],
    cover_letter_ledger: list[dict],
    cover_letter_plan: dict,
) -> list[str]:
    """V3: Validate cover_letter_ledger entries for required proof points.

    Checks:
    - P1, P2 (and BRIDGE when bridge_sentence_required=true) each have an entry.
    - Each entry's exact_span is a literal substring of the cover letter.
    - Each entry's location parses to a valid paragraph index and the span
      actually appears in that paragraph.
    """
    errors: list[str] = []
    bridge_required: bool = cover_letter_plan.get("bridge_sentence_required", False)

    ledger_index: dict[str, dict] = {
        entry.get("proof_id", ""): entry
        for entry in cover_letter_ledger
        if entry.get("proof_id")
    }

    required_ids = ["P1", "P2"]
    if bridge_required:
        required_ids.append("BRIDGE")

    for pid in required_ids:
        entry = ledger_index.get(pid)
        if entry is None:
            errors.append(f"{CL_LEDGER_MISSING_ENTRY}: no ledger entry for {pid}")
            continue

        span = entry.get("exact_span", "")
        location = entry.get("location", "")

        if not span or span not in cover_letter:
            errors.append(
                f"{CL_LEDGER_SPAN_NOT_FOUND}: ledger entry {pid} "
                f"exact_span not found in cover letter"
            )
            continue

        para_idx = _parse_cl_ledger_location(location)
        if para_idx is None:
            errors.append(
                f"{CL_LEDGER_LOCATION_INVALID}: ledger entry {pid} "
                f"location {location!r} is not in 'paragraph[N]' format"
            )
            continue

        if para_idx < 1 or para_idx > len(paragraphs):
            errors.append(
                f"{CL_LEDGER_LOCATION_INVALID}: ledger entry {pid} "
                f"location {location!r} out of range "
                f"(cover letter has {len(paragraphs)} paragraph(s))"
            )
            continue

        para_text = paragraphs[para_idx - 1]
        if span not in para_text:
            errors.append(
                f"{CL_LEDGER_LOCATION_INVALID}: ledger entry {pid} "
                f"exact_span not found in {location} "
                f"(span exists elsewhere in cover letter)"
            )

    return errors


def _validate_cl_tool_allowlist(
    paragraphs: list[str],
    cover_letter_plan: dict,
    direct_skills_set: set[str],
) -> list[str]:
    """V4: Check for unlisted high-risk tool tokens in proof paragraphs.

    Uses the same _HIGH_RISK_TOOL_LEXICON as the resume experience-claim check.
    P1 → paragraph index 1 (0-based), P2 → paragraph index 2.
    The per-proof allowlist is: proof.allowed_tool_mentions ∪ direct_skills_set.
    """
    errors: list[str] = []
    proof_points = cover_letter_plan.get("proof_points", [])
    _para_map: dict[str, int] = {"P1": 1, "P2": 2}

    for proof in proof_points:
        pid = proof.get("proof_id", "")
        para_idx = _para_map.get(pid)
        if para_idx is None or para_idx >= len(paragraphs):
            continue

        para_lower = paragraphs[para_idx].lower()
        allowed: set[str] = {
            m.lower() for m in proof.get("allowed_tool_mentions", [])
        } | direct_skills_set

        for tool in _HIGH_RISK_TOOL_LEXICON:
            pattern = r"\b" + re.escape(tool) + r"\b"
            if re.search(pattern, para_lower) and tool not in allowed:
                errors.append(
                    f"{CL_TOOL_ALLOWLIST_VIOLATION}: tool {tool!r} appears in "
                    f"{pid} paragraph but is not in allowed_tool_mentions"
                )

    return errors


# ---------------------------------------------------------------------------
# Domain translation helpers
# ---------------------------------------------------------------------------

def _format_dt_rules_sample(rules: list[dict]) -> list[dict]:
    """Return a compact sample of domain translation rule fields for error payloads."""
    return [
        {
            "rule_id": r.get("rule_id", ""),
            "allowed_phrases_sample": (r.get("allowed_phrases") or [])[:2],
            "target_frames_sample": (r.get("target_frames") or [])[:2],
        }
        for r in rules
    ]


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
    domain_translation_ledger: list | None = None,
    cover_letter_ledger: list | None = None,
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
    domain_translation_ledger:
        Optional list of domain translation ledger entries produced by the
        Phase 2 writer.  Used by Check 14 (DomainTranslationAnchorValidator).
        Each entry has ``rule_id``, ``exact_span``, and ``location`` keys.
    """
    if now is None:
        now = date.today()

    errors: list[str] = []
    warnings: list[str] = []
    structured_errors: list[dict] = []

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

    # Shared structures for Checks 8 and 11 (computed once).
    output_role_headers: list[str] = [h for h, _ in roles]
    earlier_lines: list[str] = _extract_earlier_roles_block(resume)

    # --- 8. Role preservation: every master-resume role must appear in output ---
    master_role_names: list[str] = writer_packet.get("master_resume_role_names", [])
    missing_roles: list[str] = []
    if master_role_names:
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

    # --- 10. Domain mismatch: forbidden domain membership phrases ---
    domain_forbidden_phrases_found: list[str] = []
    if writer_packet.get("domain_mismatch"):
        resume_lower = resume.lower()
        cover_lower = cover_letter.lower()
        for rule in writer_packet.get("domain_translation_rules_applied", []):
            for phrase in rule.get("forbidden_phrases", []):
                phrase_lower = phrase.lower()
                if phrase_lower in resume_lower or phrase_lower in cover_lower:
                    domain_forbidden_phrases_found.append(phrase)
        if domain_forbidden_phrases_found:
            errors.append(
                f"DOMAIN_FORBIDDEN_PHRASES: {'; '.join(domain_forbidden_phrases_found)}"
            )

    # --- 11. Employer integrity: no invented roles; no modified company names ---
    employer_integrity_violations: list[str] = []
    date_integrity_violations: list[str] = []
    if master_role_names and output_role_headers:
        # Normalise the "Earlier roles" compressed entries so we can distinguish
        # them from full role headers (they use a reversed Company | Title | Year
        # format and require looser matching logic).
        earlier_headers_norm: set[str] = {
            normalize_role_header(l).lower() for l in earlier_lines
        }
        for out_header in output_role_headers:
            if out_header.lower() in earlier_headers_norm:
                matched = _earlier_role_entry_matches_master(out_header, master_role_names)
            else:
                matched = any(
                    _employer_integrity_match(out_header, m) for m in master_role_names
                )
            if not matched:
                employer_integrity_violations.append(out_header)

        if employer_integrity_violations:
            logger.warning(
                "EMPLOYER_INTEGRITY_NEW_ROLE:\n"
                "  output_roles=%s\n  master_roles=%s\n  violations=%s",
                output_role_headers,
                master_role_names,
                employer_integrity_violations,
            )
            errors.append(
                f"{EMPLOYER_INTEGRITY_NEW_ROLE}: "
                f"{'; '.join(employer_integrity_violations)}"
            )

    # Date integrity: each output role's date line must match the master date.
    master_role_dates: dict[str, str] = writer_packet.get("master_role_dates", {})
    if master_role_dates:
        for out_header, out_date in role_date_lines.items():
            # For roles with inline dates (no separate date line), extract the
            # date from the last pipe segment of the header when it contains a year.
            if not out_date:
                header_parts = [p.strip() for p in out_header.split(" | ")]
                if len(header_parts) >= 3 and _YEAR_RE.search(header_parts[-1]):
                    out_date = header_parts[-1]
            if not out_date:
                continue
            for master_name, master_date in master_role_dates.items():
                if not _roles_match(out_header, master_name):
                    continue
                if master_date and _normalize_date_str(out_date) != _normalize_date_str(master_date):
                    date_integrity_violations.append(
                        f"{out_header!r}: master='{master_date}' output='{out_date}'"
                    )
                break  # matched this master role; stop inner loop

        if date_integrity_violations:
            errors.append(
                f"{DATE_INTEGRITY_MODIFIED}: {'; '.join(date_integrity_violations)}"
            )

    # --- 12. Narrative: summary theme coverage ---
    narrative_plan: dict = writer_packet.get("narrative_plan") or {}
    narrative_missing_summary_themes: list[str] = []
    narrative_anchor_missing_theme_ids: list[str] = []

    if narrative_plan:
        theme_map: dict[str, dict] = {
            t["theme_id"]: t for t in narrative_plan.get("theme_ranked", [])
        }
        must_cover = (
            narrative_plan.get("summary_coverage", {}).get("must_cover_theme_ids", [])
        )
        if must_cover and theme_map:
            summary_text = _extract_summary(resume)
            for theme_id in must_cover:
                theme_entry = theme_map.get(theme_id)
                if not theme_entry:
                    continue
                hits = _count_signature_hits(
                    summary_text, theme_entry.get("signature_terms", [])
                )
                if hits < 1:
                    narrative_missing_summary_themes.append(theme_id)
                    errors.append(
                        f"{NARRATIVE_SUMMARY_THEME_MISSING}: theme {theme_id} "
                        f"({theme_entry['label']!r}) 0 signature-term hits in summary"
                    )
                    structured_errors.append({
                        "code": NARRATIVE_SUMMARY_THEME_MISSING,
                        "message": f"theme {theme_id} has 0 signature-term hits in summary",
                        "payload": {
                            "missing_theme_ids": [theme_id],
                            "required_signature_terms": {
                                theme_id: theme_entry.get("signature_terms", []),
                            },
                        },
                    })

    # --- 13. Narrative: anchor role dominance ---
    if narrative_plan:
        anchor_role_id: str = narrative_plan.get("anchor_role_id", "")
        arc = narrative_plan.get("anchor_role_coverage", {})
        first_k_bullets: int = arc.get("first_k_bullets", 3)
        top_k_themes: int = arc.get("top_k_themes_to_cover", 2)
        min_occ_raw = arc.get("min_theme_occurrences", [])
        if isinstance(min_occ_raw, dict):
            # backward-compat: tests pass a dict directly
            min_occ: dict[str, int] = min_occ_raw
        else:
            # new API format: array of {theme_id, min_count}
            min_occ = {e["theme_id"]: e["min_count"] for e in min_occ_raw if isinstance(e, dict)}

        if anchor_role_id and theme_map:
            anchor_bullets = _extract_anchor_role_bullets(
                roles, anchor_role_id, first_k_bullets
            )
            bullets_text = " ".join(anchor_bullets)
            top_themes = list(theme_map.values())[:top_k_themes]

            for theme_entry in top_themes:
                tid = theme_entry["theme_id"]
                required = min_occ.get(tid, 1)
                if required == 0:
                    continue
                hits = _count_signature_hits(
                    bullets_text, theme_entry.get("signature_terms", [])
                )
                if hits < required:
                    narrative_anchor_missing_theme_ids.append(tid)

            if narrative_anchor_missing_theme_ids:
                errors.append(
                    f"{NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED}: "
                    f"anchor role {anchor_role_id!r} first {first_k_bullets} bullets "
                    f"missing themes {narrative_anchor_missing_theme_ids}"
                )
                structured_errors.append({
                    "code": NARRATIVE_ANCHOR_ROLE_DOMINANCE_FAILED,
                    "message": (
                        f"anchor role first {first_k_bullets} bullets missing themes "
                        f"{narrative_anchor_missing_theme_ids}"
                    ),
                    "payload": {
                        "anchor_role_id": anchor_role_id,
                        "first_k_bullets": first_k_bullets,
                        "missing_theme_ids": narrative_anchor_missing_theme_ids,
                        "required_signature_terms": {
                            tid: theme_map[tid].get("signature_terms", [])
                            for tid in narrative_anchor_missing_theme_ids
                            if tid in theme_map
                        },
                    },
                })

    # --- 14. Domain translation anchor validator ---
    domain_tb = (narrative_plan or {}).get("domain_translation_binding", {})
    if (
        narrative_plan
        and writer_packet.get("domain_mismatch", False)
        and domain_translation_ledger is not None
        and domain_tb
    ):
        active_dt = [
            e for e in domain_translation_ledger
            if e.get("exact_span") and e.get("location") != "skipped"
        ]
        min_total = domain_tb.get("min_total_rule_instantiations", 0)
        min_anchor_dt = domain_tb.get("min_instantiations_in_anchor_role", 0)
        req_first_k = domain_tb.get("require_target_frame_in_anchor_role_first_k", False)
        dt_anchor_id: str = (narrative_plan or {}).get("anchor_role_id", "")
        _dt_rules_applied: list[dict] = writer_packet.get("domain_translation_rules_applied") or []
        _dt_rules_sample = _format_dt_rules_sample(_dt_rules_applied)
        fk_dt: int = (narrative_plan or {}).get(
            "anchor_role_coverage", {}
        ).get("first_k_bullets", 3)

        # Pre-compute anchor-role DT count for shared structured error payload.
        _dt_anchor_entries = [
            e for e in active_dt
            if dt_anchor_id and dt_anchor_id.lower() in (e.get("location") or "").lower()
        ]
        _dt_structured_payload: dict = {
            "required_total": min_total,
            "found_total": len(active_dt),
            "required_in_anchor_role": min_anchor_dt,
            "found_in_anchor_role": len(_dt_anchor_entries),
            "anchor_role_id": dt_anchor_id,
            "applied_rules": _dt_rules_sample,
            "first_k_bullets": fk_dt,
        }

        if min_total > 0 and len(active_dt) < min_total:
            errors.append(
                f"{DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET}: "
                f"required_total={min_total}, found_total={len(active_dt)}, "
                f"anchor_role_id={dt_anchor_id!r}, "
                f"applied_rules={_dt_rules_sample}"
            )
            structured_errors.append({
                "code": DOMAIN_TRANSLATION_MIN_TOTAL_NOT_MET,
                "message": f"required {min_total} DT instantiations, found {len(active_dt)}",
                "payload": _dt_structured_payload,
            })

        if min_anchor_dt > 0 and len(_dt_anchor_entries) < min_anchor_dt:
            errors.append(
                f"{DOMAIN_TRANSLATION_ANCHOR_NOT_MET}: "
                f"required_in_anchor_role={min_anchor_dt}, "
                f"found_in_anchor_role={len(_dt_anchor_entries)}, "
                f"anchor_role_id={dt_anchor_id!r}, "
                f"applied_rules={_dt_rules_sample}"
            )
            structured_errors.append({
                "code": DOMAIN_TRANSLATION_ANCHOR_NOT_MET,
                "message": (
                    f"required {min_anchor_dt} DT instantiations in anchor role, "
                    f"found {len(_dt_anchor_entries)}"
                ),
                "payload": _dt_structured_payload,
            })

        if req_first_k and dt_anchor_id:
            anchor_bullets_dt = _extract_anchor_role_bullets(roles, dt_anchor_id, fk_dt)
            first_k_text = " ".join(anchor_bullets_dt).lower()
            dt_hit = any(
                (e.get("exact_span") or "").lower() in first_k_text
                for e in active_dt
            )
            if not dt_hit:
                errors.append(
                    f"{DOMAIN_TRANSLATION_FIRST_K_NOT_MET}: "
                    f"no domain translation span in anchor role first {fk_dt} bullets, "
                    f"anchor_role_id={dt_anchor_id!r}, "
                    f"applied_rules={_dt_rules_sample}"
                )
                structured_errors.append({
                    "code": DOMAIN_TRANSLATION_FIRST_K_NOT_MET,
                    "message": f"no DT span in anchor role first {fk_dt} bullets",
                    "payload": _dt_structured_payload,
                })

    # --- 15. Skill section allowlist ---
    skill_section_violations: list[str] = []
    allowlist_ss = {s.lower() for s in (writer_packet.get("skill_allowlist_skills_section") or [])}
    if allowlist_ss:
        skills_raw = _extract_skills_section(resume)
        for token in _tokenize_skills_section(skills_raw):
            # Also test the space-collapsed form to catch CamelCase compounds
            # written with spaces, e.g. "Rabbit MQ" → "rabbit mq" vs allowlist
            # entry "rabbitmq" (from "RabbitMQ".lower()).
            if (
                token
                and token not in allowlist_ss
                and token.replace(" ", "") not in allowlist_ss
            ):
                skill_section_violations.append(token)
        if skill_section_violations:
            errors.append(
                f"{SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION}: "
                f"offending_tokens={skill_section_violations!r}"
            )
            structured_errors.append({
                "code": SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION,
                "message": f"{len(skill_section_violations)} disallowed token(s) in Skills section",
                "payload": {
                    "section": "resume.skills",
                    "offending_tokens": [
                        {"raw": t, "normalized": t} for t in skill_section_violations
                    ],
                    "allowlist_sample": sorted(allowlist_ss)[:20],
                    "output_format_hint": "flat_list_preferred",
                },
            })

    # --- 16. Experience tool claim allowlist ---
    experience_tool_violations: list[str] = []
    allowlist_exp = {s.lower() for s in (writer_packet.get("skill_allowlist_experience_claims") or [])}
    if allowlist_exp:
        for _role_header, bullets in roles:
            for bullet in bullets:
                bullet_lower = bullet.lower()
                for tool in _HIGH_RISK_TOOL_LEXICON:
                    pattern = r"\b" + re.escape(tool) + r"\b"
                    if re.search(pattern, bullet_lower) and tool not in allowlist_exp:
                        if tool not in experience_tool_violations:
                            experience_tool_violations.append(tool)
                        errors.append(
                            f"{SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION}: "
                            f"tool {tool!r} claimed in experience bullet but not in allowlist"
                        )

    # --- 17–20. Cover-letter validators (V1–V4) ---
    # Only active when cover_letter_plan is present in the writer packet.
    cl_paragraph_errors: list[str] = []
    cl_span_errors: list[str] = []
    cl_ledger_errors: list[str] = []
    cl_tool_errors: list[str] = []

    cl_plan: dict = writer_packet.get("cover_letter_plan") or {}
    if cl_plan and cover_letter:
        cl_paragraphs = parse_cover_letter_body(cover_letter)
        bridge_required: bool = cl_plan.get("bridge_sentence_required", False)

        # V1: paragraph count
        cl_paragraph_errors = _validate_cl_paragraph_count(cl_paragraphs, bridge_required)
        errors.extend(cl_paragraph_errors)
        if cl_paragraph_errors:
            _has_salutation = bool(re.search(r"\bDear\b", cover_letter, re.IGNORECASE))
            _expected_n: int | str = 4 if bridge_required else "3-4"
            structured_errors.append({
                "code": CL_PARAGRAPH_COUNT_INVALID,
                "message": f"expected {_expected_n} body paragraphs, got {len(cl_paragraphs)}",
                "payload": {
                    "expected_body_paragraphs": _expected_n,
                    "actual_body_paragraphs": len(cl_paragraphs),
                    "has_salutation": _has_salutation,
                    "body_paragraphs_preview": [p[:160] for p in cl_paragraphs[:4]],
                },
            })

        # V2: required exact spans
        cl_span_errors = _validate_cl_required_spans(cl_paragraphs, cl_plan)
        errors.extend(cl_span_errors)
        # V2 structured errors — rebuilt inline from the same data
        _cl_para_idx: dict[str, int] = {"P1": 1, "P2": 2}  # 0-based
        _cl_code_map: dict[str, str] = {"P1": CL_P1_SPAN_MISSING, "P2": CL_P2_SPAN_MISSING}
        for _proof in cl_plan.get("proof_points", []):
            _pid = _proof.get("proof_id", "")
            _span = _proof.get("required_exact_span", "")
            if not _span or _pid not in _cl_para_idx:
                continue
            _exp_0 = _cl_para_idx[_pid]
            _exp_1b = _exp_0 + 1
            if _exp_0 < len(cl_paragraphs) and _span in cl_paragraphs[_exp_0]:
                continue  # correct location — no error
            _found_1b: int | None = None
            for _i, _para in enumerate(cl_paragraphs):
                if _span in _para:
                    _found_1b = _i + 1
                    break
            structured_errors.append({
                "code": _cl_code_map[_pid],
                "message": (
                    f"{_pid} required_exact_span not found in body paragraph {_exp_1b}"
                ),
                "payload": {
                    "proof_id": _pid,
                    "required_exact_span": _span,
                    "expected_body_paragraph_index": _exp_1b,
                    "found_in_body_paragraph_index": _found_1b,
                },
            })
        # CL_BRIDGE_MISSING structured error
        if bridge_required and len(cl_paragraphs) < 4:
            _src = writer_packet.get("candidate_primary_domain", "<source_domain>")
            _jd = writer_packet.get("jd_domain", "<jd_domain>")
            _dt_r = writer_packet.get("domain_translation_rules_applied") or []
            _tfp = (
                (_dt_r[0].get("target_frames") or ["<transferable_pattern>"])[0]
                if _dt_r else "<transferable_pattern>"
            )
            structured_errors.append({
                "code": CL_BRIDGE_MISSING,
                "message": "bridge sentence paragraph (body paragraph 4) is missing",
                "payload": {
                    "bridge_sentence": (
                        f"While my background is in {_src}, the underlying patterns of "
                        f"{_tfp} translate directly to {_jd}."
                    ),
                    "expected_body_paragraph_index": 4,
                },
            })

        # V3: ledger entries (only when cover_letter_ledger provided)
        if cover_letter_ledger is not None:
            cl_ledger_errors = _validate_cl_ledger(
                cover_letter, cl_paragraphs, cover_letter_ledger, cl_plan
            )
            errors.extend(cl_ledger_errors)

        # V4: tool allowlist per proof paragraph
        _direct_set: set[str] = {
            s.lower() for s in (writer_packet.get("direct_skills_set") or [])
        }
        cl_tool_errors = _validate_cl_tool_allowlist(cl_paragraphs, cl_plan, _direct_set)
        errors.extend(cl_tool_errors)

    # --- 15. Skill section allowlist ---
    skill_section_violations: list[str] = []
    allowlist_ss = {s.lower() for s in (writer_packet.get("skill_allowlist_skills_section") or [])}
    if allowlist_ss:
        skills_raw = _extract_skills_section(resume)
        for token in _tokenize_skills_section(skills_raw):
            if token and token not in allowlist_ss:
                skill_section_violations.append(token)
                errors.append(
                    f"{SKILL_ALLOWLIST_SKILLS_SECTION_VIOLATION}: "
                    f"skill {token!r} not in allowlist"
                )

    # --- 16. Experience tool claim allowlist ---
    experience_tool_violations: list[str] = []
    allowlist_exp = {s.lower() for s in (writer_packet.get("skill_allowlist_experience_claims") or [])}
    if allowlist_exp:
        for _role_header, bullets in roles:
            for bullet in bullets:
                bullet_lower = bullet.lower()
                for tool in _HIGH_RISK_TOOL_LEXICON:
                    pattern = r"\b" + re.escape(tool) + r"\b"
                    if re.search(pattern, bullet_lower) and tool not in allowlist_exp:
                        if tool not in experience_tool_violations:
                            experience_tool_violations.append(tool)
                        errors.append(
                            f"{SKILL_ALLOWLIST_EXPERIENCE_CLAIM_VIOLATION}: "
                            f"tool {tool!r} claimed in experience bullet but not in allowlist"
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
            "domain_forbidden_phrases": domain_forbidden_phrases_found,
            "employer_integrity_violations": employer_integrity_violations,
            "date_integrity_violations": date_integrity_violations,
            "narrative_missing_summary_themes": narrative_missing_summary_themes,
            "narrative_anchor_missing_theme_ids": narrative_anchor_missing_theme_ids,
            "skill_section_violations": skill_section_violations,
            "experience_tool_violations": experience_tool_violations,
        },
        "roles": repair_roles,
    }

    ledger_findings["judge_candidate_count"] = len(judge_candidates)

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "structured_errors": structured_errors,
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
            "domain_forbidden_phrases_found": domain_forbidden_phrases_found,
            "employer_integrity_violations": employer_integrity_violations,
            "date_integrity_violations": date_integrity_violations,
            "narrative_missing_summary_themes": narrative_missing_summary_themes,
            "narrative_anchor_missing_theme_ids": narrative_anchor_missing_theme_ids,
            "skill_section_violations": skill_section_violations,
            "experience_tool_violations": experience_tool_violations,
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


_HIGH_RISK_TOOL_LEXICON: frozenset[str] = frozenset({
    "azure", "gcp", "terraform", "ansible", "react", "rails", "angular",
    "graphql", ".net", "kotlin", "swift", "unity", "snowflake", "databricks",
})


def _tokenize_skills_section(text: str) -> list[str]:
    """Split raw skills section text into individual skill tokens."""
    tokens = re.split(r"[,|•·:\n]+", text)
    return [t.strip().lower() for t in tokens if t.strip()]


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
