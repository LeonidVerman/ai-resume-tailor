"""
backend/app/services/benchmark_service.py

Web benchmark service — runs the assess pipeline against a fixed set of
positions using the current admin-configured generation mode and models,
and the dynamic candidate profile from the DB.

Key differences from CLI benchmark:
  - Uses dynamic candidate prompt from candidate_profiles (not prompts/candidate.txt)
  - Reads generation mode/models from admin_config (not CLI flags)
  - Runs as a FastAPI background task
  - Persists progress and results to DB
  - Writes reports to reports/benchmark/<timestamp>/ instead of reports/<timestamp>/
  - Thread count = min(positions_count, MAX_WORKERS_CAP)
"""

from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Max parallel workers (same cap as CLI benchmark)
_MAX_WORKERS_CAP = 20

# Score keys matching assess.py _SCORE_KEYS
_SCORE_KEYS: list[str] = [
    "truthfulness",
    "role_fit",
    "seniority_positioning",
    "clarity_impact",
    "mechanism_quality",
    "constraint_compliance",
    "cover_letter_effectiveness",
    "overall_readiness",
]

# Mapping from assess.py score key → BenchmarkRun/Position column name
_SCORE_KEY_TO_COL: dict[str, str] = {
    "truthfulness": "truthfulness_score",
    "role_fit": "role_fit_score",
    "seniority_positioning": "seniority_positioning_score",
    "clarity_impact": "clarity_impact_score",
    "mechanism_quality": "mechanism_quality_score",
    "constraint_compliance": "constraint_compliance_score",
    "cover_letter_effectiveness": "cover_letter_effectiveness_score",
    "overall_readiness": "overall_readiness_score",
}


# ---------------------------------------------------------------------------
# Web-aware tailoring (mirrors _tailor_position from assess.py but uses
# Web candidate profile and the tailor.core_generation.llm module so that
# _override_models() in generation_service.py patches the right globals)
# ---------------------------------------------------------------------------

def _web_tailor_position(
    job: Any,
    resume_template: str,
    cover_template: str,
    candidate_profile_text: str | None,
    candidate_layer: str | None,
    simple: bool = False,
) -> tuple[Any, dict | None, dict | None, dict | None]:
    """Run Phase 1 → Phase 2 for one position using the Web generation stack.

    Returns (TailorResult, plan, phase2_debug, phase1_debug).
    On any Phase 1 failure, falls back to single-pass.
    """
    from tailor.core_generation.llm import (
        PlanValidationError,
        _run_schema_gate,
        plan_tailoring,
        tailor_documents,
        tailor_documents_with_plan,
        validate_plan,
    )
    from tailor.plan_validator import validate_plan_extended

    if simple:
        result, _ = tailor_documents(
            job, resume_template, cover_template,
            candidate_profile=candidate_profile_text,
            candidate_layer=candidate_layer,
        )
        return result, None, None, None

    try:
        raw_plan, p1_messages, p1_meta = plan_tailoring(
            job, resume_template, cover_template,
            candidate_profile=candidate_profile_text,
        )
    except Exception as exc:
        logger.warning("Phase 1 failed for %s: %s", job.source_url, exc)
        result, _ = tailor_documents(
            job, resume_template, cover_template,
            candidate_profile=candidate_profile_text,
            candidate_layer=candidate_layer,
        )
        return result, None, None, None

    gate_errors = _run_schema_gate(raw_plan)
    if gate_errors:
        logger.warning("Phase 1 schema invalid for %s: %s", job.source_url, gate_errors[0])
        result, _ = tailor_documents(
            job, resume_template, cover_template,
            candidate_profile=candidate_profile_text,
            candidate_layer=candidate_layer,
        )
        return result, None, None, None

    try:
        validate_plan(raw_plan)
    except PlanValidationError as exc:
        logger.warning("Phase 1 validation failed for %s: %s", job.source_url, exc)
        result, _ = tailor_documents(
            job, resume_template, cover_template,
            candidate_profile=candidate_profile_text,
            candidate_layer=candidate_layer,
        )
        return result, None, None, None

    extended_errors = validate_plan_extended(raw_plan)
    if extended_errors:
        logger.info(
            "Phase 1 extended validation warnings (%d) for %s",
            len(extended_errors), job.source_url,
        )

    plan = raw_plan
    result, _, phase2_debug = tailor_documents_with_plan(
        plan, job, resume_template, cover_template,
        candidate_profile=candidate_profile_text,
        candidate_layer=candidate_layer,
    )

    phase1_debug = {
        "llm_request": p1_messages,
        "llm_response_raw": p1_meta.get("raw_response") if p1_meta else None,
        "plan_json": plan,
        "model": p1_meta.get("model") if p1_meta else None,
        "usage": p1_meta.get("usage") if p1_meta else None,
        "schema_errors": [],
        "validation_errors": [],
        "prompt_used": p1_meta.get("prompt_used") if p1_meta else None,
    }

    return result, plan, phase2_debug, phase1_debug


