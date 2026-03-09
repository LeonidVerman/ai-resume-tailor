"""
backend/app/services/generation_service.py

Generation service — orchestrates the full two-phase tailoring pipeline
for the SaaS backend.

This service wraps the existing generator's plan_tailoring(),
plan_repair_tailoring(), tailor_documents_with_plan(), and
tailor_documents() functions (in tailor.core_generation.llm), adding:
  - DB lifecycle (GenerationRun + TailoredDocument records)
  - Token / cost accounting
  - Error surfacing

The generator pipeline itself is NOT modified.

Flow
----
1. Load inputs from DB (structured resume, job description, candidate profile)
2. Create a GenerationRun record with status="running"
3. Call the existing two-phase pipeline
4. Persist TailoredDocument record with text output
5. Update GenerationRun with status="succeeded" + token counts
6. On any error: update status="failed", re-raise

Rendering to DOCX/PDF and storage upload are handled by separate services
(rendering_service, storage_service) called from the API layer — not here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.app.db.models.generation_run import GenerationRun
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.generation import GenerationRequest, GenerationResponse

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v2.1"   # reflects the current tailor_plan.txt version


class GenerationService:
    """
    Orchestrate document tailoring using the existing generator pipeline.

    Dependencies
    ------------
    run_repo:    GenerationRunRepository
    doc_repo:    TailoredDocumentRepository
    jd_repo:     JobDescriptionRepository
    resume_repo: StructuredResumeRepository
    """

    def __init__(
        self,
        run_repo: GenerationRunRepository,
        doc_repo: TailoredDocumentRepository,
        jd_repo: JobDescriptionRepository,
        resume_repo: StructuredResumeRepository,
    ) -> None:
        self._run_repo = run_repo
        self._doc_repo = doc_repo
        self._jd_repo = jd_repo
        self._resume_repo = resume_repo

    def generate(
        self, user_id: str, request: GenerationRequest
    ) -> GenerationResponse:
        """
        Run the full tailoring pipeline and persist the results.

        Returns a GenerationResponse with the run_id and tailored_document_id.
        """
        from tailor.config import PHASE2_MODEL, ENABLE_TWO_PHASE

        # ── Load inputs ────────────────────────────────────────────────────
        jd = self._jd_repo.get_by_id(request.job_description_id)
        if jd is None or jd.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")

        resume = self._resume_repo.get_by_id(request.structured_resume_id)
        if resume is None or resume.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Structured resume not found")

        run_type = request.options.mode  # "two_phase" | "single_pass"

        # ── Create run record ──────────────────────────────────────────────
        run = self._run_repo.create(
            user_id=user_id,
            job_description_id=jd.id,
            run_type=run_type,
            status="running",
            model_name=PHASE2_MODEL,
            prompt_version=_PROMPT_VERSION,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info("Started generation run=%s user=%s", run.id, user_id)

        meta = jd.metadata_jsonb or {}
        try:
            result, token_input, token_output, cost = self._run_pipeline(
                run_type=run_type,
                jd_text=jd.raw_text,
                jd_company=meta.get("company", ""),
                jd_job_title=meta.get("job_title", ""),
                resume_raw_text=resume.resume_jsonb.get("raw_text", "") if resume.resume_jsonb else "",
            )
        except Exception as exc:
            logger.error("Generation run=%s failed: %s", run.id, exc)
            self._run_repo.update(
                run,
                status="failed",
                error_message=str(exc)[:2000],
                completed_at=datetime.now(tz=timezone.utc),
            )
            raise

        # ── Persist tailored document ──────────────────────────────────────
        tailored_doc = self._doc_repo.create(
            user_id=user_id,
            generation_run_id=run.id,
            company_name=meta.get("company", ""),
            role_title=meta.get("job_title", ""),
            # Store plain text in JSONB so it can be retrieved for rendering/download.
            # Extended structured parsing is a future enhancement.
            resume_jsonb={"text": result.resume} if result.resume else None,
            cover_letter_jsonb={"text": result.cover_letter} if result.cover_letter else None,
        )

        # ── Finalize run record ────────────────────────────────────────────
        self._run_repo.update(
            run,
            status="succeeded",
            token_input=token_input,
            token_output=token_output,
            cost_estimate=cost,
            completed_at=datetime.now(tz=timezone.utc),
        )
        logger.info(
            "Completed generation run=%s doc=%s tokens_in=%s tokens_out=%s",
            run.id, tailored_doc.id, token_input, token_output,
        )

        return GenerationResponse(
            run_id=run.id,
            status="succeeded",
            tailored_document_id=tailored_doc.id,
        )

    # ── Pipeline ───────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        run_type: str,
        jd_text: str,
        jd_company: str,
        jd_job_title: str,
        resume_raw_text: str,
    ):
        """
        Call the existing generator pipeline.

        Returns (TailorResult, token_input, token_output, cost_estimate).
        """
        from tailor.job import JobData
        from tailor.docx.template_fill import read_docx
        from tailor.config import RESUME_TEMPLATE, COVER_TEMPLATE, ENABLE_TWO_PHASE, ENABLE_PLAN_REPAIR

        resume_template = read_docx(str(RESUME_TEMPLATE))
        cover_template = read_docx(str(COVER_TEMPLATE))

        job = JobData(
            company=jd_company,
            job_title=jd_job_title,
            description=jd_text,
        )

        if run_type == "two_phase" and ENABLE_TWO_PHASE:
            result, messages, debug_meta = self._run_two_phase(
                job, resume_template, cover_template
            )
        else:
            from tailor.core_generation.llm import tailor_documents
            result, llm_req = tailor_documents(job, resume_template, cover_template)
            result, messages, debug_meta = result, [llm_req], {}

        # Extract token usage from messages (OpenAI usage fields)
        token_input, token_output, cost = _extract_usage_from_messages(messages)
        return result, token_input, token_output, cost

    def _run_two_phase(self, job, resume_template, cover_template):
        """
        Run Phase 1 (plan) + Phase 2 (write) with optional repair.

        Mirrors the logic from cli._run_two_phase without interactive I/O.
        Returns (TailorResult, p1_messages + p2_messages, debug_meta).
        """
        from tailor.core_generation.llm import (
            PlanValidationError,
            _run_schema_gate,
            plan_tailoring,
            plan_repair_tailoring,
            tailor_documents,
            tailor_documents_with_plan,
            validate_plan,
        )
        from tailor.plan_validator import validate_plan_extended
        from tailor.config import ENABLE_PLAN_REPAIR

        plan = None
        p1_messages = []
        prev_raw_plan: dict = {}
        schema_errors: list[str] = []
        validation_errors: list[str] = []

        for attempt in range(1, 3):
            is_repair = attempt > 1
            if is_repair and not ENABLE_PLAN_REPAIR:
                break

            try:
                if not is_repair:
                    raw_plan, p1_messages, p1_meta = plan_tailoring(
                        job, resume_template, cover_template
                    )
                else:
                    can_repair = bool(prev_raw_plan)
                    if can_repair:
                        raw_plan, p1_messages, p1_meta = plan_repair_tailoring(
                            prev_raw_plan,
                            schema_errors + validation_errors,
                            job, resume_template, cover_template,
                        )
                    else:
                        raw_plan, p1_messages, p1_meta = plan_tailoring(
                            job, resume_template, cover_template
                        )
            except Exception as exc:
                logger.warning("Phase 1 attempt %d failed: %s", attempt, exc)
                continue

            # Validate
            gate_errors = _run_schema_gate(raw_plan)
            if gate_errors:
                schema_errors = gate_errors
                prev_raw_plan = raw_plan if isinstance(raw_plan, dict) else {}
                continue

            try:
                plan = validate_plan(raw_plan)
            except PlanValidationError as exc:
                validation_errors = exc.errors if hasattr(exc, "errors") else [str(exc)]
                prev_raw_plan = raw_plan
                continue

            ext_errors = validate_plan_extended(plan)
            if ext_errors:
                validation_errors = ext_errors
                prev_raw_plan = raw_plan
                continue

            # Phase 1 succeeded
            break

        if plan is None:
            logger.warning("Phase 1 failed after 2 attempts; falling back to single-pass")
            result, messages = tailor_documents(job, resume_template, cover_template)
            return result, [messages], {}

        result, p2_messages, p2_meta = tailor_documents_with_plan(
            plan, job, resume_template, cover_template
        )
        all_messages = [p1_messages, p2_messages]
        return result, all_messages, {"phase1": p1_meta, "phase2": p2_meta}


def _extract_usage_from_messages(messages) -> tuple[int | None, int | None, float | None]:
    """
    Best-effort extraction of token counts from OpenAI message objects.

    Returns (token_input, token_output, cost_estimate_usd).
    """
    token_input = token_output = None
    try:
        for msg in (messages or []):
            usage = getattr(msg, "usage", None)
            if usage:
                token_input = (token_input or 0) + getattr(usage, "prompt_tokens", 0)
                token_output = (token_output or 0) + getattr(usage, "completion_tokens", 0)
    except Exception:
        pass
    return token_input, token_output, None
