"""
backend/app/services/candidate_meta_service.py

Lazy generation and caching of the candidate-layer prompt.

The candidate prompt is generated from the structured candidate profile JSON
using prompts/candidate_meta.txt as the instruction layer.  It is stored in
candidate_profiles.candidate_prompt and reused across generations until the
profile is edited (at which point prompt_synched is set to False).

Model selection
---------------
The caller supplies the model string. Convention (enforced in GenerationService):
  - two_phase mode  → use phase1_model
  - single_pass     → use simple_model
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def ensure_candidate_prompt_synced(
    profile,
    model: str,
    profile_repo,
) -> str:
    """
    Return the stored candidate_prompt, generating it via LLM if stale.

    - If prompt_synched is True and candidate_prompt is non-empty: return cached.
    - Otherwise: call LLM, persist result, return new prompt.

    Raises RuntimeError if generation fails.
    Never marks prompt_synched=True unless the LLM response is valid.
    Does not clear a previously stored prompt on failure.
    """
    if profile.prompt_synched and profile.candidate_prompt:
        logger.debug("Candidate prompt is synced; reusing cache (profile=%s)", profile.id)
        return profile.candidate_prompt

    logger.info(
        "Generating candidate prompt (profile=%s model=%s)", profile.id, model
    )
    meta_prompt = _load_meta_prompt()
    profile_json = json.dumps(profile.profile_jsonb, ensure_ascii=False, indent=2)
    candidate_prompt = _call_meta_llm(meta_prompt, profile_json, model)

    # Persist only after successful, non-empty response.
    profile_repo.update(
        profile,
        candidate_prompt=candidate_prompt,
        prompt_synched=True,
    )
    logger.info("Candidate prompt stored (profile=%s)", profile.id)
    return candidate_prompt


# ── Internal helpers ────────────────────────────────────────────────────────


def _load_meta_prompt() -> str:
    from tailor.prompts import _load_prompt
    return _load_prompt("candidate_meta")


def _call_meta_llm(meta_prompt: str, profile_json: str, model: str) -> str:
    import tailor.core_generation.llm as _llm

    client = _llm.get_client()
    kwargs = _llm._max_tokens_kwargs(model, None)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": meta_prompt},
            {"role": "user", "content": f"CANDIDATE_PROFILE:\n{profile_json}"},
        ],
        temperature=0.3,
        **kwargs,
    )
    content = response.choices[0].message.content
    if not content or not content.strip():
        raise RuntimeError(
            f"Candidate meta LLM returned empty response (model={model})"
        )
    return content.strip()
