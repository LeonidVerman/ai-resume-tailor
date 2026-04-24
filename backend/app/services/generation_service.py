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
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.job_description_repository import JobDescriptionRepository
from backend.app.db.repositories.monthly_usage_repository import MonthlyUsageRepository
from backend.app.db.repositories.structured_resume_repository import StructuredResumeRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.generation import GenerationRequest, GenerationResponse
from backend.app.services.storage_service import StorageService
from backend.app.services.usage_policy_service import UsagePolicyService

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
        self, user_id: str, request: GenerationRequest, billing=None
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

        meta = jd.metadata_jsonb or {}

        # ── Create run record ──────────────────────────────────────────────
        generation_mode = (request.generation_mode or "conservative") if hasattr(request, "generation_mode") else "conservative"

        run = self._run_repo.create(
            user_id=user_id,
            job_description_id=jd.id,
            run_type="single_pass",
            status="running",
            model_name=simple_model,
            prompt_version=_PROMPT_VERSION,
            generation_mode=generation_mode,
            started_at=datetime.now(tz=timezone.utc),
        )
        logger.info(
            "Generation run started run_id=%s user_id=%s jd_id=%s resume_id=%s company=%s title=%s",
            run.id, user_id, jd.id, resume.id, meta.get("company", ""), meta.get("job_title", ""),
        )

        try:
            result, token_input, token_output, cost, debug_meta = self._run_pipeline(
                jd_text=jd.raw_text,
                jd_company=meta.get("company", ""),
                jd_job_title=meta.get("job_title", ""),
                resume_raw_text=resume.resume_jsonb.get("raw_text", "") if resume.resume_jsonb else "",
                candidate_profile_text=candidate_profile_text,
                candidate_layer=candidate_layer,
                simple_model=simple_model,
                generation_mode=generation_mode,
            )
        except Exception as exc:
            logger.error(
                "Generation run finished run_id=%s status=failed user_id=%s jd_id=%s "
                "resume_id=%s company=%s title=%s error=%s",
                run.id, user_id, jd.id, resume.id,
                meta.get("company", ""), meta.get("job_title", ""), exc,
            )
            self._run_repo.update(
                run,
                status="failed",
                error_message=str(exc)[:2000],
                completed_at=datetime.now(tz=timezone.utc),
            )
            raise

        try:
            # ── Persist tailored document ──────────────────────────────────
            # Strip null bytes (\u0000) which PostgreSQL rejects in text/JSONB fields.
            resume_text = result.resume.replace("\x00", "") if result.resume else None
            cover_letter_text = result.cover_letter.replace("\x00", "") if result.cover_letter else None

            template_original_filename = (resume.resume_jsonb or {}).get("original_filename", "")
            candidate_name = ""
            if profile and profile.profile_jsonb:
                candidate_name = (profile.profile_jsonb.get("candidate") or {}).get("name", "")
            if not candidate_name:
                candidate_name = (resume.resume_jsonb or {}).get("name", "")
            tailored_doc = self._doc_repo.create(
                user_id=user_id,
                generation_run_id=run.id,
                company_name=meta.get("company", ""),
                role_title=meta.get("job_title", ""),
                resume_jsonb={
                    "text": resume_text,
                    "template_original_filename": template_original_filename,
                    "candidate_name": candidate_name,
                    "structured_resume_id": resume.id,
                    "diff": (debug_meta.get("diff") or {}).get("resume") or [],
                } if resume_text else None,
                cover_letter_jsonb={"text": cover_letter_text, "candidate_name": candidate_name} if cover_letter_text else None,
            )

            # ── Render and upload artifacts ────────────────────────────────
            updated_ir_dict = self._render_and_upload(
                user_id=user_id,
                run_id=str(run.id),
                tailored_doc=tailored_doc,
                resume=resume,
                result=result,
            )

            # ── Save run data JSON (after rendering so updated IR is included) ──
            _save_run_data(
                run_id=run.id,
                user_id=user_id,
                company=meta.get("company", ""),
                job_title=meta.get("job_title", ""),
                result=result,
                debug_meta=debug_meta,
                storage_service=self._storage_service,
                updated_ir_dict=updated_ir_dict,
            )

            # ── Consume quota slot (only on full success) ──────────────────
            # check_quota() was called earlier at the API layer for a fast
            # pre-flight rejection; the actual counter increment happens here
            # so that pipeline failures (post-processing, DB errors, etc.)
            # do not consume a generation slot.
            UsagePolicyService(
                billing_repo=BillingRepository(self._run_repo._db),
                monthly_usage_repo=MonthlyUsageRepository(self._run_repo._db),
            ).consume(user_id, billing)

            # ── Finalize run record ────────────────────────────────────────
            self._run_repo.update(
                run,
                status="succeeded",
                token_input=token_input,
                token_output=token_output,
                cost_estimate=cost,
                completed_at=datetime.now(tz=timezone.utc),
            )
            logger.info(
                "Generation run finished run_id=%s status=succeeded user_id=%s jd_id=%s "
                "resume_id=%s company=%s title=%s tokens_in=%s tokens_out=%s",
                run.id, user_id, jd.id, resume.id,
                meta.get("company", ""), meta.get("job_title", ""),
                token_input, token_output,
            )
        except Exception as exc:
            logger.error(
                "Generation run finished run_id=%s status=failed (post-pipeline) user_id=%s "
                "jd_id=%s resume_id=%s company=%s title=%s error=%s",
                run.id, user_id, jd.id, resume.id,
                meta.get("company", ""), meta.get("job_title", ""), exc,
                exc_info=True,
            )
            with contextlib.suppress(Exception):
                self._run_repo.update(
                    run,
                    status="failed",
                    error_message=str(exc)[:2000],
                    completed_at=datetime.now(tz=timezone.utc),
                )
            raise

        return GenerationResponse(
            run_id=run.id,
            status="succeeded",
            tailored_document_id=tailored_doc.id,
        )

    # ── Render & upload ────────────────────────────────────────────────────

    def _render_and_upload(self, user_id, run_id, tailored_doc, resume, result) -> "dict | None":
        """Render the 4 artifacts and upload them to storage.

        Returns the updated IR dict (serialized ResumeDocument after apply_tailored)
        for inclusion in the debug JSON, or None if it could not be captured.
        """
        from backend.app.services.rendering_service import RenderingService

        rendering_svc = RenderingService()

        template_bytes: bytes | None = None
        template_ir_dict: dict | None = None

        # Load upload-time classification (the final, validated section).
        # Falls back to None gracefully so rendering always proceeds.
        classification_dict: dict | None = None
        if resume.classification_jsonb:
            classification_dict = resume.classification_jsonb.get("classification")
            if classification_dict:
                logger.info(
                    "Resume id=%s: classification loaded for rendering (status=%s).",
                    resume.id,
                    resume.classification_jsonb.get("status", "unknown"),
                )

        if resume.template_ir_jsonb:
            # PDF-sourced resume: use the stored ResumeDocument IR directly.
            template_ir_dict = resume.template_ir_jsonb
            logger.info("Resume id=%s: using stored PDF IR for rendering.", resume.id)
        elif resume.source_file_url:
            raw_bytes = self._storage_service.get_bytes(resume.source_file_url)
            if raw_bytes[:4] == b'%PDF':
                # PDF in storage but no stored IR (legacy upload without IR column).
                # Re-parse the PDF on the fly so we never fall back to the CLI template.
                logger.info(
                    "Resume id=%s: no stored IR — re-parsing PDF from storage key=%s.",
                    resume.id, resume.source_file_url,
                )
                try:
                    from tailor.compiler.pdf_parser import parse_pdf
                    template_ir_dict = parse_pdf(raw_bytes).to_dict()
                except Exception as exc:
                    raise RuntimeError(
                        f"Resume id={resume.id} is a PDF that could not be parsed "
                        f"for rendering ({exc}). Please re-upload the resume."
                    ) from exc
            else:
                # DOCX — use bytes directly as the template.
                template_bytes = raw_bytes
                logger.info("Loaded resume DOCX template from storage key=%s (%d bytes)",
                            resume.source_file_url, len(template_bytes))
        else:
            raise RuntimeError(
                f"Resume id={resume.id} has no stored template (source_file_url is not set). "
                "Re-upload the resume to restore the template file."
            )

        url_updates: dict = {}
        updated_ir_dict: dict | None = None

        if result.resume:
            try:
                resume_docx_bytes, updated_ir_dict = rendering_svc.render_resume_for_generation(
                    result.resume,
                    template_bytes=template_bytes,
                    template_ir_dict=template_ir_dict,
                    classification_dict=classification_dict,
                )
                url_updates["resume_docx_url"] = self._storage_service.upload_resume_docx(
                    user_id, run_id, resume_docx_bytes
                )
            except Exception as exc:
                logger.warning("Failed to render/upload resume.docx run=%s: %s", run_id, exc, exc_info=True)
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

        return updated_ir_dict

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
        generation_mode: str | None = None,
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
        cover_template = _build_cover_template(read_docx(str(COVER_TEMPLATE)), candidate_profile_text)

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
                generation_mode=generation_mode,
            )

        sections = diff_resume(resume_template, result.resume) if result.resume else {}
        debug_meta = {
            "llm_request": llm_req,
            "diff": {"resume": sections} if sections else None,
        }

        token_input, token_output, cost = _extract_usage_from_messages([llm_req])
        return result, token_input, token_output, cost, debug_meta


