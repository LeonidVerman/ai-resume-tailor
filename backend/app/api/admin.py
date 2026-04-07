"""
backend/app/api/admin.py

Admin endpoints.

Endpoints
---------
POST /admin/evaluate-run              — score a completed generation run
GET  /admin/system-stats              — aggregate counts for all users
GET  /admin/generation-config         — retrieve persisted generation config
PUT  /admin/generation-config         — update generation mode / models
GET  /admin/logs/download             — download zipped log files for a date range
GET  /admin/run-data/download         — download zipped run-data JSONs for a date range
GET  /admin/run-data/download/{run_id} — download a single run-data JSON by generation run ID

Phase 8 status: FUNCTIONAL (admin-only via require_admin dependency)
---------------------------------------------------------------------
All endpoints require the authenticated user to have role='admin'.
"""

import io
import json
import re
import zipfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import Response

from backend.app.config import get_settings
from backend.app.dependencies import AdminDep, DbDep
from backend.app.db.repositories.billing_repository import BillingRepository
from backend.app.schemas.billing import GrantCreditsRequest
from backend.app.db.repositories.admin_config_repository import AdminConfigRepository
from backend.app.db.repositories.benchmark_run_repository import BenchmarkRunRepository
from backend.app.db.repositories.benchmark_run_position_repository import BenchmarkRunPositionRepository
from backend.app.db.repositories.candidate_profile_repository import CandidateProfileRepository
from backend.app.db.repositories.evaluation_run_repository import EvaluationRunRepository
from backend.app.db.repositories.generation_run_repository import GenerationRunRepository
from backend.app.db.repositories.tailored_document_repository import TailoredDocumentRepository
from backend.app.schemas.admin import (
    AVAILABLE_MODELS,
    AdminActionResponse,
    BenchmarkRunDetail,
    BenchmarkRunSummary,
    BenchmarkPositionSummary,
    BenchmarkStartRequest,
    GenerationConfigRequest,
    GenerationConfigResponse,
    SystemStats,
)
from backend.app.schemas.evaluation import EvaluationRequest, EvaluationResponse
from backend.app.services.benchmark_service import BenchmarkService
from backend.app.services.candidate_profile_normalizer import normalize_candidate_profile
from backend.app.services.evaluation_service import EvaluationService
from backend.app.services.stats_service import StatsService
from backend.app.services.storage_service import StorageService


def _storage_service() -> StorageService:
    from backend.app.clients.storage_client import make_storage_client_from_settings
    return StorageService(make_storage_client_from_settings())

# Path to the fixed benchmark positions file.
# benchmark/positions.txt is copied into the Docker image at /app/benchmark/positions.txt.
# parents[3] = repo root locally and /app in the container (backend/app/api/admin.py → 3 levels up).
_POSITIONS_FILE = str(Path(__file__).parents[3] / "benchmark" / "positions.txt")

router = APIRouter()


def _eval_service(db) -> EvaluationService:
    return EvaluationService(
        eval_repo=EvaluationRunRepository(db),
        run_repo=GenerationRunRepository(db),
        doc_repo=TailoredDocumentRepository(db),
    )


def _slugify(text: str) -> str:
    """Replace whitespace with underscores and strip non-alphanumeric/non-underscore chars."""
    text = re.sub(r"\s+", "_", text.strip())
    return re.sub(r"[^\w]", "", text) or "Unknown"


