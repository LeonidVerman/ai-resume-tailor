"""Cover letter structural validation for single-pass generation.

Runs a lightweight paragraph-count check and logs a warning when the
cover letter body falls outside the expected 3–4 paragraph range.
No exception is raised; the output is returned as-is.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


def _count_body_paragraphs(text: str) -> int:
    """Count non-empty paragraphs after the 'Dear ...' salutation line."""
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.splitlines()
    # Find the first line starting with "Dear"
    body_start = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*Dear\b", line, re.IGNORECASE):
            body_start = i + 1
            break
    if body_start is None:
        # No salutation found — fall back to the whole text
        body_start = 0
    body = "\n".join(lines[body_start:])
    # Split on one or more blank lines; count non-empty chunks
    chunks = [c.strip() for c in re.split(r"\n{2,}", body)]
    return sum(1 for c in chunks if c)


def check_cover_letter_structure(cover_letter: str | None) -> None:
    """Warn if the cover letter body does not have 3–4 paragraphs.

    Parameters
    ----------
    cover_letter:
        Raw cover letter text as returned by the LLM.  None is a no-op.
    """
    if not cover_letter:
        return
    try:
        n = _count_body_paragraphs(cover_letter)
    except Exception as exc:
        logger.warning("Cover letter structure check failed to parse: %s", exc)
        return
    if n < 3 or n > 4:
        logger.warning(
            "Cover letter has %d body paragraph(s); expected 3–4. "
            "Output returned as-is.",
            n,
        )
