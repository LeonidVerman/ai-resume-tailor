"""
backend/app/services/generation_service.py

Generation service — orchestrates single-pass tailoring for the SaaS backend.

Flow
----
1. Load inputs from DB (structured resume, job description, candidate profile)
2. Create a GenerationRun record with status="running"
3. Call tailor_documents() with admin-configured model
4. Persist TailoredDocument record with text output
5. Update GenerationRun with status="succeeded" + token counts
6. On any error: update status="failed", re-raise

Rendering to DOCX/PDF and storage upload are handled by separate services
(rendering_service, storage_service) called from the API layer — not here.
"""

from __future__ import annotations

import contextlib
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
from backend.app.services.storage_service import StorageService

logger = logging.getLogger(__name__)

_PROMPT_VERSION = "v2.1"


class GenerationService:
    """
    Orchestrate document tailoring using the single-pass generator pipeline.

    Dependencies
    ------------
    run_repo:        GenerationRunRepository
    doc_repo:        TailoredDocumentRepository
    jd_repo:         JobDescriptionRepository
    resume_repo:     StructuredResumeRepository
    profile_repo:    CandidateProfileRepository
    storage_service: StorageService
    """

    def __init__(
        self,
        run_repo: GenerationRunRepository,
        doc_repo: TailoredDocumentRepository,
        jd_repo: JobDescriptionRepository,
        resume_repo: StructuredResumeRepository,
        profile_repo: CandidateProfileRepository,
        storage_service: StorageService,
    ) -> None:
        self._run_repo = run_repo
        self._doc_repo = doc_repo
        self._jd_repo = jd_repo
        self._resume_repo = resume_repo
        self._profile_repo = profile_repo
        self._storage_service = storage_service

    def generate(
        self, user_id: str, request: GenerationRequest
    ) -> GenerationResponse:
        """
        Run the tailoring pipeline and persist the results.

        Returns a GenerationResponse with the run_id and tailored_document_id.
        """
        # ── Load admin config ──────────────────────────────────────────────
        cfg = AdminConfigRepository(self._run_repo._db).get()
        simple_model = cfg.simple_model

        # ── Load inputs ────────────────────────────────────────────────────
        jd = self._jd_repo.get_by_id(request.job_description_id)
        if jd is None or jd.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job description not found")

        resume = self._resume_repo.get_by_id(request.structured_resume_id)
        if resume is None or resume.user_id != user_id:
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Structured resume not found")

        # ── Build candidate prompt ─────────────────────────────────────────
        profile = self._profile_repo.get_by_user_id(user_id)
        candidate_profile_text = _build_candidate_profile_text(profile)
        candidate_layer = _ensure_candidate_prompt(
            profile=profile,
            model=simple_model,
            profile_repo=self._profile_repo,
        )

        # ── Create run record ──────────────────────────────────────────────
        run = self._run_repo.create(
            user_id=user_id,
            job_description_id=jd.id,
            run_type="single_pass",
            status="running",
            model_name=simple_model,
            prompt_version=_PROMPT_VERSION,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info("Started generation run=%s user=%s", run.id, user_id)

        meta = jd.metadata_jsonb or {}
        try:
            result, token_input, token_output, cost, debug_meta = self._run_pipeline(
                jd_text=jd.raw_text,
                jd_company=meta.get("company", ""),
                jd_job_title=meta.get("job_title", ""),
                resume_raw_text=resume.resume_jsonb.get("raw_text", "") if resume.resume_jsonb else "",
                candidate_profile_text=candidate_profile_text,
                candidate_layer=candidate_layer,
                simple_model=simple_model,
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

        # ── Save run data JSON ─────────────────────────────────────────────
        _save_run_data(
            run_id=run.id,
            company=meta.get("company", ""),
            job_title=meta.get("job_title", ""),
            result=result,
            debug_meta=debug_meta,
        )

        # ── Persist tailored document ──────────────────────────────────────
        template_original_filename = (resume.resume_jsonb or {}).get("original_filename", "")
        tailored_doc = self._doc_repo.create(
            user_id=user_id,
            generation_run_id=run.id,
            company_name=meta.get("company", ""),
            role_title=meta.get("job_title", ""),
            resume_jsonb={
                "text": result.resume,
                "template_original_filename": template_original_filename,
            } if result.resume else None,
            cover_letter_jsonb={"text": result.cover_letter} if result.cover_letter else None,
        )

        # ── Render and upload artifacts ────────────────────────────────────
        self._render_and_upload(
            user_id=user_id,
            run_id=str(run.id),
            tailored_doc=tailored_doc,
            resume=resume,
            result=result,
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

    # ── Render & upload ────────────────────────────────────────────────────

    def _render_and_upload(self, user_id, run_id, tailored_doc, resume, result) -> None:
        """Render the 4 artifacts and upload them to storage."""
        from backend.app.services.rendering_service import RenderingService

        rendering_svc = RenderingService()

        template_bytes: bytes | None = None
        if resume.input_conversion_warning:
            logger.warning(
                "Resume id=%s was uploaded as PDF — LibreOffice-converted DOCX has no "
                "Word styles; using CLI default template for rendering.",
                resume.id,
            )
        elif resume.source_file_url:
            try:
                template_bytes = self._storage_service.get_bytes(resume.source_file_url)
                logger.info("Loaded resume template from storage key=%s (%d bytes)",
                            resume.source_file_url, len(template_bytes))
            except Exception as exc:
                logger.error(
                    "TEMPLATE FETCH FAILED — falling back to CLI default. "
                    "key=%s error=%s", resume.source_file_url, exc, exc_info=True,
                )
        else:
            logger.warning(
                "Resume id=%s has no source_file_url — template was uploaded before "
                "storage was enabled. Re-upload the resume to use the correct template.",
                resume.id,
            )

        url_updates: dict = {}

        if result.resume:
            try:
                resume_docx_bytes = rendering_svc.render_resume_docx(
                    result.resume, template_bytes=template_bytes
                )
                url_updates["resume_docx_url"] = self._storage_service.upload_resume_docx(
                    user_id, run_id, resume_docx_bytes
                )
            except Exception as exc:
                logger.warning("Failed to render/upload resume.docx run=%s: %s", run_id, exc)
                resume_docx_bytes = None
        else:
            resume_docx_bytes = None

        if resume_docx_bytes:
            try:
                resume_pdf_bytes = rendering_svc.render_resume_pdf(resume_docx_bytes)
                url_updates["resume_pdf_url"] = self._storage_service.upload_resume_pdf(
                    user_id, run_id, resume_pdf_bytes
                )
            except Exception as exc:
                logger.warning("Failed to render/upload resume.pdf run=%s: %s", run_id, exc)

        cover_letter_docx_bytes: bytes | None = None
        if result.cover_letter:
            try:
                cover_letter_docx_bytes = rendering_svc.render_cover_letter_docx(result.cover_letter)
                url_updates["cover_letter_docx_url"] = self._storage_service.upload_cover_letter_docx(
                    user_id, run_id, cover_letter_docx_bytes
                )
            except Exception as exc:
                logger.warning(
                    "Failed to render/upload cover_letter.docx run=%s: %s", run_id, exc
                )

        if cover_letter_docx_bytes:
            try:
                cover_letter_pdf_bytes = rendering_svc.render_cover_letter_pdf(cover_letter_docx_bytes)
                url_updates["cover_letter_pdf_url"] = self._storage_service.upload_cover_letter_pdf(
                    user_id, run_id, cover_letter_pdf_bytes
                )
            except Exception as exc:
                logger.warning(
                    "Failed to render/upload cover_letter.pdf run=%s: %s", run_id, exc
                )

        if url_updates:
            self._doc_repo.update(tailored_doc, **url_updates)
            logger.info("Artifact URLs updated for doc=%s: %s", tailored_doc.id, list(url_updates))

    # ── Pipeline ───────────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        jd_text: str,
        jd_company: str,
        jd_job_title: str,
        resume_raw_text: str,
        candidate_profile_text: str | None = None,
        candidate_layer: str | None = None,
        simple_model: str | None = None,
    ):
        """Call single-pass tailor_documents().

        Returns (TailorResult, token_input, token_output, cost_estimate, debug_meta).
        """
        from tailor.core_generation.llm import tailor_documents
        from tailor.job import JobData
        from tailor.docx.template_fill import read_docx
        from tailor.config import COVER_TEMPLATE
        from tailor.diff import diff_resume

        resume_template = resume_raw_text
        cover_template = read_docx(str(COVER_TEMPLATE))

        job = JobData(
            company=jd_company,
            job_title=jd_job_title,
            description=jd_text,
        )

        with _override_simple_model(simple_model):
            result, llm_req = tailor_documents(
                job, resume_template, cover_template,
                candidate_profile=candidate_profile_text,
                candidate_layer=candidate_layer,
            )

        sections = diff_resume(resume_template, result.resume) if result.resume else {}
        debug_meta = {
            "llm_request": llm_req,
            "diff": {"resume": sections} if sections else None,
        }

        token_input, token_output, cost = _extract_usage_from_messages([llm_req])
        return result, token_input, token_output, cost, debug_meta


@contextlib.contextmanager
def _override_simple_model(model: str | None):
    """Temporarily patch SIMPLE_MODEL in tailor.core_generation.llm."""
    if not model:
        yield
        return
    import tailor.core_generation.llm as _llm
    orig = _llm.SIMPLE_MODEL
    _llm.SIMPLE_MODEL = model
    try:
        yield
    finally:
        _llm.SIMPLE_MODEL = orig


def _ensure_candidate_prompt(profile, model: str, profile_repo) -> str | None:
    if profile is None or not profile.profile_jsonb:
        return None
    from backend.app.services.candidate_meta_service import ensure_candidate_prompt_synced
    return ensure_candidate_prompt_synced(profile, model, profile_repo)


def _build_candidate_profile_text(profile) -> str | None:
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
            output_dir=settings.run_data_dir,
            extra={"generation_run_id": str(run_id)},
        )
    except Exception:
        logger.warning("Failed to save run data for run=%s", run_id, exc_info=True)


def _extract_usage_from_messages(messages) -> tuple[int | None, int | None, float | None]:
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