def _build_run_filename(run, db) -> str:
    """
    Build a rich download filename for a generation-run debug JSON.

    Format: FirstName_LastName-Company-Position-RunID-yyyyMMdd-HHmmss.json
    Example: Leonid_Verman-Boam_AI-Technical_Product_Lead-68-20260318-065752.json
    """
    # Candidate name from profile JSONB
    name = "Unknown"
    profile = CandidateProfileRepository(db).get_by_user_id(run.user_id)
    if profile:
        candidate = (profile.profile_jsonb or {}).get("candidate") or {}
        raw_name = (candidate.get("name") or "").strip()
        if raw_name:
            parts = raw_name.split()
            first = _slugify(parts[0])
            last = _slugify(parts[-1]) if len(parts) >= 2 else first
            name = f"{first}_{last}"

    # Company and role from first tailored document
    company = "Unknown"
    position = "Unknown"
    if run.tailored_documents:
        doc = run.tailored_documents[0]
        if doc.company_name:
            company = _slugify(doc.company_name)
        if doc.role_title:
            position = _slugify(doc.role_title)

    timestamp = run.started_at.strftime("%Y%m%d-%H%M%S")
    return f"{name}-{company}-{position}-{run.id}-{timestamp}.json"


@router.post("/evaluate-run", response_model=EvaluationResponse, status_code=201)
def evaluate_run(request: EvaluationRequest, _admin: AdminDep, db: DbDep):
    """
    Score a completed generation run using the assess pipeline.

    The generation run must have status='succeeded'. Creates or replaces
    the EvaluationRun record and returns the scores.
    Returns 404 if the run or its tailored document is not found.
    Returns 422 if the run has not yet succeeded.
    """
    return _eval_service(db).evaluate(request.generation_run_id)


@router.get("/system-stats", response_model=SystemStats)
def system_stats(_admin: AdminDep, db: DbDep):
    """Return aggregate counts across all users for admin dashboard."""
    return StatsService(db).get_system_stats()


@router.get("/generation-config", response_model=GenerationConfigResponse)
def get_generation_config(_admin: AdminDep, db: DbDep):
    """Return the persisted admin generation configuration."""
    cfg = AdminConfigRepository(db).get()
    return GenerationConfigResponse(
        simple_model=cfg.simple_model,
        available_models=AVAILABLE_MODELS,
    )


@router.put("/generation-config", response_model=AdminActionResponse)
def save_generation_config(
    request: GenerationConfigRequest, _admin: AdminDep, db: DbDep
):
    """Persist admin generation configuration."""
    AdminConfigRepository(db).upsert(simple_model=request.simple_model)
    return AdminActionResponse(ok=True, message="Generation configuration saved.")


@router.post("/candidate-profiles/backfill", response_model=AdminActionResponse)
def backfill_candidate_profiles(_admin: AdminDep, db: DbDep):
    """
    Re-normalise every stored candidate profile to v2.0 structure.

    Migrates v1.x fields (authz_authn_experience, scalability_reliability_patterns)
    into the v2.0 layout and runs token-to-readable normalisation on all list fields.
    Sets prompt_synched=False on any profile whose JSONB was changed.
    """
    repo = CandidateProfileRepository(db)
    profiles = repo.list_all()
    updated = 0
    for profile in profiles:
        if not profile.profile_jsonb:
            continue
        normalised = normalize_candidate_profile(profile.profile_jsonb)
        if normalised != profile.profile_jsonb:
            repo.update(profile, profile_jsonb=normalised, prompt_synched=False)
            updated += 1
    db.commit()
    return AdminActionResponse(
        ok=True,
        message=f"Backfill complete. {updated}/{len(profiles)} profiles updated.",
    )


# ── Benchmark runs ────────────────────────────────────────────────────────


@router.post("/benchmark-runs", response_model=BenchmarkRunSummary, status_code=201)
def start_benchmark_run(
    request: BenchmarkStartRequest,
    background_tasks: BackgroundTasks,
    _admin: AdminDep,
    db: DbDep,
):
    """
    Start a new benchmark run for the given client_id.

    Validates that the client exists, no benchmark is currently active, and
    the positions file is present.  Creates a benchmark_runs record with
    status=queued, then dispatches the benchmark as a background job.
    Returns 409 if a benchmark is already active.
    Returns 404 if client_id is not found.
    Returns 503 if the positions file is missing or empty.
    """
    settings = get_settings()
    run = BenchmarkService().start(
        client_id=request.client_id,
        assess_model=request.assess_model,
        generation_mode=request.generation_mode,
        db=db,
        background_tasks=background_tasks,
        database_url=settings.database_url,
        report_base_dir=settings.benchmark_report_dir,
        positions_file=_POSITIONS_FILE,
    )
    return _benchmark_run_to_summary(run)