def _build_cover_template(raw_template: str, candidate_profile_text: str | None) -> str:
    """Replace the personal header in the cover letter template with the candidate's name.

    Leonid's cover letter template starts with his name and contact block.  For
    web users this causes the LLM to adopt Leonid's identity when the MASTER_RESUME
    name is garbled (e.g. PDF encoding artefacts).  We strip everything before
    "Dear " and prepend only the candidate's name so the LLM has the correct
    structural template without any alien contact info.
    """
    # Extract candidate name from profile JSON if available.
    candidate_name = ""
    if candidate_profile_text:
        import json as _json
        try:
            profile_data = _json.loads(candidate_profile_text)
            candidate_name = (profile_data.get("candidate") or {}).get("name", "")
        except Exception:
            pass

    # Find the start of "Dear " to isolate the body (strips personal header).
    dear_idx = raw_template.find("\nDear ")
    if dear_idx == -1:
        dear_idx = raw_template.find("Dear ")
    body = raw_template[dear_idx:].lstrip("\n") if dear_idx >= 0 else raw_template

    header = f"{candidate_name}\n" if candidate_name else ""
    return header + body


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
    user_id: str,
    company: str,
    job_title: str,
    result,
    debug_meta: dict,
    storage_service: StorageService,
    updated_ir_dict: dict | None = None,
) -> None:
    """Persist the generation debug JSON to object storage (S3 / local)."""
    import json as _json
    try:
        data = {
            "company": company,
            "position": job_title,
            "llm_request": debug_meta.get("llm_request"),
            "llm_response": {"resume": result.resume, "cover_letter": result.cover_letter},
            "diff": debug_meta.get("diff"),
            "generation_run_id": str(run_id),
            "updated_ir": updated_ir_dict,
        }
        json_bytes = _json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8")
        storage_service.upload_debug_json(user_id, str(run_id), json_bytes)
    except Exception:
        logger.warning("Failed to upload debug JSON for run=%s", run_id, exc_info=True)


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
