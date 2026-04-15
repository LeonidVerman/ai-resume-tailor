"""
backend/app/services/resume_classification_service.py

Upload-time LLM classification of resume templates.

Responsibilities
----------------
- Accept a parsed ResumeDocument (or build one from raw bytes when needed)
- Build a compact ClassificationInput from the IR
- Call the LLM with the classification prompt and JSON schema
- Parse the response into a ClassificationOutput
- Persist the result in structured_resumes.classification_jsonb
- Expose a retrieval helper for the admin debug endpoint

This service is additive (Phase 1): it does NOT affect tailoring or rendering.
All errors are caught and logged so a classification failure never blocks upload.

Prompt:   prompts/classification/classify_template_resume.txt
Schema:   prompts/classification/classification_schema.json
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from backend.app.clients.openai_client import make_openai_client_from_settings
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository

logger = logging.getLogger(__name__)

# Prompt and schema paths relative to the tailor package config.
_PROMPT_NAME = "classification/classify_template_resume"


def _load_schema() -> dict:
    """Load the OpenAI response_format JSON schema from the prompts directory."""
    from tailor.config import PROMPTS_DIR
    schema_path = PROMPTS_DIR / "classification" / "classification_schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def _build_ir(norm_data: bytes, template_ir_dict: dict | None):
    """Return a ResumeDocument for classification.

    For PDF uploads template_ir_dict is already available (parsed during
    normalization).  For DOCX uploads we write the bytes to a temp file and
    parse with parse_docx (stable IDs are assigned inside the parser).
    """
    if template_ir_dict:
        from tailor.compiler.models import ResumeDocument, assign_stable_ids
        doc = ResumeDocument.from_dict(template_ir_dict)
        # Re-assign IDs in case the serialized IR pre-dates the stable-ID feature.
        if not any(s.section_id for s in doc.sections):
            assign_stable_ids(doc)
        return doc

    # DOCX path: write to temp file, parse, return.
    suffix = ".docx"
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(norm_data)
        from tailor.compiler.docx_parser import parse_docx
        return parse_docx(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


class ResumeClassificationService:
    """
    Classify an uploaded resume template via LLM and persist the result.

    Parameters
    ----------
    db: SQLAlchemy Session — used to persist the classification result.
    """

    def __init__(self, db: Session) -> None:
        self._db = db
        self._repo = StructuredResumeRepository(db)

    def classify_and_store(
        self,
        resume_id: int,
        norm_data: bytes,
        template_ir_dict: dict | None,
    ) -> dict | None:
        """Run classification and persist result in classification_jsonb.

        Returns the raw classification dict on success, None on any failure.
        Errors are logged; they do NOT propagate to the caller.
        """
        resume = self._repo.get_by_id(resume_id)
        if resume is None:
            logger.warning("classify_and_store: resume %s not found", resume_id)
            return None

        try:
            result = self._classify(resume_id, norm_data, template_ir_dict)
        except Exception as exc:
            logger.error(
                "Classification failed for resume=%s: %s",
                resume_id, exc, exc_info=True,
            )
            return None

        try:
            self._repo.update(resume, classification_jsonb=result)
            self._db.commit()
            logger.info("Classification stored for resume=%s", resume_id)
        except Exception as exc:
            logger.error(
                "Failed to persist classification for resume=%s: %s",
                resume_id, exc, exc_info=True,
            )
            self._db.rollback()

        return result

    def get_classification(self, resume_id: int) -> dict | None:
        """Return the stored classification dict, or None if not yet classified."""
        resume = self._repo.get_by_id(resume_id)
        if resume is None:
            return None
        return resume.classification_jsonb

    # ── Internal ──────────────────────────────────────────────────────────

    def _classify(
        self,
        resume_id: int,
        norm_data: bytes,
        template_ir_dict: dict | None,
    ) -> dict:
        """Build input, call LLM, return raw classification dict."""
        from tailor.compiler.classification_models import build_classification_input
        from tailor.prompts import _load_prompt

        doc = _build_ir(norm_data, template_ir_dict)
        document_id = str(resume_id)
        cls_input = build_classification_input(doc, document_id)

        prompt_template = _load_prompt(_PROMPT_NAME)
        input_json = json.dumps(cls_input.to_dict(), ensure_ascii=False)
        full_prompt = f"{prompt_template}\n\nINPUT:\n{input_json}"

        schema = _load_schema()
        client = make_openai_client_from_settings()
        result = client.complete_json(
            messages=[{"role": "user", "content": full_prompt}],
            json_schema=schema,
            model="gpt-4o",
            temperature=0.1,
            max_tokens=8192,
        )

        classification_dict = json.loads(result.content)
        logger.debug(
            "Classification complete resume=%s sections=%d tokens=%d",
            resume_id,
            len(classification_dict.get("sections", [])),
            result.usage.total_tokens,
        )
        return classification_dict
