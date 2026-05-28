"""
backend/app/services/resume_classification_service.py

Upload-time LLM classification of resume templates.

Responsibilities
----------------
- Accept a parsed ResumeDocument (or build one from raw bytes when needed)
- Build a compact ClassificationInput from the IR
- Call the LLM with the classification prompt and JSON schema
- Validate the LLM output
- If invalid sections exist: attempt section-level LLM repair (single pass)
- Revalidate after repair; downgrade any remaining invalid sections
- Persist the result envelope in structured_resumes.classification_jsonb
- Expose a retrieval helper for the admin debug endpoint

Phase 1:  LLM classification.
Phase 2a: Deterministic validation + safe section downgrade (no LLM retry).
Phase 2b: LLM repair of invalid sections before downgrade (single pass).

Prompt (classify):  prompts/classification/classify_template_resume.txt
Schema (classify):  prompts/classification/classification_schema.json
Prompt (repair):    prompts/classification/repair_classification.txt
Schema (repair):    prompts/classification/repair_classification_schema.json

Stored envelope (classification_jsonb):
{
  "llm_input":                  {...},   # normalized input actually sent to the classifier LLM
  "llm_input_raw":              {...},   # pre-normalization input (before heading removal / role clearing / meta split)
  "classification_input_normalization_report": {...},  # what normalization did (splits, clears, removals)
  "raw_classification":         {...},   # verbatim initial LLM output (para_ids may be synthetic)
  "validation":                 {...},   # validate_classification() result (initial)
  "repair_input":               {...|null},  # repair request payload (null if no repair needed)
  "repair_response":            {...|null},  # verbatim repair LLM output (null if not attempted / failed)
  "validation_after_repair":    {...|null},  # validate_classification() after repair merge (null if no repair)
  "classification":             {...},   # final contract-safe classification (synthetic para_ids resolved)
  "status":                     "valid" | "repaired" | "repaired_and_downgraded" | "downgraded" | "failed",
  "validation_error_count":     int,     # errors in initial validation
  "invalid_section_count_before_repair": int,
  "invalid_section_count_after_repair":  int,
  "repaired_section_count":     int,
  "downgraded_section_count":   int,
  "invalid_section_ids":        [...],   # kept for backward compat (ids downgraded in final)
  "recovery_applied":           bool,    # kept for backward compat
}
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

_PROMPT_NAME = "classification/classify_template_resume"
_REPAIR_PROMPT_NAME = "classification/repair_classification"

# Experience role body block type sets (mirrors classification_validator.py)
_EXP_REWRITEABLE_BODY_TYPES: frozenset[str] = frozenset({
    "bullet", "role_achievement_bullet", "role_responsibility_bullet",
})
_EXP_PRESERVED_BODY_TYPES: frozenset[str] = frozenset({
    "role_intro", "role_highlight", "role_project_label", "role_project_context",
    "role_tech_stack", "role_key_technologies", "role_tools",
    "role_nested_detail", "role_freeform_note",
})


def _load_schema() -> dict:
    from tailor.config import PROMPTS_DIR
    schema_path = PROMPTS_DIR / "classification" / "classification_schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def _load_repair_schema() -> dict:
    from tailor.config import PROMPTS_DIR
    schema_path = PROMPTS_DIR / "classification" / "repair_classification_schema.json"
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def _build_ir(norm_data: bytes, template_ir_dict: dict | None):
    if template_ir_dict:
        from tailor.compiler.models import ResumeDocument, assign_stable_ids
        doc = ResumeDocument.from_dict(template_ir_dict)
        if not any(s.section_id for s in doc.sections):
            assign_stable_ids(doc)
        return doc

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
        """Run classification and persist result in classification_jsonb."""
        resume = self._repo.get_by_id(resume_id)
        if resume is None:
            logger.warning("classify_and_store: resume %s not found", resume_id)
            return None

        try:
            _llm_input, result = self._classify(resume_id, norm_data, template_ir_dict)
        except Exception as exc:
            logger.error(
                "Classification failed for resume=%s: %s",
                resume_id, exc, exc_info=True,
            )
            return None

        try:
            self._repo.update(resume, classification_jsonb=result)
            self._db.commit()
            logger.info("Classification stored for resume=%s status=%s", resume_id, result.get("status"))
        except Exception as exc:
            logger.error(
                "Failed to persist classification for resume=%s: %s",
                resume_id, exc, exc_info=True,
            )
            self._db.rollback()

        return result

    def get_classification(self, resume_id: int) -> dict | None:
        resume = self._repo.get_by_id(resume_id)
        if resume is None:
            return None
        return resume.classification_jsonb

    def classify_bytes(self, norm_data: bytes, template_ir_dict: dict | None) -> dict:
        _llm_input, classification = self._classify(0, norm_data, template_ir_dict)
        return classification

    def classify_bytes_with_input(
        self, norm_data: bytes, template_ir_dict: dict | None
    ) -> tuple[dict, dict]:
        return self._classify(0, norm_data, template_ir_dict)

    # ── Internal ──────────────────────────────────────────────────────────

    _INVALID_EDUCATION_BLOCK_TYPES = frozenset({
        "role_header", "role_meta", "other_paragraph",
        "summary_paragraph", "skills_paragraph",
        "project_entry", "additional_item",
    })

    @staticmethod
    def _normalize_education_blocks(classification: dict) -> dict:
        """Return classification with any invalid block types inside education
        sections converted to education_entry + preserve.

        This is a deterministic safety net applied after each LLM call so that
        mis-classified parser-semantic lines (e.g. pipe-format degree lines
        tagged role_header) never survive into the final output as wrong types.
        """
        invalid_types = ResumeClassificationService._INVALID_EDUCATION_BLOCK_TYPES
        sections = classification.get("sections", [])
        if not any(
            s.get("semantic_type") == "education"
            for s in sections
            if isinstance(s, dict)
        ):
            return classification  # fast path: no education sections

        result = dict(classification)
        new_sections = []
        for sec in sections:
            if not isinstance(sec, dict) or sec.get("semantic_type") != "education":
                new_sections.append(sec)
                continue
            blocks = sec.get("blocks", [])
            if not any(b.get("semantic_type") in invalid_types for b in blocks if isinstance(b, dict)):
                new_sections.append(sec)
                continue
            fixed_blocks = []
            for b in blocks:
                if isinstance(b, dict) and b.get("semantic_type") in invalid_types:
                    b = dict(b)
                    b["semantic_type"] = "education_entry"
                    b["rewrite_policy"] = "preserve"
                fixed_blocks.append(b)
            sec = dict(sec)
            sec["blocks"] = fixed_blocks
            new_sections.append(sec)
        result["sections"] = new_sections
        return result

    @staticmethod
    def _normalize_experience_blocks(classification: dict) -> dict:
        """Auto-correct common LLM mistakes in experience sections.

        Applied after each LLM call to ensure the experience section always
        passes the validator without needing a repair round-trip:
          - Forces rewrite_policy = "rewrite_bullets_only"
          - Forces preserve_heading / preserve_body_structure = True
          - Clears top-level blocks[] (must be empty for experience)
          - Fixes header_blocks → type=role_header, policy=preserve
          - Fixes meta_blocks  → type=role_meta,   policy=preserve
          - Fixes body_blocks  → correct policy per type;
            unknown types are converted to role_achievement_bullet
        """
        sections = classification.get("sections", [])
        if not any(
            s.get("semantic_type") == "experience"
            for s in sections
            if isinstance(s, dict)
        ):
            return classification

        result = dict(classification)
        new_sections = []
        for sec in sections:
            if not isinstance(sec, dict) or sec.get("semantic_type") != "experience":
                new_sections.append(sec)
                continue

            sec = dict(sec)
            sec["rewrite_policy"] = "rewrite_bullets_only"
            sec["preserve_heading"] = True
            sec["preserve_body_structure"] = True
            sec["blocks"] = []

            new_roles = []
            for role in sec.get("roles", []):
                if not isinstance(role, dict):
                    new_roles.append(role)
                    continue
                role = dict(role)

                fixed_headers = []
                for b in role.get("header_blocks", []):
                    b = dict(b)
                    b["semantic_type"] = "role_header"
                    b["rewrite_policy"] = "preserve"
                    fixed_headers.append(b)
                role["header_blocks"] = fixed_headers

                fixed_meta = []
                for b in role.get("meta_blocks", []):
                    b = dict(b)
                    b["semantic_type"] = "role_meta"
                    b["rewrite_policy"] = "preserve"
                    fixed_meta.append(b)
                role["meta_blocks"] = fixed_meta

                fixed_body = []
                for b in role.get("body_blocks", []):
                    b = dict(b)
                    st = b.get("semantic_type", "")
                    if st in _EXP_REWRITEABLE_BODY_TYPES:
                        b["rewrite_policy"] = "rewrite_text"
                    elif st in _EXP_PRESERVED_BODY_TYPES:
                        b["rewrite_policy"] = "preserve"
                    else:
                        b["semantic_type"] = "role_achievement_bullet"
                        b["rewrite_policy"] = "rewrite_text"
                    fixed_body.append(b)
                role["body_blocks"] = fixed_body

                new_roles.append(role)
            sec["roles"] = new_roles
            new_sections.append(sec)

        result["sections"] = new_sections
        return result

    @staticmethod
    def _normalize_projects_blocks(classification: dict) -> dict:
        """Auto-correct common LLM mistakes in projects sections.

        Applied after each LLM call:
          - Forces rewrite_policy = "preserve"
          - Flattens any roles[] into top-level blocks with type project_entry
          - Normalises all block types to project_entry or other_paragraph
          - Clears roles[]
        """
        sections = classification.get("sections", [])
        if not any(
            s.get("semantic_type") == "projects"
            for s in sections
            if isinstance(s, dict)
        ):
            return classification

        result = dict(classification)
        new_sections = []
        for sec in sections:
            if not isinstance(sec, dict) or sec.get("semantic_type") != "projects":
                new_sections.append(sec)
                continue

            sec = dict(sec)
            sec["rewrite_policy"] = "preserve"

            seen_pids: set[str] = set()
            merged_blocks: list[dict] = []

            for b in sec.get("blocks", []):
                if not isinstance(b, dict):
                    continue
                pid = b.get("para_id", "")
                if pid not in seen_pids:
                    seen_pids.add(pid)
                    merged_blocks.append(b)

            for role in sec.get("roles", []):
                if not isinstance(role, dict):
                    continue
                for group in ("header_blocks", "meta_blocks", "body_blocks"):
                    for b in role.get(group, []):
                        if not isinstance(b, dict):
                            continue
                        pid = b.get("para_id", "")
                        if pid not in seen_pids:
                            seen_pids.add(pid)
                            merged_blocks.append(b)

            fixed_blocks = []
            for b in merged_blocks:
                b = dict(b)
                if b.get("semantic_type") not in ("project_entry", "other_paragraph"):
                    b["semantic_type"] = "project_entry"
                b["rewrite_policy"] = "preserve"
                fixed_blocks.append(b)

            sec["blocks"] = fixed_blocks
            sec["roles"] = []
            new_sections.append(sec)

        result["sections"] = new_sections
        return result

    @staticmethod
    def _build_repair_payload(
        llm_input_dict: dict,
        raw_classification: dict,
        validation: dict,
    ) -> dict:
        """Assemble the repair request payload for all invalid sections.

        Each invalid section contributes one entry with:
          - parser_section:   the parser-produced section from llm_input_dict
          - classified_section: the current (invalid) classification output
          - validation_errors: error dicts scoped to that section
        """
        from tailor.compiler.classification_validator import get_errors_by_section

        errors_by_sid = get_errors_by_section(validation)

        # Index parser sections by section_id for fast lookup
        parser_secs: dict[str, dict] = {
            s["section_id"]: s
            for s in llm_input_dict.get("sections", [])
            if isinstance(s, dict) and s.get("section_id")
        }

        # Index classified sections by section_id
        classified_secs: dict[str, dict] = {
            s.get("section_id", ""): s
            for s in raw_classification.get("sections", [])
            if isinstance(s, dict)
        }

        invalid_section_ids = {
            sr["section_id"]
            for sr in validation.get("section_results", [])
            if not sr.get("is_valid", True) and sr.get("section_id")
        }

        invalid_sections = []
        for sid in invalid_section_ids:
            classified = classified_secs.get(sid)
            if classified is None:
                continue
            invalid_sections.append({
                "parser_section": parser_secs.get(sid, {}),
                "classified_section": classified,
                "validation_errors": errors_by_sid.get(sid, []),
            })

        return {
            "document_id": raw_classification.get("document_id", ""),
            "source_kind": raw_classification.get("source_kind", ""),
            "invalid_sections": invalid_sections,
        }

    @staticmethod
    def _merge_repaired_sections(
        raw_classification: dict,
        repaired_sections: list[dict],
        invalid_ids: set[str],
    ) -> dict:
        """Return a new classification dict with repaired sections substituted.

        Rules:
        - Only sections whose section_id was invalid may be replaced.
        - If repair omits an invalid section, the original stays (downgrade will handle it).
        - If repair returns an unknown section_id, it is ignored.
        - Already-valid sections are never touched.
        """
        repair_by_id: dict[str, dict] = {}
        for sec in repaired_sections:
            if not isinstance(sec, dict):
                continue
            sid = sec.get("section_id", "")
            if sid in invalid_ids:
                repair_by_id[sid] = sec
            else:
                if sid:
                    logger.debug("Repair returned unknown/valid section_id=%s — ignored", sid)

        result = dict(raw_classification)
        result["sections"] = [
            repair_by_id.get(sec.get("section_id", ""), sec)
            if sec.get("section_id", "") in invalid_ids
            else sec
            for sec in raw_classification.get("sections", [])
        ]
        return result

    def _call_repair(self, repair_payload: dict, resume_id: int) -> dict | None:
        """Call the repair LLM with the repair payload.

        Returns the parsed repair response dict, or None if the call fails or
        the response is malformed.
        """
        from tailor.prompts import _load_prompt

        try:
            repair_prompt = _load_prompt(_REPAIR_PROMPT_NAME)
        except FileNotFoundError:
            logger.error("Repair prompt file not found: %s", _REPAIR_PROMPT_NAME)
            return None

        repair_schema = _load_repair_schema()
        input_json = json.dumps(repair_payload, ensure_ascii=False)
        full_prompt = f"{repair_prompt}\n\nINPUT:\n{input_json}"

        client = make_openai_client_from_settings()
        try:
            result = client.complete_json(
                messages=[{"role": "user", "content": full_prompt}],
                json_schema=repair_schema,
                model="gpt-4o-mini",
                temperature=0.1,
                max_tokens=8192,
            )
        except Exception as exc:
            logger.error(
                "Repair LLM call failed for resume=%s: %s", resume_id, exc, exc_info=True,
            )
            return None

        try:
            parsed = json.loads(result.content)
        except Exception as exc:
            logger.error(
                "Repair response is not valid JSON for resume=%s: %s", resume_id, exc,
            )
            return None

        if not isinstance(parsed.get("sections"), list):
            logger.error(
                "Repair response missing sections[] for resume=%s", resume_id,
            )
            return None

        logger.debug(
            "Repair LLM complete resume=%s repaired_sections=%d tokens=%d",
            resume_id,
            len(parsed.get("sections", [])),
            result.usage.total_tokens,
        )
        return parsed

    def _classify(
        self,
        resume_id: int,
        norm_data: bytes,
        template_ir_dict: dict | None,
    ) -> tuple[dict, dict]:
        """Build input, call LLM, validate, repair, revalidate, downgrade.

        Returns
        -------
        (llm_input_dict, envelope_dict)

        envelope_dict contains:
          raw_classification, validation, repair_input, repair_response,
          validation_after_repair, classification (final), status,
          validation_error_count, invalid_section_count_before_repair,
          invalid_section_count_after_repair, repaired_section_count,
          downgraded_section_count, invalid_section_ids, recovery_applied.
        """
        from tailor.compiler.classification_models import build_classification_input
        from tailor.compiler.classification_validator import (
            validate_classification,
            downgrade_invalid_sections,
        )
        from tailor.prompts import _load_prompt

        doc = _build_ir(norm_data, template_ir_dict)
        document_id = str(resume_id)
        cls_input = build_classification_input(doc, document_id)
        raw_llm_input_dict = cls_input.to_dict()

        from tailor.compiler.classification_normalizer import (
            normalize_classification_input,
            resolve_synthetic_para_ids,
        )
        cls_input_normalized, sidecar, norm_report = normalize_classification_input(cls_input)
        llm_input_dict = cls_input_normalized.to_dict()

        prompt_template = _load_prompt(_PROMPT_NAME)
        input_json = json.dumps(llm_input_dict, ensure_ascii=False)
        full_prompt = f"{prompt_template}\n\nINPUT:\n{input_json}"

        schema = _load_schema()
        client = make_openai_client_from_settings()
        result = client.complete_json(
            messages=[{"role": "user", "content": full_prompt}],
            json_schema=schema,
            model="gpt-4o-mini",
            temperature=0.1,
            max_tokens=8192,
        )

        raw_classification = json.loads(result.content)
        raw_classification = self._normalize_education_blocks(raw_classification)
        raw_classification = self._normalize_experience_blocks(raw_classification)
        raw_classification = self._normalize_projects_blocks(raw_classification)
        logger.debug(
            "Classification complete resume=%s sections=%d tokens=%d",
            resume_id,
            len(raw_classification.get("sections", [])),
            result.usage.total_tokens,
        )

        # ── Step 2: initial validation ────────────────────────────────────
        initial_validation = validate_classification(raw_classification)
        invalid_ids_before = [
            sr["section_id"]
            for sr in initial_validation["section_results"]
            if not sr["is_valid"]
        ]
        invalid_count_before = len(invalid_ids_before)

        # ── Step 3: repair invalid sections if any ────────────────────────
        repair_input: dict | None = None
        repair_response: dict | None = None
        validation_after_repair: dict | None = None
        merged = raw_classification

        if invalid_ids_before:
            repair_input = self._build_repair_payload(
                llm_input_dict, raw_classification, initial_validation
            )
            repair_response = self._call_repair(repair_input, resume_id)

            if repair_response is not None:
                repaired_sections = repair_response.get("sections", [])
                merged = self._merge_repaired_sections(
                    raw_classification, repaired_sections, set(invalid_ids_before)
                )
                merged = self._normalize_education_blocks(merged)
                merged = self._normalize_experience_blocks(merged)
                merged = self._normalize_projects_blocks(merged)
                # ── Step 4: revalidate merged result ──────────────────────
                validation_after_repair = validate_classification(merged)
                logger.info(
                    "Classification repair resume=%s invalid_before=%d repair_sections=%d",
                    resume_id, invalid_count_before, len(repaired_sections),
                )
            else:
                logger.warning(
                    "Repair call produced no usable result for resume=%s — falling back to downgrade",
                    resume_id,
                )

        # ── Step 5: downgrade remaining invalid sections ──────────────────
        check_validation = validation_after_repair or initial_validation
        invalid_ids_after = [
            sr["section_id"]
            for sr in check_validation["section_results"]
            if not sr["is_valid"]
        ]
        invalid_count_after = len(invalid_ids_after)
        invalid_set_after = set(invalid_ids_after)

        final = downgrade_invalid_sections(merged, invalid_set_after)
        final = resolve_synthetic_para_ids(final, sidecar)

        # ── Step 6: compute status and counts ────────────────────────────
        repaired_count = invalid_count_before - invalid_count_after
        downgraded_count = invalid_count_after

        if invalid_count_before == 0:
            status = "valid"
        elif repair_response is not None and invalid_count_after == 0:
            status = "repaired"
        elif repair_response is not None and invalid_count_after > 0:
            status = "repaired_and_downgraded"
        elif repair_response is None and invalid_count_before > 0:
            status = "downgraded"
        else:
            status = "valid"

        if status != "valid":
            logger.warning(
                "Classification result resume=%s status=%s before=%d after=%d "
                "repaired=%d downgraded=%d",
                resume_id, status,
                invalid_count_before, invalid_count_after,
                repaired_count, downgraded_count,
            )
        else:
            logger.debug("Classification validation passed for resume=%s", resume_id)

        envelope: dict = {
            "llm_input": llm_input_dict,
            "llm_input_raw": raw_llm_input_dict,
            "classification_input_normalization_report": norm_report,
            "raw_classification": raw_classification,
            "validation": initial_validation,
            "repair_input": repair_input,
            "repair_response": repair_response,
            "validation_after_repair": validation_after_repair,
            "classification": final,
            "status": status,
            "validation_error_count": len(initial_validation["errors"]),
            "invalid_section_count_before_repair": invalid_count_before,
            "invalid_section_count_after_repair": invalid_count_after,
            "repaired_section_count": repaired_count,
            "downgraded_section_count": downgraded_count,
            # backward-compat keys
            "invalid_section_ids": invalid_ids_after,
            "recovery_applied": bool(invalid_set_after),
        }
        return llm_input_dict, envelope