@router.get("/benchmark-runs", response_model=list[BenchmarkRunSummary])
def list_benchmark_runs(
    _admin: AdminDep,
    db: DbDep,
    limit: int = Query(20, ge=1, le=100),
):
    """Return recent benchmark runs, newest first."""
    runs = BenchmarkRunRepository(db).list_recent(limit=limit)
    return [_benchmark_run_to_summary(r) for r in runs]


@router.get("/benchmark-runs/{run_id}/download")
def download_benchmark_zip(run_id: int, _admin: AdminDep, db: DbDep):
    """
    Download the benchmark report directory as a ZIP archive.

    Locates the report_dir from the benchmark_runs record and zips its
    entire contents (JSON report, CSV, raw/ subfolder).
    Returns 404 if the run or its report directory is not found.
    """
    run = BenchmarkRunRepository(db).get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    if not run.report_dir:
        raise HTTPException(
            status_code=404,
            detail="Report directory not available for this benchmark run",
        )
    report_dir = Path(run.report_dir)
    if not report_dir.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Report directory not found on disk: {run.report_dir}",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(report_dir.rglob("*")):
            if file_path.is_file():
                arcname = file_path.relative_to(report_dir.parent)
                zf.write(file_path, arcname)

    ts_label = report_dir.name
    filename = f"benchmark_{run_id}_{ts_label}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/benchmark-runs/{run_id}", response_model=BenchmarkRunDetail)
def get_benchmark_run(run_id: int, _admin: AdminDep, db: DbDep):
    """Return full benchmark run details including per-position scores."""
    run = BenchmarkRunRepository(db).get_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    positions = BenchmarkRunPositionRepository(db).get_by_benchmark_run_id(run_id)
    return _benchmark_run_to_detail(run, positions)


# ── Benchmark schema helpers ───────────────────────────────────────────────


def _f(v) -> float | None:
    return float(v) if v is not None else None


