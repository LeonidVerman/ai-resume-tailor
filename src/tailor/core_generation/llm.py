"""OpenAI client wrapper and single-pass document tailoring."""

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from openai import OpenAI

from tailor.config import (
    SIMPLE_MODEL,
    SIMPLE_TEMPERATURE,
)
from tailor.job import JobData
from tailor.prompts import _load_candidate_profile, _load_prompt, _load_prompt_optional
from .cover_letter_validator import check_cover_letter_structure

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


@dataclass
class TailorResult:
    resume: str | None = None
    cover_letter: str | None = None


# ---------------------------------------------------------------------------
# Single-pass generation
# ---------------------------------------------------------------------------

def _load_generation_mode_overlay(mode: str | None) -> str:
    """Return the mode overlay prompt text for non-conservative modes, or ''."""
    if not mode or mode == "conservative":
        return ""
    return _load_prompt_optional(f"modes/mode_{mode}").strip()


def tailor_documents(
    job: JobData,
    resume_template: str,
    cover_template: str,
    candidate_profile: str | None = None,
    candidate_layer: str | None = None,
    generation_mode: str | None = None,
) -> tuple[TailorResult, list]:
    """Generate a tailored resume and cover letter in a single LLM call.

    Returns
    -------
    result:
        TailorResult with ``resume`` and ``cover_letter`` text fields.
    messages:
        The full message list sent to the LLM (useful for debug logging).
    """
    # candidate_layer: if provided (web mode), use it in the candidate-layer slot;
    # otherwise fall back to prompts/candidate.txt (CLI mode).
    resolved_candidate = (
        candidate_layer.strip() if candidate_layer
        else _load_prompt_optional("candidate").strip()
    )
    mode_overlay = _load_generation_mode_overlay(generation_mode)
    developer_instructions = "\n\n".join(
        part for part in (
            _load_prompt("tailor").strip(),
            resolved_candidate,
            _load_prompt_optional("role").strip(),
            mode_overlay,
        )
        if part
    )
    profile = _load_candidate_profile(candidate_profile)

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

    response_format = {"type": "json_object"}
    response = get_client().chat.completions.create(
        model=SIMPLE_MODEL,
        messages=messages,
        temperature=SIMPLE_TEMPERATURE,
        response_format=response_format,
    )

    raw = json.loads(response.choices[0].message.content)
    result = TailorResult(
        resume=raw.get("resume") or raw.get("tailored_resume"),
        cover_letter=raw.get("cover_letter") or raw.get("tailored_cover_letter"),
    )

    check_cover_letter_structure(result.cover_letter)

    llm_request = _build_llm_request_record(
        messages, SIMPLE_MODEL, SIMPLE_TEMPERATURE, response_format=response_format,
    )
    return result, llm_request


# ---------------------------------------------------------------------------
# Metadata extraction
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


_USES_MAX_COMPLETION_TOKENS_RE = re.compile(r"^(o\d|gpt-[5-9])", re.IGNORECASE)


def _max_tokens_kwargs(model: str, max_tokens: int | None) -> dict:
    """Return the correct token-limit kwarg for the given model."""
    if max_tokens is None:
        return {}
    key = "max_completion_tokens" if _USES_MAX_COMPLETION_TOKENS_RE.match(model) else "max_tokens"
    return {key: max_tokens}


def _build_llm_request_record(
    messages: list,
    model: str,
    temperature: float,
    max_tokens: int | None = None,
    response_format: dict | None = None,
) -> dict:
    """Package all OpenAI call parameters into a structured debug record."""
    record: dict = {
        "model": model,
        "temperature": temperature,
        "messages": messages,
    }
    if max_tokens is not None:
        record.update(_max_tokens_kwargs(model, max_tokens))
    if response_format is not None:
        record["response_format"] = response_format
    return record
