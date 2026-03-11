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
from backend.app.db.repositories.admin_config_repository import AdminConfigRepository
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
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
    profile_repo: CandidateProfileRepository
    """

    def __init__(
        self,
        run_repo: GenerationRunRepository,
        doc_repo: TailoredDocumentRepository,
        jd_repo: JobDescriptionRepository,
        resume_repo: StructuredResumeRepository,
        profile_repo: CandidateProfileRepository,
    ) -> None:
        self._run_repo = run_repo
        self._doc_repo = doc_repo
        self._jd_repo = jd_repo
        self._resume_repo = resume_repo
        self._profile_repo = profile_repo

    def generate(
        self, user_id: str, request: GenerationRequest
    ) -> GenerationResponse:
        """
        Run the full tailoring pipeline and persist the results.

        Returns a GenerationResponse with the run_id and tailored_document_id.
        """
        # ── Load admin config ──────────────────────────────────────────────
        cfg = AdminConfigRepository(self._run_repo._db).get()  # shares same session
        admin_mode = cfg.generation_mode  # "simple" | "two_phase"
        simple_model = cfg.simple_model
        phase1_model = cfg.phase1_model
        phase2_model = cfg.phase2_model

        # Map admin mode to internal run_type
        run_type = "two_phase" if admin_mode == "two_phase" else "single_pass"
        model_name = phase2_model if admin_mode == "two_phase" else simple_model

        # ── Load inputs ────────────────────────────────────────────────────
        jd = self._jd_repo.get_by_id(request.job_description_id)
        if jd is None or jd.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")

        resume = self._resume_repo.get_by_id(request.structured_resume_id)
        if resume is None or resume.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Structured resume not found")

        # ── Load candidate profile from DB ─────────────────────────────────
        candidate_profile_text = _build_candidate_profile_text(
            self._profile_repo.get_by_user_id(user_id)
        )

        # ── Create run record ──────────────────────────────────────────────
        run = self._run_repo.create(
            user_id=user_id,
            job_description_id=jd.id,
            run_type=run_type,
            status="running",
            model_name=model_name,
            prompt_version=_PROMPT_VERSION,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info(
            "Started generation run=%s user=%s mode=%s", run.id, user_id, admin_mode
        )

        meta = jd.metadata_jsonb or {}
        try:
            result, token_input, token_output, cost, debug_meta = self._run_pipeline(
                run_type=run_type,
                jd_text=jd.raw_text,
                jd_company=meta.get("company", ""),
                jd_job_title=meta.get("job_title", ""),
                resume_raw_text=resume.resume_jsonb.get("raw_text", "") if resume.resume_jsonb else "",
                candidate_profile_text=candidate_profile_text,
                simple_model=simple_model,
                phase1_model=phase1_model,
                phase2_model=phase2_model,
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

        # ── Save run data JSON (mirrors CLI debug output) ──────────────────
        _save_run_data(
            run_id=run.id,
            company=meta.get("company", ""),
            job_title=meta.get("job_title", ""),
            result=result,
            debug_meta=debug_meta,
        )

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
        candidate_profile_text: str | None = None,
        simple_model: str | None = None,
        phase1_model: str | None = None,
        phase2_model: str | None = None,
    ):
        """
        Call the existing generator pipeline.

        Returns (TailorResult, token_input, token_output, cost_estimate, debug_meta).
        """
        from tailor.job import JobData
        from tailor.docx.template_fill import read_docx
        from tailor.config import RESUME_TEMPLATE, COVER_TEMPLATE, ENABLE_TWO_PHASE

        resume_template = read_docx(str(RESUME_TEMPLATE))
        cover_template = read_docx(str(COVER_TEMPLATE))

        job = JobData(
            company=jd_company,
            job_title=jd_job_title,
            description=jd_text,
        )

        with _override_models(
            simple_model=simple_model,
            phase1_model=phase1_model,
            phase2_model=phase2_model,
        ):
            if run_type == "two_phase" and ENABLE_TWO_PHASE:
                result, messages, debug_meta = self._run_two_phase(
                    job, resume_template, cover_template,
                    candidate_profile=candidate_profile_text,
                )
            else:
                from tailor.core_generation.llm import tailor_documents
                result, llm_req = tailor_documents(
                    job, resume_template, cover_template,
                    candidate_profile=candidate_profile_text,
                )
                result, messages, debug_meta = result, [llm_req], {"llm_request": llm_req}

        # Compute resume diff and store in debug_meta (mirrors CLI behaviour).
        from tailor.diff import diff_resume
        sections = diff_resume(resume_template, result.resume) if result.resume else {}
        debug_meta["diff"] = {"resume": sections} if sections else None

        # Extract token usage from messages (OpenAI usage fields)
        token_input, token_output, cost = _extract_usage_from_messages(messages)
        return result, token_input, token_output, cost, debug_meta

    def _run_two_phase(self, job, resume_template, cover_template, candidate_profile=None):
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
        p1_meta: dict = {}
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
                        job, resume_template, cover_template,
                        candidate_profile=candidate_profile,
                    )
                else:
                    can_repair = bool(prev_raw_plan)
                    if can_repair:
                        raw_plan, p1_messages, p1_meta = plan_repair_tailoring(
                            prev_raw_plan,
                            schema_errors + validation_errors,
                            job, resume_template, cover_template,
                            candidate_profile=candidate_profile,
                        )
                    else:
                        raw_plan, p1_messages, p1_meta = plan_tailoring(
                            job, resume_template, cover_template,
                            candidate_profile=candidate_profile,
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

        # Build phase1_debug in the same structure the CLI produces.
        phase1_debug = {
            "llm_request": p1_messages,
            "llm_response_raw": p1_meta.get("raw_response") if p1_meta else None,
            "plan_json": plan,
            "model": p1_meta.get("model") if p1_meta else None,
            "usage": p1_meta.get("usage") if p1_meta else None,
            "schema_errors": schema_errors,
            "validation_errors": validation_errors,
            "prompt_used": p1_meta.get("prompt_used") if p1_meta else None,
        }

        if plan is None:
            logger.warning("Phase 1 failed after 2 attempts; falling back to single-pass")
            result, llm_req = tailor_documents(
                job, resume_template, cover_template,
                candidate_profile=candidate_profile,
            )
            return result, [llm_req], {"llm_request": llm_req, "phase1": phase1_debug}

        result, p2_messages, p2_meta = tailor_documents_with_plan(
            plan, job, resume_template, cover_template,
            candidate_profile=candidate_profile,
        )
        all_messages = [p1_messages, p2_messages]
        return result, all_messages, {
            "llm_request": p2_messages,
            "phase1": phase1_debug,
            "phase2": p2_meta,
        }


import contextlib


@contextlib.contextmanager
def _override_models(
    simple_model: str | None,
    phase1_model: str | None,
    phase2_model: str | None,
):
    """
    Temporarily patch tailor.core_generation.llm module globals so that
    pipeline calls use admin-configured model names.

    Restores originals on exit even if an exception is raised.
    This is safe for the synchronous request model (one request per worker).
    """
    import tailor.core_generation.llm as _llm

    orig = {
        "SIMPLE_MODEL": _llm.SIMPLE_MODEL,
        "PHASE1_MODEL": _llm.PHASE1_MODEL,
        "PHASE2_MODEL": _llm.PHASE2_MODEL,
    }
    if simple_model:
        _llm.SIMPLE_MODEL = simple_model
    if phase1_model:
        _llm.PHASE1_MODEL = phase1_model
    if phase2_model:
        _llm.PHASE2_MODEL = phase2_model
    try:
        yield
    finally:
        _llm.SIMPLE_MODEL = orig["SIMPLE_MODEL"]
        _llm.PHASE1_MODEL = orig["PHASE1_MODEL"]
        _llm.PHASE2_MODEL = orig["PHASE2_MODEL"]


def _build_candidate_profile_text(profile) -> str | None:
    """
    Serialize a CandidateProfile DB record to a JSON string for injection
    into the LLM prompt, matching the structure of profile/candidate_profile.json.

    Returns None when no profile exists (pipeline will fall back to the CLI file).
    """
    if profile is None or not profile.profile_jsonb:
        return None
    import json
    return json.dumps(profile.profile_jsonb, ensure_ascii=False, indent=2)


def _save_run_data(
    run_id,
    company: str,
    job_title: str,
    result,
    debug_meta: dict,
) -> None:
    """
    Persist a generation run debug JSON using the same mechanism as the CLI.

    No-ops silently when RUN_DATA_DIR is not configured or on any error so
    that a logging failure never breaks the generation response.
    """
    from backend.app.config import get_settings
    settings = get_settings()
    if not settings.run_data_dir:
        return
    try:
        from tailor.debug import save_debug_data
        save_debug_data(
            company=company,
            job_title=job_title,
            llm_response={"resume": result.resume, "cover_letter": result.cover_letter},
            llm_request=debug_meta.get("llm_request"),
            diff=debug_meta.get("diff"),
            phase1=debug_meta.get("phase1"),
            phase2=debug_meta.get("phase2"),
            output_dir=settings.run_data_dir,
            extra={"generation_run_id": str(run_id)},
        )
    except Exception:
        logger.warning("Failed to save run data for run=%s", run_id, exc_info=True)


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