def _benchmark_run_to_summary(run) -> BenchmarkRunSummary:
    return BenchmarkRunSummary(
        id=run.id,
        client_id=run.client_id,
        status=run.status,
        positions_count=run.positions_count,
        completed_positions=run.completed_positions,
        integrated_score=_f(run.integrated_score),
        generation_mode=run.generation_mode,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


def _benchmark_run_to_detail(run, positions) -> BenchmarkRunDetail:
    return BenchmarkRunDetail(
        id=run.id,
        client_id=run.client_id,
        status=run.status,
        positions_count=run.positions_count,
        completed_positions=run.completed_positions,
        integrated_score=_f(run.integrated_score),
        generation_mode=run.generation_mode,
        truthfulness_score=_f(run.truthfulness_score),
        role_fit_score=_f(run.role_fit_score),
        seniority_positioning_score=_f(run.seniority_positioning_score),
        clarity_impact_score=_f(run.clarity_impact_score),
        mechanism_quality_score=_f(run.mechanism_quality_score),
        constraint_compliance_score=_f(run.constraint_compliance_score),
        cover_letter_effectiveness_score=_f(run.cover_letter_effectiveness_score),
        overall_readiness_score=_f(run.overall_readiness_score),
        simple_model=run.simple_model,
        assess_model=run.assess_model,
        error_message=run.error_message,
        report_dir=run.report_dir,
        weights_json=run.weights_json,
        created_at=run.created_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        positions=[_benchmark_position_to_schema(p) for p in positions],
    )


def _benchmark_position_to_schema(p) -> BenchmarkPositionSummary:
    return BenchmarkPositionSummary(
        id=p.id,
        position_url=p.position_url,
        company=p.company,
        role_title=p.role_title,
        truthfulness_score=_f(p.truthfulness_score),
        role_fit_score=_f(p.role_fit_score),
        seniority_positioning_score=_f(p.seniority_positioning_score),
        clarity_impact_score=_f(p.clarity_impact_score),
        mechanism_quality_score=_f(p.mechanism_quality_score),
        constraint_compliance_score=_f(p.constraint_compliance_score),
        cover_letter_effectiveness_score=_f(p.cover_letter_effectiveness_score),
        overall_readiness_score=_f(p.overall_readiness_score),
        integrated_score=_f(p.integrated_score),
    )


# ── Shared download helpers ────────────────────────────────────────────────


def _validate_date_range(from_date: date, to_date: date | None) -> date:
    """Return effective to_date (defaults to today) and raise 400 if range is invalid."""
    effective_to = to_date or date.today()
    if from_date > effective_to:
        raise HTTPException(
            status_code=400,
            detail=f"from_date ({from_date}) must not be after to_date ({effective_to}).",
        )
    return effective_to


def _build_zip(files: list[tuple[str, Path]]) -> bytes:
    """Build an in-memory ZIP from a list of (archive_name, file_path) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in files:
            zf.write(path, arcname)
    return buf.getvalue()


def _build_zip_from_bytes(files: list[tuple[str, bytes]]) -> bytes:
    """Build an in-memory ZIP from a list of (archive_name, raw_bytes) pairs."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, data in files:
            zf.writestr(arcname, data)
    return buf.getvalue()


def _zip_response(zip_bytes: bytes, filename: str) -> Response:
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Log download ───────────────────────────────────────────────────────────


def _log_files_for_range(log_dir: str, from_date: date, to_date: date) -> list[tuple[str, Path]]:
    """
    Return (archive_name, path) pairs for existing log files in [from_date, to_date].

    TimedRotatingFileHandler names the current day's file 'app.log' and
    rotated past-day files 'app.log.YYYY-MM-DD'.
    """
    log_path = Path(log_dir)
    today = date.today()
    result: list[tuple[str, Path]] = []
    current = from_date
    while current <= to_date:
        if current == today:
            candidate = log_path / "app.log"
            arcname = f"app-{today.isoformat()}.log"
        else:
            candidate = log_path / f"app.log.{current.isoformat()}"
            arcname = f"app-{current.isoformat()}.log"
        if candidate.exists():
            result.append((arcname, candidate))
        current += timedelta(days=1)
    return result


@router.get("/logs/download")
def download_logs(
    _admin: AdminDep,
    from_date: date = Query(..., description="Start date (inclusive), YYYY-MM-DD"),
    to_date: date | None = Query(None, description="End date (inclusive), YYYY-MM-DD. Defaults to today."),
):
    """
    Download a ZIP archive of daily log files for the given date range.

    Requires LOG_DIR to be configured on the server.
    Returns logs-YYYY-MM-DD-to-YYYY-MM-DD.zip containing one .log file per day.
    """
    settings = get_settings()
    if not settings.log_dir:
        raise HTTPException(
            status_code=503,
            detail="File logging is not enabled on this server (LOG_DIR is not set).",
        )

    effective_to = _validate_date_range(from_date, to_date)
    files = _log_files_for_range(settings.log_dir, from_date, effective_to)

    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"No log files found for {from_date} – {effective_to}.",
        )

    zip_bytes = _build_zip(files)
    filename = f"logs-{from_date.isoformat()}-to-{effective_to.isoformat()}.zip"
    return _zip_response(zip_bytes, filename)


# ── Run data download ──────────────────────────────────────────────────────

_RUN_DATA_DATE_RE = re.compile(r"(\d{4})(\d{2})(\d{2})-\d{6}\.json$")


