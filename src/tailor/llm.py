"""OpenAI client wrapper and document tailoring logic."""

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from openai import OpenAI

from tailor.config import (
    PHASE1_MAX_TOKENS,
    PHASE1_MODEL,
    PHASE1_TEMPERATURE,
    PHASE2_MAX_TOKENS,
    PHASE2_MODEL,
    PHASE2_TEMPERATURE,
)
from tailor.plan_validator import validate_plan_extended
from tailor.job import JobData
from tailor.phase2_validator import validate_phase2_output
from tailor.prompts import _load_candidate_profile, _load_prompt, _load_prompt_optional
from tailor.writer_packet import build_writer_packet

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

# --- Allowed values ---
_VALID_ROLE_LEVELS = {"director", "senior", "mid", "junior"}
_VALID_PRIORITIES = {"high", "medium", "low"}
_VALID_EVIDENCE_SOURCES = {"candidate_profile", "master_resume"}

# Required top-level keys for a TailoringPlan — single source of truth.
_PLAN_REQUIRED_KEYS = frozenset({
    "role_level",
    "jd_top_themes",
    "evidence_map",
    "resume_strategy",
    "cover_letter_strategy",
    "risk_checks",
})


# ---------------------------------------------------------------------------
# Schema gate + role_level coercion (run BEFORE deep validation)
# ---------------------------------------------------------------------------

def _coerce_role_level(raw: str) -> str:
    """Map any role_level string to a valid enum value.

    Handles cases where the LLM returns a job title instead of the enum
    (e.g. "Senior Software Engineer" → "senior").
    Falls back to "junior" for anything unrecognised.
    """
    s = raw.lower()
    if "director" in s or "vp" in s:
        return "director"
    if "senior" in s or "staff" in s:
        return "senior"
    if "mid" in s or "intermediate" in s:
        return "mid"
    return "junior"


def _run_schema_gate(data: Any) -> list[str]:
    """Structural schema gate: required-key and type checks only.

    Returns a list of error strings; empty list means the schema is valid.
    Never raises.  Must be called before any deep validation to prevent
    crashes caused by missing or wrongly-typed fields.

    Errors are capped at 15 to avoid flooding the repair-prompt context.
    """
    if not isinstance(data, dict):
        return [f"schema_invalid: expected JSON object, got {type(data).__name__}"]

    errors: list[str] = []

    # All required keys must be present before sub-structure checks.
    missing = _PLAN_REQUIRED_KEYS - data.keys()
    if missing:
        errors.append(f"schema_invalid: missing keys: {sorted(missing)}")
        return errors  # sub-checks on absent keys would be misleading

    # role_level: must be a string (value is normalised by _coerce_role_level)
    if not isinstance(data["role_level"], str):
        errors.append(
            f"schema_invalid: role_level must be a string, "
            f"got {type(data['role_level']).__name__}"
        )

    # jd_top_themes: list of objects each with required keys
    jdt = data["jd_top_themes"]
    if not isinstance(jdt, list):
        errors.append(
            f"schema_invalid: jd_top_themes must be a list, got {type(jdt).__name__}"
        )
    else:
        for i, item in enumerate(jdt):
            if not isinstance(item, dict):
                errors.append(f"schema_invalid: jd_top_themes[{i}] must be an object")
            else:
                for k in ("theme", "why_important", "keywords"):
                    if k not in item:
                        errors.append(
                            f"schema_invalid: jd_top_themes[{i}] missing key {k!r}"
                        )

    # evidence_map: list of objects, each with an evidence list of objects
    em = data["evidence_map"]
    if not isinstance(em, list):
        errors.append(
            f"schema_invalid: evidence_map must be a list, got {type(em).__name__}"
        )
    else:
        for i, entry in enumerate(em):
            if not isinstance(entry, dict):
                errors.append(f"schema_invalid: evidence_map[{i}] must be an object")
                continue
            evs = entry.get("evidence")
            if evs is None:
                errors.append(f"schema_invalid: evidence_map[{i}] missing key 'evidence'")
                continue
            if not isinstance(evs, list):
                errors.append(
                    f"schema_invalid: evidence_map[{i}].evidence must be a list"
                )
                continue
            for j, ev in enumerate(evs):
                if not isinstance(ev, dict):
                    errors.append(
                        f"schema_invalid: evidence_map[{i}].evidence[{j}] must be an object"
                    )
                else:
                    for k in ("source", "location", "quote", "allowed_claims"):
                        if k not in ev:
                            errors.append(
                                f"schema_invalid: evidence_map[{i}].evidence[{j}] "
                                f"missing key {k!r}"
                            )

    # resume_strategy.experience must be a list (deep validators iterate it)
    rs = data.get("resume_strategy")
    if isinstance(rs, dict):
        exp = rs.get("experience")
        if exp is not None and not isinstance(exp, list):
            errors.append(
                f"schema_invalid: resume_strategy.experience must be a list, "
                f"got {type(exp).__name__}"
            )

    return errors[:15]  # cap to avoid flooding repair context


