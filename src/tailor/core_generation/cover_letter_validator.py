"""Cover letter structural validation for single-pass generation.

Runs a lightweight paragraph-count check and logs a warning when the
cover letter body falls outside the expected 3–4 paragraph range.
No exception is raised; the output is returned as-is.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def check_cover_letter_structure(cover_letter: str | None) -> None:
    """Warn if the cover letter body does not have 3–4 paragraphs.

    Parameters
    ----------
    cover_letter:
        Raw cover letter text as returned by the LLM.  None is a no-op.
    """
    if not cover_letter:
        return

    from tailor.cover_letter import parse_cover_letter

    try:
        parsed = parse_cover_letter(cover_letter)
    except Exception as exc:
        logger.warning("Cover letter structure check failed to parse: %s", exc)
        return

    n = len(parsed.body_paragraphs)
    if n < 3 or n > 4:
        logger.warning(
            "Cover letter has %d body paragraph(s); expected 3–4. "
            "Output returned as-is.",
            n,
        )