def _run_data_files_for_range(
    run_data_dir: str, from_date: date, to_date: date
) -> list[Path]:
    """
    Return run-data JSON files whose filename timestamp falls in [from_date, to_date].

    Filenames follow the CLI convention: {company}-{title}-{YYYYMMDD}-{HHMMSS}.json
    """
    run_data_path = Path(run_data_dir)
    if not run_data_path.exists():
        return []
    result: list[Path] = []
    for f in run_data_path.glob("*.json"):
        m = _RUN_DATA_DATE_RE.search(f.name)
        if not m:
            continue
        try:
            file_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if from_date <= file_date <= to_date:
            result.append(f)
    return sorted(result)


def _find_run_data_file(run_data_dir: str, run_id: str) -> Path | None:
    """
    Scan run-data files to find the one whose generation_run_id matches run_id.

    Files are checked most-recently-modified first so recent runs are found quickly.
    """
    run_data_path = Path(run_data_dir)
    if not run_data_path.exists():
        return None
    files = sorted(
        run_data_path.glob("*.json"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("generation_run_id") == run_id:
                return f
        except Exception:
            continue
    return None


@router.get("/run-data/download")
def download_run_data(
    _admin: AdminDep,
    db: DbDep,
    from_date: date = Query(..., description="Start date (inclusive), YYYY-MM-DD"),
    to_date: date | None = Query(None, description="End date (inclusive), YYYY-MM-DD. Defaults to today."),
):
    """
    Download a ZIP archive of run-data JSON files for the given date range.

    Queries the DB for succeeded runs in the range, then fetches each debug.json
    from object storage. Runs that pre-date the storage feature are skipped silently.
    Returns run-data-YYYY-MM-DD-to-YYYY-MM-DD.zip containing one JSON per run.
    """
    effective_to = _validate_date_range(from_date, to_date)
    runs = GenerationRunRepository(db).list_by_date_range(from_date, effective_to)

    if not runs:
        raise HTTPException(
            status_code=404,
            detail=f"No succeeded runs found for {from_date} – {effective_to}.",
        )

    storage = _storage_service()
    files: list[tuple[str, bytes]] = []
    for run in runs:
        try:
            data = storage.get_debug_json_bytes(run.user_id, str(run.id))
            files.append((_build_run_filename(run, db), data))
        except Exception:
            # Skip runs that have no debug file (pre-feature or failed upload)
            continue

    if not files:
        raise HTTPException(
            status_code=404,
            detail=f"No run data files found for {from_date} – {effective_to}.",
        )

    zip_bytes = _build_zip_from_bytes(files)
    filename = f"run-data-{from_date.isoformat()}-to-{effective_to.isoformat()}.zip"
    return _zip_response(zip_bytes, filename)


@router.get("/run-data/download/{run_id}")
def download_run_data_by_id(run_id: str, _admin: AdminDep, db: DbDep):
    """
    Download the run-data JSON for a specific generation run.

    Looks up the run in the DB, then fetches debug.json from object storage.
    Returns 404 if the run does not exist or has no debug file.
    """
    try:
        run_id_int = int(run_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="run_id must be an integer.")

    run = GenerationRunRepository(db).get_by_id(run_id_int)
    if run is None:
        raise HTTPException(
            status_code=404,
            detail=f"Generation run {run_id} not found.",
        )

    storage = _storage_service()
    try:
        data = storage.get_debug_json_bytes(run.user_id, str(run_id_int))
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"No debug file found for run {run_id}.",
        )

    filename = _build_run_filename(run, db)
    return Response(
        content=data,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Billing admin ──────────────────────────────────────────────────────────

@router.post("/billing/grant-credits", status_code=200)
def grant_credits(request: GrantCreditsRequest, _admin: AdminDep, db: DbDep):
    """
    Grant one-time generation credits to a user.

    Admin-only. Use to award beta credits or compensate users manually.
    Creates a billing row for the user if none exists.
    """
    if request.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    BillingRepository(db).add_credits(request.user_id, request.amount)
    return {"user_id": request.user_id, "credits_granted": request.amount}