def get_client() -> OpenAI:
    """Return a module-level OpenAI client, creating it on first call."""
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


@dataclass
class TailorResult:
    resume: str | None = None
    cover_letter: str | None = None


# ---------------------------------------------------------------------------
# TailoringPlan validation
# ---------------------------------------------------------------------------

class PlanValidationError(ValueError):
    """Raised when Phase 1 JSON does not match the required schema."""


class PlanParseError(Exception):
    """Raised when a Phase 1 LLM response cannot be parsed as JSON.

    Carries the raw response text so the caller can pass it to the repair
    prompt as RAW_TEXT (repair cannot work with an empty INVALID_PLAN).
    """

    def __init__(self, original_exc: json.JSONDecodeError, raw_content: str) -> None:
        super().__init__(str(original_exc))
        self.raw_content = raw_content


def validate_plan(data: Any) -> dict:
    """Validate and return the plan dict, raising PlanValidationError on failure.

    Checks performed:
    - All required top-level keys are present.
    - role_level is a known value.
    - jd_top_themes has 4-7 entries.
    - Each evidence quote is <= 25 words.
    """
    if not isinstance(data, dict):
        raise PlanValidationError(f"Plan must be a JSON object, got {type(data).__name__}")

    missing = _PLAN_REQUIRED_KEYS - data.keys()
    if missing:
        raise PlanValidationError(f"Plan missing required keys: {missing}")

    # Coerce role_level in place; maps job-title strings to the valid enum.
    if isinstance(data.get("role_level"), str):
        data["role_level"] = _coerce_role_level(data["role_level"])
    role_level = data.get("role_level")
    if role_level not in _VALID_ROLE_LEVELS:
        raise PlanValidationError(
            f"role_level must be one of {_VALID_ROLE_LEVELS}, got {role_level!r}"
        )

    themes = data.get("jd_top_themes", [])
    if not isinstance(themes, list) or not (4 <= len(themes) <= 7):
        raise PlanValidationError(
            f"jd_top_themes must be a list of 4-7 items, got "
            f"{len(themes) if isinstance(themes, list) else type(themes).__name__}"
        )

    for entry in data.get("evidence_map", []):
        for ev in entry.get("evidence", []):
            quote = ev.get("quote", "")
            word_count = len(quote.split())
            if word_count > 25:
                raise PlanValidationError(
                    f"Evidence quote exceeds 25-word limit ({word_count} words): {quote!r}"
                )

    return data


# ---------------------------------------------------------------------------
# Phase 1 — plan_tailoring
# ---------------------------------------------------------------------------

def plan_tailoring(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[dict, list, dict]:
    """Phase 1: produce a structured TailoringPlan JSON.

    Parameters
    ----------
    job:
        Structured job data (company, title, description).
    resume_template:
        Plain-text content of the resume template.
    cover_template:
        Plain-text content of the cover letter template.

    Returns
    -------
    plan:
        Validated TailoringPlan dict.
    messages:
        Full message list sent to Phase 1 LLM.
    debug_meta:
        Dict with ``model``, ``usage`` (token counts), ``raw_response``.
    """
    planner_instructions = _load_prompt("phase1")
    profile = _load_candidate_profile()

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )

    messages: list = [
        {"role": "developer", "content": planner_instructions},
    ]
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response = get_client().chat.completions.create(
        model=PHASE1_MODEL,
        messages=messages,
        temperature=PHASE1_TEMPERATURE,
        max_tokens=PHASE1_MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    raw_content = response.choices[0].message.content
    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
        "prompt_used": "PLAN",
    }
    try:
        plan_data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise PlanParseError(exc, raw_content) from exc

    return plan_data, messages, debug_meta


