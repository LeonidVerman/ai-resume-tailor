"""
backend/app/clients/openai_client.py

OpenAI API client for the SaaS backend.

Design notes
------------
The existing generator (src/tailor/core_generation/llm.py) already has
working OpenAI call patterns including:
  - model-level token-limit kwarg selection (_max_tokens_kwargs)
  - usage extraction (_extract_usage)
  - structured output via response_format
  - a module-level singleton client (get_client())

This backend client reuses the same ideas in a form that is:
  - instantiable / injectable (easier to mock in tests)
  - config-driven (reads from backend Settings)
  - transport-only (no prompt logic, no retry beyond SDK defaults)
  - returns normalized CompletionResult objects

The existing generator continues to manage its own OpenAI singleton
unchanged.  This client is for future backend service use only.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Regex reused from existing generator logic (llm.py _max_tokens_kwargs)
_USES_MAX_COMPLETION_TOKENS = re.compile(r"^(o\d|gpt-[5-9])", re.IGNORECASE)


# ── Result types ───────────────────────────────────────────────────────────

@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class CompletionResult:
    """Normalized result of a single chat completion call."""
    content: str                        # raw text of the first choice
    model: str                          # model actually used (from response)
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_response: Any = field(default=None, repr=False)  # original SDK response


# ── Client ─────────────────────────────────────────────────────────────────

class OpenAIClient:
    """
    Thin wrapper around the OpenAI Python SDK for backend service use.

    Parameters
    ----------
    api_key:
        OpenAI API key.  Pass explicitly or leave empty to fall back to
        the OPENAI_API_KEY environment variable (SDK default behaviour).
    default_model:
        Model used when callers do not specify one.
    """

    def __init__(self, api_key: str = "", default_model: str = "gpt-4o") -> None:
        self._api_key = api_key or None   # None → SDK reads env var
        self._default_model = default_model
        self._client: Any = None          # lazy; created on first use

    # ── Internal helpers ───────────────────────────────────────────────────

    def _get_client(self):
        """Return (and lazily create) the underlying OpenAI SDK client."""
        if self._client is None:
            from openai import OpenAI
            kwargs = {}
            if self._api_key:
                kwargs["api_key"] = self._api_key
            self._client = OpenAI(**kwargs)
        return self._client

    @staticmethod
    def _max_tokens_kwargs(model: str, max_tokens: int | None) -> dict:
        """Choose correct token-limit kwarg for the given model.

        Mirrors the generator's _max_tokens_kwargs to stay consistent.
        Newer models (o-series, gpt-5+) require max_completion_tokens.
        """
        if max_tokens is None:
            return {}
        key = (
            "max_completion_tokens"
            if _USES_MAX_COMPLETION_TOKENS.match(model)
            else "max_tokens"
        )
        return {key: max_tokens}

    @staticmethod
    def _extract_usage(response: Any) -> TokenUsage:
        """Pull token counts from a response, returning zeros if absent."""
        if response.usage:
            return TokenUsage(
                prompt_tokens=response.usage.prompt_tokens,
                completion_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
        return TokenUsage()

    # ── Public API ─────────────────────────────────────────────────────────

    def complete(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> CompletionResult:
        """
        Send a chat completion request and return a normalized result.

        Parameters
        ----------
        messages:
            List of {role, content} dicts.
        model:
            Override the instance default model.
        temperature:
            Sampling temperature.
        max_tokens:
            Token budget (mapped to the right kwarg per model family).
        response_format:
            If provided, passed directly to the API (e.g. json_schema mode).
        """
        resolved_model = model or self._default_model
        client = self._get_client()

        call_kwargs: dict[str, Any] = {
            "model": resolved_model,
            "messages": messages,
            "temperature": temperature,
            **self._max_tokens_kwargs(resolved_model, max_tokens),
        }
        if response_format is not None:
            call_kwargs["response_format"] = response_format

        logger.debug(
            "OpenAI request model=%s temperature=%s max_tokens=%s",
            resolved_model, temperature, max_tokens,
        )

        response = client.chat.completions.create(**call_kwargs)
        usage = self._extract_usage(response)
        content = response.choices[0].message.content or ""

        logger.debug(
            "OpenAI response tokens prompt=%d completion=%d",
            usage.prompt_tokens, usage.completion_tokens,
        )

        return CompletionResult(
            content=content,
            model=response.model,
            usage=usage,
            raw_response=response,
        )

    def complete_json(
        self,
        messages: list[dict],
        json_schema: dict,
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """
        Convenience wrapper for structured JSON output mode.

        Passes the json_schema as a strict response_format, matching the
        pattern used by the existing generator for Phase 1 output.
        """
        return self.complete(
            messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": json_schema,
            },
        )

    @staticmethod
    def estimate_cost_usd(usage: TokenUsage, model: str) -> float:
        """
        Rough cost estimate based on known public pricing (USD).
        Returns 0.0 for unknown models — do not rely on for billing.
        """
        # Prices per 1K tokens (input, output) as of 2026-Q1
        _PRICING: dict[str, tuple[float, float]] = {
            "gpt-4o":       (0.0025, 0.01),
            "gpt-4o-mini":  (0.00015, 0.0006),
            "gpt-4-turbo":  (0.01, 0.03),
        }
        # Normalize model key (strip date suffixes like -2024-08-06)
        base = model.split("-20")[0].split("-20")[0]
        prices = _PRICING.get(base, (0.0, 0.0))
        return (
            usage.prompt_tokens / 1000 * prices[0]
            + usage.completion_tokens / 1000 * prices[1]
        )


# ── Factory ────────────────────────────────────────────────────────────────

def make_openai_client(api_key: str = "", default_model: str = "gpt-4o") -> OpenAIClient:
    """Create an OpenAIClient from explicit parameters."""
    return OpenAIClient(api_key=api_key, default_model=default_model)


def make_openai_client_from_settings() -> OpenAIClient:
    """Create an OpenAIClient from application settings."""
    from backend.app.config import get_settings
    settings = get_settings()
    return OpenAIClient(
        api_key=settings.openai_api_key,
        default_model="gpt-4o",
    )