def _web_process_one_position(
    url: str,
    idx: int,
    total: int,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    candidate_layer: str | None,
    assess_model: str,
    assess_temperature: float,
    raw_dir: Path,
    weights: dict[str, float],
    print_lock: threading.Lock,
    simple: bool,
    progress_callback,
) -> dict | None:
    """Scrape → tailor (Web profile) → assess one position URL.

    Returns an entry dict compatible with assess.py report format, or None on failure.
    Calls progress_callback() after each position completes (success or skip).
    """
    from tailor.assess import (
        _position_cache_key,
        _save_raw,
        build_assessment_input,
        compute_integrated_score,
        _call_assess_llm,
        validate_assessment_response,
    )
    from tailor.job.scrape import scrape_job_url

    def _print(*args: Any) -> None:
        with print_lock:
            logger.info(*args)

    _print("[%d/%d] %s", idx, total, url)

    try:
        job = scrape_job_url(url)
    except Exception as exc:
        _print("[%d] Skipping — scrape failed: %s", idx, exc)
        progress_callback()
        return None

    _print("[%d] Company: %s | Role: %s", idx, job.company, job.job_title)

    try:
        result, plan, phase2_debug, phase1_debug = _web_tailor_position(
            job, resume_template, cover_template,
            candidate_profile_text=profile_str,
            candidate_layer=candidate_layer,
            simple=simple,
        )
    except Exception as exc:
        _print("[%d] Skipping — tailoring failed: %s", idx, exc)
        progress_callback()
        return None

    cache_key = _position_cache_key(
        url,
        result.resume or "",
        result.cover_letter or "",
        assess_model,
    )

    assessment_input = build_assessment_input(
        job, result, resume_template, cover_template, profile_str, phase2_debug, plan
    )
    try:
        raw_assessment = _call_assess_llm(assessment_input, assess_model, assess_temperature)
    except Exception as exc:
        _print("[%d] Skipping — assess LLM failed: %s", idx, exc)
        progress_callback()
        return None

    raw_path = _save_raw(raw_dir, cache_key, raw_assessment)

    raw_scores: dict = raw_assessment.get("scores", {})
    scores_flat: dict[str, int] = {
        k: raw_scores[k]["score"]
        for k in _SCORE_KEYS
        if isinstance(raw_scores.get(k), dict) and isinstance(raw_scores[k].get("score"), int)
    }
    integrated = compute_integrated_score(scores_flat, weights)

    _print("[%d] %s / %s — integrated: %.2f", idx, job.company, job.job_title, integrated)

    progress_callback()

    return {
        "position_url": url,
        "company": job.company,
        "role_title": job.job_title,
        "scores": scores_flat,
        "overall_readiness": scores_flat.get("overall_readiness"),
        "integrated_score": integrated,
        "raw_assessment_path": raw_path,
        "flags": raw_assessment.get("flags", {}),
        "notes": raw_assessment.get("notes", {}),
        "assessment_json": raw_assessment,
    }


# ---------------------------------------------------------------------------
# Background execution
# ---------------------------------------------------------------------------

def _run_benchmark_background(
    benchmark_run_id: str,
    database_url: str,
    report_base_dir: str,
    positions_file: str,
    assess_model: str | None = None,
) -> None:
    """Entry point for the FastAPI background task.

    Creates its own DB session since the request session is already closed.
    """
    from backend.app.db.session import get_session_factory
    factory = get_session_factory(database_url)
    db = factory()
    try:
        _execute_benchmark(benchmark_run_id, db, report_base_dir, positions_file, assess_model=assess_model)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        raise
    finally:
        db.close()