def plan_repair_tailoring(
    invalid_plan: dict,
    schema_errors: list[str],
    validation_errors: list[str],
    job: JobData,
    resume_template: str,
    cover_template: str,
    raw_text: str | None = None,
) -> tuple[dict, list, dict]:
    """Phase 1 repair: rebuild a plan that failed schema or deep validation.

    Parameters
    ----------
    invalid_plan:
        The last parsed plan dict (may be ``{}`` if JSON parsing failed).
    schema_errors:
        Structural errors from ``_run_schema_gate`` (type mismatches, missing keys).
    validation_errors:
        Deep validation errors from ``validate_plan`` / ``validate_plan_extended``.
    job, resume_template, cover_template:
        Same sources used in the original ``plan_tailoring`` call.

    Returns
    -------
    plan:
        Raw (unvalidated) plan dict from the repair LLM call.
    messages:
        Full message list sent to the repair LLM.
    debug_meta:
        Dict with ``model``, ``usage``, ``raw_response``.
    """
    repair_instructions = _load_prompt("phase1_repair")
    profile = _load_candidate_profile()

    messages: list = [
        {"role": "developer", "content": repair_instructions},
        {
            "role": "user",
            "content": f"SCHEMA_ERRORS:\n{json.dumps(schema_errors, indent=2)}",
        },
        {
            "role": "user",
            "content": f"VALIDATION_ERRORS:\n{json.dumps(validation_errors, indent=2)}",
        },
        {
            "role": "user",
            "content": f"INVALID_PLAN:\n{json.dumps(invalid_plan, indent=2)}",
        },
    ]
    if raw_text:
        messages.append({"role": "user", "content": f"RAW_TEXT:\n{raw_text}"})
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})

    response = get_client().chat.completions.create(
        model=PHASE1_MODEL,
        messages=messages,
        temperature=PHASE1_TEMPERATURE,
        max_tokens=PHASE1_MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    raw_content = response.choices[0].message.content
    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
        "prompt_used": "REPAIR",
    }
    try:
        plan_data = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        raise PlanParseError(exc, raw_content) from exc

    return plan_data, messages, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 — tailor_documents_with_plan  (writer + validator + repair)
# ---------------------------------------------------------------------------

_PLAN_OBEY_RULES = """
TAILORING_PLAN COMPLIANCE
You will be given a TAILORING_PLAN JSON as a user message.
Follow it exactly: apply its resume_strategy and cover_letter_strategy.
If the plan conflicts with MASTER_RESUME or CANDIDATE_PROFILE, prefer the
source documents and adjust the plan guidance safely -- never invent facts
to satisfy the plan.
Do not add skills, technologies, or claims listed in risk_checks.do_not_invent.
Preserve every metric listed in each role's keep_metrics.
""".strip()

_WRITER_PACKET_RULES = """
WRITER_PACKET COMPLIANCE
You will be given a WRITER_PACKET JSON as the first user message.
It encodes hard constraints derived from the plan and source documents.
You MUST follow all of these:
- must_keep_metrics: every item MUST appear verbatim in the resume output.
- must_surface_mechanisms: at least 4 MUST appear explicitly in the top 2
  roles. Use concrete names such as "read replicas", "horizontal scaling",
  "multi-layer caching", "async messaging", "stateless services", etc.
  Do NOT write "improved scalability" without naming the mechanism.
- must_include_skills: every item MUST be present in the Technical Skills
  section if it exists in the allowed pool.
- do_not_add_terms: NEVER add these to the output.
- unsafe_jd_nouns: NEVER use these unless the exact term is present in
  MASTER_RESUME or CANDIDATE_PROFILE.
- density_targets: the top 2 roles MUST each have 4-6 bullets.
""".strip()


