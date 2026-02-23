"""OpenAI client wrapper and document tailoring logic."""

import json
from dataclasses import dataclass

from openai import OpenAI

from tailor.job import JobData
from tailor.prompts import _load_candidate_profile, _load_prompt

_client: OpenAI | None = None


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


def tailor_documents(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[TailorResult, list]:
    """Generate a tailored resume and cover letter for the given job.

    Parameters
    ----------
    job:
        Structured job data (company, title, description).
    resume_template:
        Plain-text content of the resume template (read from the .docx).
    cover_template:
        Plain-text content of the cover letter template.

    Returns
    -------
    result:
        TailorResult with ``resume`` and ``cover_letter`` text fields.
    messages:
        The full message list sent to the LLM (useful for debug logging).
    """
    developer_instructions = _load_prompt("tailor")
    profile = _load_candidate_profile()

    messages: list = [
        {"role": "developer", "content": developer_instructions},
    ]
    if profile:
        messages.append({"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile}"})
    messages.append({"role": "user", "content": f"JOB_DESCRIPTION:\n{job.description}"})
    messages.append({"role": "user", "content": f"MASTER_RESUME:\n{resume_template}"})
    messages.append({"role": "user", "content": f"MASTER_COVER_LETTER:\n{cover_template}"})
    task = _load_prompt("tailor_task", company=job.company, job_title=job.job_title)
    messages.append({"role": "user", "content": task})

    response = get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        temperature=0.3,
        response_format={"type": "json_object"},
    )

    raw = json.loads(response.choices[0].message.content)
    result = TailorResult(
        resume=raw.get("resume"),
        cover_letter=raw.get("cover_letter"),
    )
    return result, messages
