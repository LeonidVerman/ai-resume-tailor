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
from tailor.job import JobData
from tailor.prompts import _load_candidate_profile, _load_prompt, _load_prompt_optional

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

# --- Allowed values ---
_VALID_ROLE_LEVELS = {"director", "senior", "mid", "junior"}
_VALID_PRIORITIES = {"high", "medium", "low"}
_VALID_EVIDENCE_SOURCES = {"candidate_profile", "master_resume"}

# Required top-level keys for a TailoringPlan
_PLAN_REQUIRED_KEYS = {
    "role_level",
    "jd_top_themes",
    "evidence_map",
    "resume_strategy",
    "cover_letter_strategy",
    "risk_checks",
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
    - jd_top_themes has 4–7 entries.
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
            f"jd_top_themes must be a list of 4–7 items, got {len(themes) if isinstance(themes, list) else type(themes).__name__}"
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

    usage = {}
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    debug_meta = {
        "model": PHASE1_MODEL,
        "usage": usage,
        "raw_response": raw_content,
    }

    return plan_data, messages, debug_meta


# ---------------------------------------------------------------------------
# Phase 2 — tailor_documents_with_plan
# ---------------------------------------------------------------------------

_PLAN_OBEY_RULES = """
TAILORING_PLAN COMPLIANCE
You will be given a TAILORING_PLAN JSON as the first user message.
Follow it exactly: apply its resume_strategy and cover_letter_strategy.
If the plan conflicts with MASTER_RESUME or CANDIDATE_PROFILE, prefer the
source documents and adjust the plan guidance safely — never invent facts
to satisfy the plan.
Do not add skills, technologies, or claims listed in risk_checks.do_not_invent.
Preserve every metric listed in each role's keep_metrics.
""".strip()


def tailor_documents_with_plan(
    plan: dict,
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list, dict]:
    """Phase 2: generate tailored resume + cover letter following the plan.

    Parameters
    ----------
    plan:
        Validated TailoringPlan dict from Phase 1.
    job:
        Structured job data.
    resume_template:
        Plain-text content of the resume template.
    cover_template:
        Plain-text content of the cover letter template.

    Returns
    -------
    result:
        TailorResult with resume and cover_letter text.
    messages:
        Full message list sent to Phase 2 LLM.
    debug_meta:
        Dict with ``model``, ``usage``, ``raw_response``.
    """
    base_instructions = "\n\n".join(
        part for part in (
            _load_prompt("tailor").strip(),
            _load_prompt_optional("tailor_candidate").strip(),
            _load_prompt_optional("tailor_role").strip(),
            _PLAN_OBEY_RULES,
        )
        if part
    )
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
        {"role": "developer", "content": base_instructions},
        # Plan first — anchors execution
        {"role": "user", "content": f"TAILORING_PLAN:\n{json.dumps(plan, indent=2)}"},
    ]
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
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

    raw_content = response.choices[0].message.content
    raw = json.loads(raw_content)
    result = TailorResult(
        resume=raw.get("resume"),
        cover_letter=raw.get("cover_letter"),
    )

    usage = {}
    if response.usage:
        usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }

    debug_meta = {
        "model": PHASE2_MODEL,
        "usage": usage,
        "raw_response": raw_content,
    }

    return result, messages, debug_meta


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