def _execute_benchmark(
    benchmark_run_id: str,
    db: Any,
    report_base_dir: str,
    positions_file: str,
    assess_model: str | None = None,
) -> None:
    """Main benchmark execution logic — runs in the background task."""
    from backend.app.db.repositories.benchmark_run_repository import BenchmarkRunRepository
    from backend.app.db.repositories.benchmark_run_position_repository import BenchmarkRunPositionRepository
    from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
    from backend.app.services.generation_service import (
        _build_candidate_profile_text,
        _ensure_candidate_prompt,
    )
    from backend.app.services.generation_service import _override_models
    from tailor.assess import (
        load_positions,
        _aggregate,
        _save_csv,
        _DEFAULT_WEIGHTS,
        _SCORE_KEYS as _ASSESS_SCORE_KEYS,
    )
    from tailor.config import ASSESS_MODEL, ASSESS_TEMPERATURE
    from tailor.docx.template_fill import read_docx
    from tailor.config import RESUME_TEMPLATE, COVER_TEMPLATE

    repo = BenchmarkRunRepository(db)
    pos_repo = BenchmarkRunPositionRepository(db)

    run = repo.get_by_id(benchmark_run_id)
    if run is None:
        logger.error("Benchmark run %s not found", benchmark_run_id)
        return

    # Use assess_model from parameter (passed at enqueue time from the run record)
    effective_assess_model = assess_model or run.assess_model or ASSESS_MODEL

    # Mark as running
    repo.update(run, status="running", started_at=datetime.now(tz=timezone.utc))
    db.commit()

    try:
        # Load positions
        urls = load_positions(positions_file)
        if not urls:
            raise ValueError(f"Positions file is empty: {positions_file}")

        repo.update(run, positions_count=len(urls))
        db.commit()

        # Set up report directory
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = Path(report_base_dir) / ts
        raw_dir = run_dir / "raw"
        report_path = run_dir / f"assess_{ts}.json"
        csv_path = run_dir / f"assess_{ts}.csv"
        run_dir.mkdir(parents=True, exist_ok=True)

        # Determine mode/models from the stored run config
        admin_mode = run.generation_mode
        simple = (admin_mode != "two_phase")
        simple_model = run.simple_model
        phase1_model = run.phase1_model
        phase2_model = run.phase2_model

        # Load shared resources
        resume_template = read_docx(str(RESUME_TEMPLATE))
        cover_template = read_docx(str(COVER_TEMPLATE))

        # Build dynamic candidate profile from DB
        profile_repo = CandidateProfileRepository(db)
        profile = profile_repo.get_by_user_id(run.client_id)
        candidate_profile_text = _build_candidate_profile_text(profile)
        # Use phase1_model for meta-prompt (matches generation_service.py convention)
        meta_model = phase1_model if admin_mode == "two_phase" else simple_model
        candidate_layer = _ensure_candidate_prompt(
            profile=profile,
            model=meta_model or ASSESS_MODEL,
            profile_repo=profile_repo,
        )
        db.commit()

        # Commit any candidate prompt updates before starting threaded work
        # Use the profile_str for the assessment input (JSON for assess LLM)
        profile_str = candidate_profile_text or ""

        # Thread count: min(positions_count, MAX_WORKERS_CAP)
        effective_workers = min(len(urls), _MAX_WORKERS_CAP)

        weights = _DEFAULT_WEIGHTS
        print_lock = threading.Lock()

        # Progress counter (thread-safe)
        progress_lock = threading.Lock()
        _completed_count = [0]

        def _progress_callback():
            with progress_lock:
                _completed_count[0] += 1
                count = _completed_count[0]
            # Each commit in background is its own transaction
            _update_progress(repo, db, run, count)

        # Run benchmark with model overrides
        with _override_models(
            simple_model=simple_model,
            phase1_model=phase1_model,
            phase2_model=phase2_model,
        ):
            with ThreadPoolExecutor(max_workers=effective_workers) as executor:
                futures = [
                    executor.submit(
                        _web_process_one_position,
                        url, i, len(urls),
                        resume_template, cover_template,
                        profile_str, candidate_layer,
                        effective_assess_model, ASSESS_TEMPERATURE,
                        raw_dir, weights, print_lock, simple,
                        _progress_callback,
                    )
                    for i, url in enumerate(urls, 1)
                ]
                position_entries: list[dict] = [
                    entry
                    for future in futures
                    if (entry := future.result()) is not None
                ]

        # Aggregate results
        agg = _aggregate(position_entries, weights)

        report: dict = {
            "version": "assess_report_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model": effective_assess_model,
            "calibrate": False,
            "calibrate_data": False,
            "positions_count": len(position_entries),
            "positions": position_entries,
            "category_averages": agg["category_averages"],
            "integrated_score": agg["integrated_score"],
            "weights": weights,
        }

        # Write JSON report
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        # Write CSV report
        _save_csv(csv_path, position_entries)

        # Parse scores into DB columns
        category_avgs = agg["category_averages"]
        score_kwargs: dict[str, Any] = {
            _SCORE_KEY_TO_COL[k]: category_avgs.get(k)
            for k in _SCORE_KEY_TO_COL
            if category_avgs.get(k) is not None
        }

        # Save position rows
        for entry in position_entries:
            scores = entry.get("scores", {})
            pos_kwargs: dict[str, Any] = {
                "benchmark_run_id": run.id,
                "position_url": entry["position_url"],
                "company": entry.get("company"),
                "role_title": entry.get("role_title"),
                "integrated_score": entry.get("integrated_score"),
                "raw_assessment_path": entry.get("raw_assessment_path"),
                "assessment_json": entry.get("assessment_json"),
            }
            for k in _SCORE_KEY_TO_COL:
                pos_kwargs[_SCORE_KEY_TO_COL[k]] = scores.get(k)
            pos_repo.create(**pos_kwargs)

        # Finalize the run record
        repo.update(
            run,
            status="completed",
            completed_at=datetime.now(tz=timezone.utc),
            completed_positions=len(position_entries),
            positions_count=len(urls),
            report_dir=str(run_dir),
            report_json_path=str(report_path),
            report_csv_path=str(csv_path),
            report_json=report,
            weights_json=weights,
            integrated_score=agg["integrated_score"],
            **score_kwargs,
        )
        db.commit()
        logger.info(
            "Benchmark run %s completed: %d/%d positions, integrated=%.2f",
            run.id, len(position_entries), len(urls), agg["integrated_score"],
        )

    except Exception as exc:
        logger.error("Benchmark run %s failed: %s", benchmark_run_id, exc, exc_info=True)
        db.rollback()
        try:
            run = repo.get_by_id(benchmark_run_id)
            if run:
                repo.update(
                    run,
                    status="failed",
                    error_message=str(exc)[:2000],
                    completed_at=datetime.now(tz=timezone.utc),
                )
                db.commit()
        except Exception:
            logger.error("Failed to update benchmark run status to failed", exc_info=True)


