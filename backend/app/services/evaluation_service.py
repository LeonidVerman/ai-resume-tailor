"""
backend/app/services/evaluation_service.py

Evaluation service — scores a completed generation run for quality
dimensions using the existing generator's assess pipeline.

The existing generator's tailor.assess module runs five scoring rubrics
(truthfulness, role fit, clarity, seniority match, integrated score).
This service adapts that pipeline for the SaaS backend:

  1. Load the TailoredDocument for the given GenerationRun.
  2. Run the assessment pipeline from tailor.assess.
  3. Persist an EvaluationRun record with the scores.
  4. Return an EvaluationResponse.

Assessment is intentionally separate from generation so it can be
triggered asynchronously (admin batch, webhook, etc.).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from backend.app.db.repositories.evaluation_run_repository import EvaluationRunRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.evaluation import EvaluationResponse, EvaluationScores

logger = logging.getLogger(__name__)


class EvaluationService:
    """
    Score a completed generation run using the existing assess pipeline.

    Dependencies
    ------------
    eval_repo: EvaluationRunRepository
    run_repo:  GenerationRunRepository
    doc_repo:  TailoredDocumentRepository
    """

    def __init__(
        self,
        eval_repo: EvaluationRunRepository,
        run_repo: GenerationRunRepository,
        doc_repo: TailoredDocumentRepository,
    ) -> None:
        self._eval_repo = eval_repo
        self._run_repo = run_repo
        self._doc_repo = doc_repo

    def evaluate(self, generation_run_id: str) -> EvaluationResponse:
        """
        Score the tailored document for the given generation run.

        Creates or replaces the EvaluationRun record for this run.
        """
        run = self._run_repo.get_by_id(generation_run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Generation run not found",
            )
        if run.status != "succeeded":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Generation run status is '{run.status}'; only 'succeeded' runs can be evaluated",
            )

        doc = self._doc_repo.get_by_generation_run_id(generation_run_id)
        if doc is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Tailored document not found for this run",
            )

        resume_text = (doc.resume_jsonb or {}).get("text", "") if doc.resume_jsonb else ""
        cover_letter_text = (doc.cover_letter_jsonb or {}).get("text", "") if doc.cover_letter_jsonb else ""

        scores = self._run_assessment(
            resume_text=resume_text,
            cover_letter_text=cover_letter_text,
            jd_id=run.job_description_id,
        )

        existing = self._eval_repo.get_by_generation_run_id(generation_run_id)
        if existing:
            ev = self._eval_repo.update(existing, **scores)
        else:
            ev = self._eval_repo.create(
                generation_run_id=generation_run_id,
                created_at=datetime.now(tz=timezone.utc),
                **scores,
            )

        logger.info("Evaluation created/updated eval=%s run=%s", ev.id, generation_run_id)
        return self._to_response(ev)

    def get_by_run(self, generation_run_id: str) -> EvaluationResponse:
        ev = self._eval_repo.get_by_generation_run_id(generation_run_id)
        if ev is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No evaluation found for this run",
            )
        return self._to_response(ev)

    # ── Assessment ─────────────────────────────────────────────────────────

    def _run_assessment(
        self,
        resume_text: str,
        cover_letter_text: str,
        jd_id: str | None,
    ) -> dict:
        """
        Run the generator's assess pipeline and return a scores dict.

        Falls back to null scores if the assess module is unavailable
        or the run fails, so evaluation never blocks the user.
        """
        try:
            from tailor.assess import score_single
            result = score_single(resume=resume_text, cover_letter=cover_letter_text)
            return {
                "truthfulness_score": result.get("truthfulness"),
                "role_fit_score": result.get("role_fit"),
                "clarity_score": result.get("clarity"),
                "seniority_score": result.get("seniority"),
                "integrated_score": result.get("integrated"),
            }
        except (ImportError, AttributeError):
            logger.debug("tailor.assess.score_single not available; returning null scores")
        except Exception as exc:
            logger.warning("Assessment failed: %s", exc)

        return {
            "truthfulness_score": None,
            "role_fit_score": None,
            "clarity_score": None,
            "seniority_score": None,
            "integrated_score": None,
        }

    # ── Internal ───────────────────────────────────────────────────────────

    @staticmethod
    def _to_response(ev) -> EvaluationResponse:
        return EvaluationResponse(
            id=ev.id,
            generation_run_id=ev.generation_run_id,
            scores=EvaluationScores(
                truthfulness_score=float(ev.truthfulness_score) if ev.truthfulness_score is not None else None,
                role_fit_score=float(ev.role_fit_score) if ev.role_fit_score is not None else None,
                clarity_score=float(ev.clarity_score) if ev.clarity_score is not None else None,
                seniority_score=float(ev.seniority_score) if ev.seniority_score is not None else None,
                integrated_score=float(ev.integrated_score) if ev.integrated_score is not None else None,
            ),
            created_at=ev.created_at,
        )
