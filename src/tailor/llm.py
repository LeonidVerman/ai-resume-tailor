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

# Required top-level keys for a TailoringPlan (v2.1)
_PLAN_REQUIRED_KEYS = {
    "role_level",
    "jd_top_themes",
    "evidence_map",
    "resume_strategy",
    "cover_letter_strategy",
    "risk_checks",
    "theme_priority",
    "role_repositioning_intent",
    "domain_de_emphasis",
    "evidence_saturation_rules",
    "bullet_allocation_plan",
}


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
    planner_instructions = _load_prompt("tailor_plan")
    profile = _load_candidate_profile()

    d = date.today()
    current_date = f"{d.strftime('%B')} {d.day}, {d.year}"
    task = _load_prompt(
        "tailor_task",
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
    plan_data = json.loads(raw_content)

    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
    }

    return plan_data, messages, debug_meta


def plan_repair_tailoring(
    invalid_plan: dict,
    validation_errors: list[str],
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[dict, list, dict]:
    """Phase 1 repair: ask the LLM to fix a plan that failed extended validation.

    Parameters
    ----------
    invalid_plan:
        The plan dict that failed ``validate_plan_extended``.
    validation_errors:
        The error strings returned by ``validate_plan_extended``.
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
    repair_instructions = _load_prompt("tailor_plan_repair")
    profile = _load_candidate_profile()

    messages: list = [
        {"role": "developer", "content": repair_instructions},
        {
            "role": "user",
            "content": f"VALIDATION_ERRORS:\n{json.dumps(validation_errors, indent=2)}",
        },
        {
            "role": "user",
            "content": f"INVALID_PLAN:\n{json.dumps(invalid_plan, indent=2)}",
        },
    ]
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
    plan_data = json.loads(raw_content)

    usage = _extract_usage(response)
    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
    }

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
        "tailor_task",
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
    }

    return final_result, attempt1_messages, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 internal helpers
# ---------------------------------------------------------------------------

def _build_phase2_developer_instructions() -> str:
    return "\n\n".join(
        part for part in (
            _load_prompt("tailor").strip(),
            _load_prompt_optional("tailor_candidate").strip(),
            _load_prompt_optional("tailor_role").strip(),
            _PLAN_OBEY_RULES,
            _WRITER_PACKET_RULES,
        )
        if part
    )


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
    developer_instructions = _build_phase2_developer_instructions()

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

    return _parse_phase2_response(response, PHASE2_MODEL)


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
            _load_prompt_optional("tailor_candidate").strip(),
            _load_prompt_optional("tailor_role").strip(),
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
        "tailor_task",
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