def tailor_documents_with_plan(
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list, dict]:
    """Phase 2: generate + validate + optionally repair tailored documents.

    Flow
    ----
    1. Build WriterPacket from plan + sources.
    2. Run Phase 2 writer LLM call.
    3. Validate output with Phase2Validator.
    4. If validation fails: run one repair pass using phase2_repair.txt.
    5. Return best-effort result plus full debug_meta.

    Returns
    -------
    result:
        TailorResult with resume and cover_letter text.
    first_attempt_messages:
        Message list from the first (writer) LLM call.
    debug_meta:
        Dict containing writer_packet, attempts (per-call details),
        final_validation_ok, model, usage.
    """
    profile_str = _load_candidate_profile()

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )

    writer_packet = build_writer_packet(
        plan, profile_str, resume_template, job.description
    )

    # --- Attempt 1: normal writer ---
    result, attempt1_messages, attempt1_meta = _run_phase2_writer(
        writer_packet, plan, job, resume_template, cover_template,
        profile_str, task, current_date,
    )

    validation1 = validate_phase2_output(
        writer_packet,
        result.resume or "",
        result.cover_letter or "",
        current_date,
    )

    attempts = [
        {
            "llm_request": attempt1_messages,
            "llm_response_raw": attempt1_meta["raw_response"],
            "validation_report": validation1,
            "model": attempt1_meta["model"],
            "usage": attempt1_meta["usage"],
        }
    ]

    final_result = result
    final_validation = validation1

    # --- Attempt 2: repair pass (only if first attempt failed) ---
    if not validation1["ok"]:
        repair_result, repair_messages, repair_meta = _run_phase2_repair(
            writer_packet, plan, job, resume_template, cover_template,
            profile_str, task, current_date, validation1, result,
        )
        validation2 = validate_phase2_output(
            writer_packet,
            repair_result.resume or "",
            repair_result.cover_letter or "",
            current_date,
        )
        attempts.append(
            {
                "llm_request": repair_messages,
                "llm_response_raw": repair_meta["raw_response"],
                "validation_report": validation2,
                "model": repair_meta["model"],
                "usage": repair_meta["usage"],
            }
        )
        final_result = repair_result
        final_validation = validation2

    if not final_validation["ok"]:
        logger.warning(
            "Phase 2 validation still failing after repair: %s",
            final_validation["errors"],
        )

    debug_meta: dict = {
        "model": PHASE2_MODEL,
        "usage": attempts[-1]["usage"],
        "raw_response": attempts[-1]["llm_response_raw"],
        "writer_packet": writer_packet,
        "attempts": attempts,
        "final_validation_ok": final_validation["ok"],
        "phase2_prompt_name": attempt1_meta.get("phase2_prompt_name", "tailor"),
    }

    return final_result, attempt1_messages, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 internal helpers
# ---------------------------------------------------------------------------

def _build_phase2_developer_instructions() -> tuple[str, str]:
    """Build developer instructions for the Phase 2 writer.

    Prefers ``phase2.txt`` — a self-contained plan-aware writer prompt
    that already embeds plan/packet compliance rules.  Falls back to the
    legacy assembly (``tailor.txt`` + inline constants) when the file is absent.

    Returns
    -------
    instructions:
        Full developer instruction block ready to use as the first message.
    prompt_name:
        ``"phase2"`` or ``"tailor"`` — recorded in debug_meta.
    """
    phase2_base = _load_prompt_optional("phase2").strip()
    if phase2_base:
        prompt_name = "phase2"
        parts: list[str] = [phase2_base]
    else:
        prompt_name = "tailor"
        parts = [
            _load_prompt("tailor").strip(),
            _PLAN_OBEY_RULES,
            _WRITER_PACKET_RULES,
        ]

    # Optional per-candidate / per-role overlays (always appended when present)
    parts += [
        _load_prompt_optional("candidate").strip(),
        _load_prompt_optional("role").strip(),
    ]

    return "\n\n".join(p for p in parts if p), prompt_name