def _update_progress(repo: Any, db: Any, run: Any, completed: int) -> None:
    """Persist progress count to DB — best effort, never raises."""
    try:
        repo.update(run, completed_positions=completed)
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public service API (called from admin.py route handler)
# ---------------------------------------------------------------------------

class BenchmarkService:
    """Orchestrate Web benchmark runs: validate, start, and query."""

    def start(
        self,
        client_id: str,
        db: Any,
        background_tasks: Any,
        database_url: str,
        report_base_dir: str,
        positions_file: str,
        assess_model: str | None = None,
    ) -> Any:
        """Validate inputs, create a benchmark_run record, and enqueue the job.

        Returns the BenchmarkRun ORM record (status=queued).
        Raises HTTPException on validation failures.
        """
        from fastapi import HTTPException, status as http_status
        from backend.app.db.repositories.benchmark_run_repository import BenchmarkRunRepository
        from backend.app.db.repositories.user_repository import UserRepository
        from backend.app.db.repositories.admin_config_repository import AdminConfigRepository

        # Validate client exists
        user = UserRepository(db).get_by_id(client_id)
        if user is None:
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"Client (user) not found: {client_id!r}",
            )

        repo = BenchmarkRunRepository(db)

        # Enforce single active run
        active = repo.get_active()
        if active is not None:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail=(
                    f"A benchmark run is already active "
                    f"(id={active.id!r}, status={active.status!r})."
                ),
            )

        # Validate positions file
        pos_path = Path(positions_file)
        if not pos_path.exists():
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Positions file not found: {positions_file}",
            )
        from tailor.assess import load_positions
        urls = load_positions(positions_file)
        if not urls:
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Positions file is empty: {positions_file}",
            )

        # Read current admin generation config
        cfg = AdminConfigRepository(db).get()

        # Create benchmark run record
        run = repo.create(
            client_id=client_id,
            status="queued",
            positions_count=len(urls),
            completed_positions=0,
            generation_mode=cfg.generation_mode,
            simple_model=cfg.simple_model if cfg.generation_mode == "simple" else None,
            phase1_model=cfg.phase1_model if cfg.generation_mode == "two_phase" else None,
            phase2_model=cfg.phase2_model if cfg.generation_mode == "two_phase" else None,
            assess_model=assess_model,
        )
        db.commit()

        # Dispatch background job
        background_tasks.add_task(
            _run_benchmark_background,
            run.id,
            database_url,
            report_base_dir,
            positions_file,
            assess_model,
        )

        logger.info(
            "Benchmark run %s queued for client=%s mode=%s positions=%d",
            run.id, client_id, cfg.generation_mode, len(urls),
        )
        return run
