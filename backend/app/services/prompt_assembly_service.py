"""
backend/app/services/prompt_assembly_service.py

Prompt assembly service — thin wrapper around the existing generator's
prompt loading utilities (tailor.prompts), plus access to the backend
prompt asset files under backend/app/prompts/.

Two prompt namespaces
---------------------
1. Generator prompts (prompts/*.txt at project root)
   Loaded via tailor.prompts._load_prompt().
   Used directly by the two-phase pipeline (plan_tailoring, tailor_documents_with_plan).

2. Backend prompt assets (backend/app/prompts/<category>/)
   Loaded via load_backend_prompt_asset() / get_prompt_version().
   Externalized copies for versioning, inspection, and future backend use.
   These do NOT replace generator prompt loading in v1.

Responsibilities
----------------
- Load named prompt templates and substitute variables
- Load the candidate profile JSON string
- Provide a stable interface for the generation service
- Read backend prompt asset files (system.md, output_schema.json, version.txt)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Directory containing backend prompt assets
_BACKEND_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


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

    # ── Backend prompt asset access ─────────────────────────────────────────

    def load_backend_prompt_asset(self, category: str, filename: str) -> str:
        """
        Read a file from backend/app/prompts/<category>/<filename>.

        Parameters
        ----------
        category:
            Prompt category directory name: 'resume_tailor', 'cover_letter',
            or 'evaluation'.
        filename:
            File to read: 'system.md', 'output_schema.json', or 'version.txt'.

        Returns the file contents as a string.
        Raises FileNotFoundError if the asset does not exist.
        """
        path = _BACKEND_PROMPTS_DIR / category / filename
        if not path.is_file():
            raise FileNotFoundError(
                f"Backend prompt asset not found: {path}\n"
                f"Expected at backend/app/prompts/{category}/{filename}"
            )
        logger.debug("Loading backend prompt asset: %s/%s", category, filename)
        return path.read_text(encoding="utf-8")

    def load_backend_output_schema(self, category: str) -> dict:
        """
        Load and parse output_schema.json for the given prompt category.

        Returns the parsed JSON dict.
        """
        raw = self.load_backend_prompt_asset(category, "output_schema.json")
        return json.loads(raw)

    def get_prompt_version(self, category: str) -> str:
        """
        Return the version string from backend/app/prompts/<category>/version.txt.

        Strips trailing whitespace. Returns 'unknown' if the file is missing.
        """
        try:
            return self.load_backend_prompt_asset(category, "version.txt").strip()
        except FileNotFoundError:
            logger.warning("version.txt missing for prompt category: %s", category)
            return "unknown"