def _run_phase2_writer(
    writer_packet: dict,
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    task: str,
    current_date: str,
) -> tuple[TailorResult, list, dict]:
    """Execute a normal Phase 2 writer LLM call."""
    developer_instructions, prompt_name = _build_phase2_developer_instructions()

    messages: list = [
        {"role": "developer", "content": developer_instructions},
        # WriterPacket first — sets hard constraints before anything else
        {"role": "user", "content": f"WRITER_PACKET:\n{json.dumps(writer_packet, indent=2)}"},
        {"role": "user", "content": f"TAILORING_PLAN:\n{json.dumps(plan, indent=2)}"},
    ]
    if profile_str:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile_str}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response = get_client().chat.completions.create(
        model=PHASE2_MODEL,
        messages=messages,
        temperature=PHASE2_TEMPERATURE,
        max_tokens=PHASE2_MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    result, _, meta = _parse_phase2_response(response, PHASE2_MODEL)
    meta["phase2_prompt_name"] = prompt_name
    return result, messages, meta


def _run_phase2_repair(
    writer_packet: dict,
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    task: str,
    current_date: str,
    validation_report: dict,
    draft: TailorResult,
) -> tuple[TailorResult, list, dict]:
    """Execute a repair pass using the phase2_repair.txt prompt."""
    repair_instructions = _load_prompt("phase2_repair")

    draft_payload = json.dumps(
        {"resume": draft.resume or "", "cover_letter": draft.cover_letter or ""},
        indent=2,
    )

    messages: list = [
        {"role": "developer", "content": repair_instructions},
        {"role": "user", "content": f"VALIDATION_REPORT:\n{json.dumps(validation_report, indent=2)}"},
        {"role": "user", "content": f"WRITER_PACKET:\n{json.dumps(writer_packet, indent=2)}"},
        {"role": "user", "content": f"DRAFT_OUTPUT:\n{draft_payload}"},
        {"role": "user", "content": f"TAILORING_PLAN:\n{json.dumps(plan, indent=2)}"},
    ]
    if profile_str:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile_str}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    messages.append({"role": "user", "content": task})
    messages.append({"role": "user", "content": f"CURRENT_DATE:\n{current_date}"})

    response = get_client().chat.completions.create(
        model=PHASE2_MODEL,
        messages=messages,
        temperature=PHASE2_TEMPERATURE,
        max_tokens=PHASE2_MAX_TOKENS,
        response_format={"type": "json_object"},
    )

    return _parse_phase2_response(response, PHASE2_MODEL)


def _parse_phase2_response(response: Any, model: str) -> tuple[TailorResult, list, dict]:
    raw_content = response.choices[0].message.content
    raw = json.loads(raw_content)
    result = TailorResult(
        resume=raw.get("resume"),
        cover_letter=raw.get("cover_letter"),
    )
    usage = _extract_usage(response)
    meta = {
        "model": model,
        "usage": usage,
        "raw_response": raw_content,
    }
    # messages not available here; callers own their message lists
    return result, [], meta


# ---------------------------------------------------------------------------
# Single-pass fallback (original behaviour)
# ---------------------------------------------------------------------------

def tailor_documents(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list]:
    """Single-pass generate a tailored resume and cover letter (fallback).

    Returns
    -------
    result:
        TailorResult with ``resume`` and ``cover_letter`` text fields.
    messages:
        The full message list sent to the LLM (useful for debug logging).
    """
    developer_instructions = "\n\n".join(
        part for part in (
            _load_prompt("tailor").strip(),
            _load_prompt_optional("candidate").strip(),
            _load_prompt_optional("role").strip(),
        )
        if part
    )
    profile = _load_candidate_profile()

    messages: list = [
        {"role": "developer", "content": developer_instructions},
    ]
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "task",
        company=job.company,
        job_title=job.job_title,
        current_date=current_date,
    )
    messages.append({"role": "user", "content": task})

    response = get_client().chat.completions.create(
        model=PHASE2_MODEL,
        messages=messages,
        temperature=PHASE2_TEMPERATURE,
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content)
    result = TailorResult(
        resume=raw.get("resume"),
        cover_letter=raw.get("cover_letter"),
    )
    return result, messages


# ---------------------------------------------------------------------------
# Metadata extraction (unchanged)
# ---------------------------------------------------------------------------

def extract_metadata_ai(job_text: str) -> dict:
    """Use the LLM to extract company name and job title from raw job text."""
    prompt = _load_prompt("extract_metadata", job_text=job_text)

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"},
    )

    return json.loads(response.choices[0].message.content)


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def _extract_usage(response: Any) -> dict:
    if response.usage:
        return {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
    return {}
