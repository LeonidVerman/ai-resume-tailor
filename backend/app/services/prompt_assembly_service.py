"""
backend/app/services/prompt_assembly_service.py

Prompt assembly service — thin wrapper around the existing generator's
prompt loading utilities (tailor.prompts).

The existing generator loads prompts from the filesystem relative to the
project root (PROMPTS_DIR = BASE_DIR / "prompts").  This service reuses
those utilities unchanged so the backend shares the exact same prompt
files.  No prompt text is duplicated.

Responsibilities
----------------
- Load named prompt templates and substitute variables
- Load the candidate profile JSON string
- Provide a stable interface for the generation service
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class PromptAssemblyService:
    """
    Assemble prompts for the two-phase generation pipeline.

    All loading is delegated to the existing generator's tailor.prompts
    module so prompt files are never duplicated.
    """

    def load_prompt(self, name: str, **kwargs: str) -> str:
        """
        Load prompts/<name>.txt and substitute {placeholder} values.

        Delegates to tailor.prompts._load_prompt unchanged.
        Raises FileNotFoundError if the prompt file does not exist.
        """
        from tailor.prompts import _load_prompt
        logger.debug("Loading prompt: %s kwargs=%s", name, list(kwargs))
        return _load_prompt(name, **kwargs)

    def load_prompt_optional(self, name: str, **kwargs: str) -> str:
        """Like load_prompt but returns '' when the file is missing."""
        from tailor.prompts import _load_prompt_optional
        return _load_prompt_optional(name, **kwargs)

    def load_candidate_profile(self) -> str:
        """
        Load profile/candidate_profile.json as a raw string.

        Returns an empty string when the file is absent so callers
        degrade gracefully.
        """
        from tailor.prompts import _load_candidate_profile
        profile = _load_candidate_profile()
        if not profile:
            logger.warning("Candidate profile file is missing or empty")
        return profile

    def load_resume_template_text(self) -> str:
        """
        Read the resume DOCX template as plain text.

        Used to provide the generator with the current template structure.
        """
        from tailor.config import RESUME_TEMPLATE
        from tailor.docx.template_fill import read_docx
        try:
            return read_docx(str(RESUME_TEMPLATE))
        except Exception as exc:
            logger.error("Failed to read resume template: %s", exc)
            raise

    def load_cover_template_text(self) -> str:
        """
        Read the cover letter DOCX template as plain text.
        """
        from tailor.config import COVER_TEMPLATE
        from tailor.docx.template_fill import read_docx
        try:
            return read_docx(str(COVER_TEMPLATE))
        except Exception as exc:
            logger.error("Failed to read cover letter template: %s", exc)
            raise
