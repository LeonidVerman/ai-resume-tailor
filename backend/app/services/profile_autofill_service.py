"""
backend/app/services/profile_autofill_service.py

Candidate profile autofill service.

Given a resume already stored in structured_resumes, calls the LLM once to
produce a full CandidateProfileDocument draft.  The draft is cached in
candidate_profile_resume_drafts (one row per user+resume pair) so repeat
requests reuse the stored result.  A SHA-256 hash of resume_jsonb detects
when the resume has changed since the draft was generated (is_stale flag).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from backend.app.clients.openai_client import make_openai_client_from_settings
from backend.app.db.repositories.candidate_profile_resume_draft_repository import (
    CandidateProfileResumeDraftRepository,
)
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.schemas.autofill import AutofillDraftResponse
from backend.app.schemas.candidate_profile import CandidateProfileDocument
from tailor.prompts import _load_prompt

logger = logging.getLogger(__name__)

_AUTOFILL_PROMPT_NAME = "autofill/autofill_from_resume"


# ── JSON schema passed to OpenAI structured-output mode ───────────────────────
# Mirrors CandidateProfileDocument structure; all leaf fields are optional
# so partial resumes still produce valid output.

_PROFILE_SCHEMA: dict = {
    "name": "candidate_profile",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        # OpenAI strict mode: every key in properties must appear in required.
        # Optional fields use anyOf [type, null] to allow the model to omit them.
        "required": [
            "candidate_profile_version",
            "candidate",
            "contacts",
            "domains",
            "experience_highlights",
            "technical_skills",
            "leadership",
            "ai_tooling_practice",
            "role_fit_themes",
            "constraints_and_preferences",
            "claim_boundaries",
        ],
        "properties": {
            "candidate_profile_version": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "candidate": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "headline", "summary"],
                "properties": {
                    "name": {"type": "string"},
                    "headline": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "summary": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
            },
            "contacts": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["email", "phone", "linkedin_url", "location"],
                        "properties": {
                            "email": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "phone": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "linkedin_url": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                            "location": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "domains": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["primary", "secondary"],
                        "properties": {
                            "primary": {"type": "array", "items": {"type": "string"}},
                            "secondary": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "experience_highlights": {
                "anyOf": [
                    {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": [
                                "area", "market", "employer_relationship", "impact",
                                "team_context", "architecture_patterns",
                                "constraints_and_tradeoffs", "skills_applied",
                                "security_auth_patterns",
                            ],
                            "properties": {
                                "area": {"type": "string"},
                                "market": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                "employer_relationship": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                                "impact": {"type": "array", "items": {"type": "string"}},
                                "team_context": {"type": "array", "items": {"type": "string"}},
                                "architecture_patterns": {"type": "array", "items": {"type": "string"}},
                                "constraints_and_tradeoffs": {"type": "array", "items": {"type": "string"}},
                                "skills_applied": {"type": "array", "items": {"type": "string"}},
                                "security_auth_patterns": {"type": "array", "items": {"type": "string"}},
                            },
                        },
                    },
                    {"type": "null"},
                ],
            },
            "technical_skills": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": [
                            "languages", "backend_systems", "datastores", "infra_devops",
                            "frontend", "api_patterns", "async_messaging", "observability",
                            "security_auth_patterns", "scalability_reliability_patterns",
                        ],
                        "properties": {
                            "languages": {"type": "array", "items": {"type": "string"}},
                            "backend_systems": {"type": "array", "items": {"type": "string"}},
                            "datastores": {"type": "array", "items": {"type": "string"}},
                            "infra_devops": {"type": "array", "items": {"type": "string"}},
                            "frontend": {"type": "array", "items": {"type": "string"}},
                            "api_patterns": {"type": "array", "items": {"type": "string"}},
                            "async_messaging": {"type": "array", "items": {"type": "string"}},
                            "observability": {"type": "array", "items": {"type": "string"}},
                            "security_auth_patterns": {"type": "array", "items": {"type": "string"}},
                            "scalability_reliability_patterns": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "leadership": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["scope", "practices", "risk_management"],
                        "properties": {
                            "scope": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["team_size_max", "style_keywords"],
                                "properties": {
                                    "team_size_max": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
                                    "style_keywords": {"type": "array", "items": {"type": "string"}},
                                },
                            },
                            "practices": {"type": "array", "items": {"type": "string"}},
                            "risk_management": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "ai_tooling_practice": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["hands_on_tools", "usage_patterns", "principles", "concepts_familiarity"],
                        "properties": {
                            "hands_on_tools": {"type": "array", "items": {"type": "string"}},
                            "usage_patterns": {"type": "array", "items": {"type": "string"}},
                            "principles": {"type": "array", "items": {"type": "string"}},
                            "concepts_familiarity": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "role_fit_themes": {
                "anyOf": [
                    {"type": "array", "items": {"type": "string"}},
                    {"type": "null"},
                ],
            },
            "constraints_and_preferences": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["work_context", "communication", "resume_constraint"],
                        "properties": {
                            "work_context": {"type": "array", "items": {"type": "string"}},
                            "communication": {"type": "array", "items": {"type": "string"}},
                            "resume_constraint": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
            "claim_boundaries": {
                "anyOf": [
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["security_auth", "domain_limits", "employment_constraints"],
                        "properties": {
                            "security_auth": {"type": "array", "items": {"type": "string"}},
                            "domain_limits": {"type": "array", "items": {"type": "string"}},
                            "employment_constraints": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    {"type": "null"},
                ],
            },
        },
    },
}


def _load_autofill_prompt(raw_text: str) -> str:
    """Load prompts/autofill/autofill_from_resume.txt and inject the resume text."""
    return _load_prompt(_AUTOFILL_PROMPT_NAME, resume_text=raw_text)


# ── Service ────────────────────────────────────────────────────────────────────

class ProfileAutofillService:
    def __init__(self, db: Session) -> None:
        self._db = db

    @staticmethod
    def _resume_hash(resume_jsonb: dict) -> str:
        """SHA-256 hex digest of the resume JSONB for staleness detection."""
        return hashlib.sha256(
            json.dumps(resume_jsonb, sort_keys=True).encode()
        ).hexdigest()

    @staticmethod
    def _coerce_draft(raw: dict) -> CandidateProfileDocument:
        """
        Validate LLM output into CandidateProfileDocument.

        The JSON schema allows null for optional top-level fields so that the
        model can omit sections it has no data for. CandidateProfileDocument
        uses default_factory for those fields, so passing null raises a
        ValidationError. Stripping null values lets the defaults kick in.
        """
        cleaned = {k: v for k, v in raw.items() if v is not None}
        return CandidateProfileDocument.model_validate(cleaned)

    def get_draft(self, user_id: str, resume_id: int) -> AutofillDraftResponse | None:
        """
        Return the cached draft for this resume, with an is_stale flag.
        Returns None if no draft has been generated yet.
        Raises 404 if the resume does not exist or belongs to another user.
        """
        resume = StructuredResumeRepository(self._db).get_by_id(resume_id)
        if resume is None or resume.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

        row = CandidateProfileResumeDraftRepository(self._db).get_by_user_and_resume(
            user_id, resume_id
        )
        if row is None:
            return None

        current_hash = self._resume_hash(resume.resume_jsonb)
        return AutofillDraftResponse(
            resume_id=resume_id,
            draft=self._coerce_draft(row.draft_jsonb),
            status=row.status,
            resume_hash=row.resume_hash,
            is_stale=(row.resume_hash != current_hash),
            model=row.model,
            generated_at=row.generated_at,
        )

    def generate(self, user_id: str, resume_id: int) -> AutofillDraftResponse:
        """
        Generate (or regenerate) a profile draft from a resume using the LLM.
        Persists the result in candidate_profile_resume_drafts and returns it.
        Raises 404 if the resume is not found, 422 if it has no text.
        """
        resume = StructuredResumeRepository(self._db).get_by_id(resume_id)
        if resume is None or resume.user_id != user_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resume not found")

        raw_text = resume.resume_jsonb.get("raw_text", "")
        if not raw_text.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Resume has no extractable text. Re-upload the resume and try again.",
            )

        client = make_openai_client_from_settings()
        prompt = _load_autofill_prompt(raw_text)

        logger.info(
            "ProfileAutofillService.generate user_id=%s resume_id=%s text_len=%d prompt=%s",
            user_id, resume_id, len(raw_text), _AUTOFILL_PROMPT_NAME,
        )

        result = client.complete_json(
            [{"role": "user", "content": prompt}],
            json_schema=_PROFILE_SCHEMA,
            model="gpt-4o",
            temperature=0.2,
            max_tokens=4096,
        )

        draft_dict = json.loads(result.content)
        current_hash = self._resume_hash(resume.resume_jsonb)
        now = datetime.now(tz=timezone.utc)

        repo = CandidateProfileResumeDraftRepository(self._db)
        row = repo.upsert(
            user_id=user_id,
            resume_id=resume_id,
            draft_jsonb=draft_dict,
            resume_hash=current_hash,
            status="ready",
            model=result.model,
            generated_at=now,
        )
        self._db.commit()

        logger.info(
            "ProfileAutofillService.generate done draft_id=%s model=%s",
            row.id, result.model,
        )

        return AutofillDraftResponse(
            resume_id=resume_id,
            draft=self._coerce_draft(draft_dict),
            status="ready",
            resume_hash=current_hash,
            is_stale=False,
            model=result.model,
            generated_at=row.generated_at,
        )
