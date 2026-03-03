"""Assessment pipeline: score tailored documents across a batch of positions.

Usage (via CLI):
    tailor assess --positions positions.txt --out reports/assess_20260228.json

One LLM round-trip per position (assess.txt prompt).  Results are cached by
(position_url, resume_hash, cover_hash, model, prompt_version) so re-runs
with the same output skip the LLM call.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from tailor.config import ASSESS_MODEL, ASSESS_TEMPERATURE, COVER_TEMPLATE, RESUME_TEMPLATE
from tailor.debug import save_debug_data
from tailor.docx.template_fill import read_docx
from tailor.job import JobData
from tailor.job.scrape import scrape_job_url
from tailor.llm import (
    PlanParseError,
    PlanValidationError,
    _run_schema_gate,
    get_client,
    plan_tailoring,
    tailor_documents,
    tailor_documents_with_plan,
    validate_plan,
)
from tailor.plan_validator import validate_plan_extended
from tailor.prompts import _load_candidate_profile, _load_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROMPT_VERSION = "assess_v1"

# Company name and role title currently present in the cover letter template.
# Update these if the template is re-written for a different target role.
_COVER_TEMPLATE_COMPANY = "Tigera"
_COVER_TEMPLATE_TITLE = "Senior Software Engineer"

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

_DEFAULT_WEIGHTS: dict[str, float] = {
    "truthfulness": 0.20,
    "role_fit": 0.20,
    "seniority_positioning": 0.10,
    "clarity_impact": 0.15,
    "mechanism_quality": 0.10,
    "constraint_compliance": 0.15,
    "cover_letter_effectiveness": 0.05,
    "overall_readiness": 0.05,
}


# ---------------------------------------------------------------------------
# Positions file loader
# ---------------------------------------------------------------------------

def load_positions(path: str) -> list[str]:
    """Parse a positions file: one URL per line; blank lines and # comments ignored."""
    urls: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls


# ---------------------------------------------------------------------------
# Per-position tailoring (Phase 1 + Phase 2, single attempt each)
# ---------------------------------------------------------------------------

def _tailor_position(
    job: JobData,
    resume_template: str,
    cover_template: str,
) -> tuple[Any, dict | None, dict | None, dict | None]:
    """Run Phase 1 → Phase 2 for one position.

    Returns
    -------
    result : TailorResult
    plan : validated plan dict, or None if Phase 1 failed
    phase2_debug : Phase 2 debug meta dict, or None if unavailable
    phase1_debug : Phase 1 debug dict (model, usage, raw_response, plan_json), or None
    """
    try:
        raw_plan, p1_messages, p1_meta = plan_tailoring(job, resume_template, cover_template)
    except (PlanParseError, Exception) as exc:
        logger.warning("Phase 1 failed for %s: %s", job.source_url, exc)
        result, _ = tailor_documents(job, resume_template, cover_template)
        return result, None, None, None

    gate_errors = _run_schema_gate(raw_plan)
    if gate_errors:
        logger.warning("Phase 1 schema invalid for %s: %s", job.source_url, gate_errors[0])
        result, _ = tailor_documents(job, resume_template, cover_template)
        return result, None, None, None

    try:
        validate_plan(raw_plan)
    except PlanValidationError as exc:
        logger.warning("Phase 1 validation failed for %s: %s", job.source_url, exc)
        result, _ = tailor_documents(job, resume_template, cover_template)
        return result, None, None, None

    extended_errors = validate_plan_extended(raw_plan)
    if extended_errors:
        logger.info(
            "Phase 1 extended validation warnings (%d) for %s",
            len(extended_errors), job.source_url,
        )

    plan = raw_plan
    result, _, phase2_debug = tailor_documents_with_plan(plan, job, resume_template, cover_template)

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


# ---------------------------------------------------------------------------
# Assessment input construction
# ---------------------------------------------------------------------------

def _extract_validator_findings(phase2_debug: dict | None) -> dict:
    """Pull structured constraint findings from the last Phase 2 attempt."""
    _empty: dict = {
        "unsafe_noun_hits": [],
        "missing_skills": [],
        "missing_metrics": [],
        "validation_ok": True,
        "error_count": 0,
    }
    if not phase2_debug:
        return _empty
    attempts = phase2_debug.get("attempts", [])
    if not attempts:
        return _empty
    last_report: dict = attempts[-1].get("validation_report", {})
    global_issues: dict = last_report.get("repair_brief", {}).get("global_issues", {})
    return {
        "unsafe_noun_hits": global_issues.get("unsafe_nouns_in_resume", []),
        "missing_skills": global_issues.get("missing_skills", []),
        "missing_metrics": global_issues.get("missing_metrics", []),
        "validation_ok": last_report.get("ok", True),
        "error_count": len(last_report.get("errors", [])),
    }


def _extract_writer_packet_summary(phase2_debug: dict | None, plan: dict | None) -> dict:
    """Pull the writer-packet fields most useful for assessment context."""
    wp: dict = (phase2_debug or {}).get("writer_packet", {})
    return {
        "role_level": (plan or {}).get("role_level"),
        "must_include_skills": wp.get("must_include_skills", []),
        "must_keep_metrics": wp.get("must_keep_metrics", []),
        "unsafe_jd_nouns": wp.get("unsafe_jd_nouns", []),
        "must_surface_arch_mechanisms": wp.get(
            "arch_mechanisms_primary", wp.get("must_surface_mechanisms", [])
        ),
        "density_targets": wp.get("density_targets", {}),
    }


def build_assessment_input(
    job: JobData,
    result: Any,
    master_resume: str,
    master_cover: str,
    profile_str: str,
    phase2_debug: dict | None,
    plan: dict | None,
) -> dict:
    """Build the structured payload sent to the assessment LLM."""
    return {
        "position_url": job.source_url or "",
        "company": job.company,
        "role_title": job.job_title,
        "job_description_text": job.description,
        "master_resume_text": master_resume,
        "master_cover_letter_text": master_cover,
        "candidate_profile_json": profile_str,
        "generated_resume_text": result.resume or "",
        "generated_cover_letter_text": result.cover_letter or "",
        "writer_packet_summary": _extract_writer_packet_summary(phase2_debug, plan),
        "validator_findings": _extract_validator_findings(phase2_debug),
    }


# ---------------------------------------------------------------------------
# Assessment LLM call
# ---------------------------------------------------------------------------

def _strip_code_fence(text: str) -> str:
    """Remove markdown ```...``` wrapper if present."""
    text = text.strip()
    if not text.startswith("```"):
        return text
    lines = text.split("\n")
    # Drop opening fence line (```json or ```)
    start = 1
    # Drop closing fence line if present
    end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
    return "\n".join(lines[start:end]).strip()


def _call_assess_llm(
    assessment_input: dict,
    model: str,
    temperature: float,
    max_tokens: int = 4096,
) -> dict:
    """Call the assessment LLM and return parsed JSON.

    On JSON parse failure, retries once with a repair message.
    """
    client = get_client()
    prompt = _load_prompt(
        "assess",
        ASSESSMENT_INPUT_JSON=json.dumps(assessment_input, ensure_ascii=False, separators=(",", ":")),
    )
    messages: list[dict] = [{"role": "user", "content": prompt}]

    response = client.chat.completions.create(
        model=model,
        temperature=temperature,
        max_completion_tokens=max_tokens,
        messages=messages,
    )
    raw = _strip_code_fence(response.choices[0].message.content or "")

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        repair_resp = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_completion_tokens=max_tokens,
            messages=messages + [
                {"role": "assistant", "content": raw},
                {"role": "user", "content": "Return valid JSON only. No extra text or markdown."},
            ],
        )
        raw2 = _strip_code_fence(repair_resp.choices[0].message.content or "")
        return json.loads(raw2)


# ---------------------------------------------------------------------------
# Assessment response validation
# ---------------------------------------------------------------------------

def validate_assessment_response(data: dict) -> list[str]:
    """Return a list of schema/value errors for an assessment LLM response.

    An empty list means the response is valid.
    """
    if not isinstance(data, dict):
        return ["response is not a dict"]
    errors: list[str] = []
    scores = data.get("scores", {})
    if not isinstance(scores, dict):
        return ["scores is not a dict"]
    for key in _SCORE_KEYS:
        if key not in scores:
            errors.append(f"missing score key: {key}")
            continue
        entry = scores[key]
        if not isinstance(entry, dict):
            errors.append(f"scores.{key} is not a dict")
            continue
        score = entry.get("score")
        if not isinstance(score, int) or not (1 <= score <= 10):
            errors.append(f"scores.{key}.score must be int 1-10, got {score!r}")
    return errors


# ---------------------------------------------------------------------------
# Score aggregation
# ---------------------------------------------------------------------------

def compute_integrated_score(scores: dict[str, int | float], weights: dict[str, float]) -> float:
    """Return a deterministic weighted average score (0.0–10.0)."""
    total_weight = sum(weights.get(k, 0.0) for k in scores if scores[k] is not None)
    if total_weight == 0.0:
        return 0.0
    weighted_sum = sum(
        float(scores[k]) * weights.get(k, 0.0)
        for k in scores
        if scores[k] is not None
    )
    return round(weighted_sum / total_weight, 2)


def _aggregate(
    position_entries: list[dict],
    weights: dict[str, float],
) -> dict[str, Any]:
    """Compute per-category averages and overall integrated score."""
    averages: dict[str, float] = {}
    for key in _SCORE_KEYS:
        vals = [
            e["scores"][key]
            for e in position_entries
            if isinstance(e.get("scores", {}).get(key), (int, float))
        ]
        averages[key] = round(sum(vals) / len(vals), 2) if vals else 0.0
    integrated = compute_integrated_score(averages, weights)
    return {"category_averages": averages, "integrated_score": integrated}


# ---------------------------------------------------------------------------
# Caching helpers
# ---------------------------------------------------------------------------

def _position_cache_key(
    position_url: str, resume_text: str, cover_text: str, model: str
) -> str:
    h = hashlib.sha256()
    for s in (position_url, resume_text or "", cover_text or "", model, _PROMPT_VERSION):
        h.update(s.encode())
    return h.hexdigest()[:16]


def _cache_path(cache_dir: str, key: str) -> Path:
    return Path(cache_dir) / f"{key}.json"


def _cache_load(cache_dir: str | None, key: str) -> dict | None:
    if not cache_dir:
        return None
    p = _cache_path(cache_dir, key)
    if not p.exists():
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_save(cache_dir: str | None, key: str, data: dict) -> None:
    if not cache_dir:
        return
    os.makedirs(cache_dir, exist_ok=True)
    with open(_cache_path(cache_dir, key), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Raw assessment persistence
# ---------------------------------------------------------------------------

def _save_raw(raw_dir: Path, key: str, data: dict) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{key}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return str(path)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def _save_csv(path: Path, entries: list[dict]) -> None:
    if not entries:
        return
    fieldnames = ["company", "role_title"] + _SCORE_KEYS + ["integrated_score"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for e in entries:
            row: dict = {"company": e["company"], "role_title": e["role_title"]}
            row.update(e.get("scores", {}))
            row["integrated_score"] = e.get("integrated_score")
            writer.writerow(row)


# ---------------------------------------------------------------------------
# Calibration data matching
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> list[str]:
    """Normalize a company name to lowercase alphanumeric tokens."""
    return re.findall(r"[a-z0-9]+", name.lower())


def _names_match(a: str, b: str) -> bool:
    """Return True when two company name strings refer to the same company.

    Handles partial matches such as "Plata Card" vs "Plata": the shorter
    name's tokens must all appear in the longer name's token list.
    """
    wa, wb = _normalize_name(a), _normalize_name(b)
    if not wa or not wb:
        return False
    if wa == wb:
        return True
    shorter, longer = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return all(w in longer for w in shorter)


def _extract_company_slug(filename: str) -> str:
    """Extract the company portion from a calibration document filename.

    Examples
    --------
    "Leonid_Verman_Resume_NEOGOV.docx"               → "NEOGOV"
    "Leonid_Verman_Cover_Letter_Jonas_Software.docx"  → "Jonas Software"
    """
    stem = Path(filename).stem
    for marker in ("Cover_Letter_", "Resume_"):
        idx = stem.find(marker)
        if idx != -1:
            return stem[idx + len(marker):].replace("_", " ")
    return stem


def _find_in_index(company: str, index: dict[str, str]) -> str | None:
    """Return the text for *company* in *index*, or None if not found."""
    for slug, text in index.items():
        if _names_match(company, slug):
            return text
    return None


def build_calibration_index(data_dir: str) -> tuple[dict[str, str], dict[str, str]]:
    """Scan *data_dir* and build company→text indexes for resumes and covers.

    Filenames must follow the pattern:
      <any>_Resume_<CompanyName>.docx
      <any>_Cover_Letter_<CompanyName>.docx

    Returns
    -------
    (resume_index, cover_index)
        Each dict maps a company slug (extracted from the filename) to the
        plain text extracted from the .docx file.

    Raises
    ------
    ValueError
        If *data_dir* does not exist or is not a directory.
    """
    data_path = Path(data_dir)
    if not data_path.is_dir():
        raise ValueError(f"Calibration data directory not found: {data_dir!r}")

    resume_index: dict[str, str] = {}
    cover_index: dict[str, str] = {}

    for f in sorted(data_path.glob("*.docx")):
        slug = _extract_company_slug(f.name)
        stem = f.stem
        if "Cover_Letter" in stem:
            cover_index[slug] = read_docx(str(f))
        elif "Resume" in stem:
            resume_index[slug] = read_docx(str(f))

    return resume_index, cover_index


def validate_calibration_coverage(
    companies: list[str],
    resume_index: dict[str, str],
    cover_index: dict[str, str],
) -> list[str]:
    """Return error messages for companies without a matching calibration file."""
    errors: list[str] = []
    for company in companies:
        if _find_in_index(company, resume_index) is None:
            errors.append(f"Missing resume for company: {company!r}")
        if _find_in_index(company, cover_index) is None:
            errors.append(f"Missing cover letter for company: {company!r}")
    return errors


# ---------------------------------------------------------------------------
# Calibration helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2}(?:[a-z]{1,2})?,?\s*\d{4}\b",
    re.IGNORECASE,
)


def _prepare_calibration_cover_letter(
    master_cover_text: str,
    company: str,
    job_title: str,
    current_date: str,
) -> str:
    """Replace date, company, and job title in the master cover letter for calibration.

    The assessment LLM penalises mismatched company/position, so these three
    fields must reflect the actual position being evaluated even when the rest of
    the cover letter is the unchanged master template text.
    """
    text = _DATE_RE.sub(current_date, master_cover_text)
    text = text.replace(_COVER_TEMPLATE_COMPANY, company)
    text = text.replace(_COVER_TEMPLATE_TITLE, job_title)
    return text


# ---------------------------------------------------------------------------
# Per-position worker (runs in a thread)
# ---------------------------------------------------------------------------

def _process_one_position(
    url: str,
    idx: int,
    total: int,
    resume_template: str,
    cover_template: str,
    profile_str: str,
    model: str,
    temperature: float,
    cache_dir: str | None,
    raw_dir: Path,
    weights: dict[str, float],
    print_lock: threading.Lock,
    calibrate: bool = False,
    calibration_index: tuple[dict[str, str], dict[str, str]] | None = None,
    calibration_errors: list[str] | None = None,
    calibration_errors_lock: threading.Lock | None = None,
) -> dict | None:
    """Scrape → tailor → assess one position URL.  Returns an entry dict or None on failure."""

    def _print(*args: Any) -> None:
        with print_lock:
            print(*args)

    _print(f"\n[{idx}/{total}] {url}")

    try:
        job = scrape_job_url(url)
    except Exception as exc:
        _print(f"  [{idx}] Skipping — scrape failed: {exc}")
        return None

    _print(f"  [{idx}] Company: {job.company}  |  Role: {job.job_title}")

    try:
        result, plan, phase2_debug, phase1_debug = _tailor_position(job, resume_template, cover_template)
    except Exception as exc:
        _print(f"  [{idx}] Skipping — tailoring failed: {exc}")
        return None

    save_debug_data(
        job.company,
        job.job_title,
        {"resume": result.resume, "cover_letter": result.cover_letter},
        llm_request=None,
        diff=None,
        phase1=phase1_debug,
        phase2=phase2_debug,
    )

    # Determine what resume/cover letter text to send to the assessor.
    # calibration_index  → use pre-generated sample docx files
    # calibrate          → use master template (existing behaviour)
    # otherwise          → use the tailored output
    from tailor.llm import TailorResult as _TailorResult  # local to avoid circular at module level

    if calibration_index is not None:
        resume_idx, cover_idx = calibration_index
        cal_resume = _find_in_index(job.company, resume_idx)
        cal_cover = _find_in_index(job.company, cover_idx)
        missing_parts: list[str] = []
        if cal_resume is None:
            missing_parts.append("resume")
        if cal_cover is None:
            missing_parts.append("cover letter")
        if missing_parts:
            msg = f"No {' or '.join(missing_parts)} found for company {job.company!r} (URL: {url})"
            if calibration_errors is not None and calibration_errors_lock is not None:
                with calibration_errors_lock:
                    calibration_errors.append(msg)
            _print(f"  [{idx}] ERROR — {msg}")
            return None
        assessment_result = _TailorResult(resume=cal_resume, cover_letter=cal_cover)
    elif calibrate:
        today = date.today()
        current_date = f"{today.strftime('%B')} {today.day}, {today.year}"
        assessment_result = _TailorResult(
            resume=resume_template,
            cover_letter=_prepare_calibration_cover_letter(
                cover_template, job.company, job.job_title, current_date
            ),
        )
    else:
        assessment_result = result

    cache_key = _position_cache_key(
        url, assessment_result.resume or "", assessment_result.cover_letter or "", model
    )
    raw_assessment = _cache_load(cache_dir, cache_key)

    if raw_assessment is not None:
        _print(f"  [{idx}] (using cached assessment)")
    else:
        assessment_input = build_assessment_input(
            job, assessment_result, resume_template, cover_template, profile_str, phase2_debug, plan
        )
        try:
            raw_assessment = _call_assess_llm(assessment_input, model, temperature)
        except Exception as exc:
            _print(f"  [{idx}] Skipping — assess LLM failed: {exc}")
            return None
        _cache_save(cache_dir, cache_key, raw_assessment)

    raw_path = _save_raw(raw_dir, cache_key, raw_assessment)

    raw_scores: dict = raw_assessment.get("scores", {})
    scores_flat: dict[str, int] = {
        k: raw_scores[k]["score"]
        for k in _SCORE_KEYS
        if isinstance(raw_scores.get(k), dict) and isinstance(raw_scores[k].get("score"), int)
    }
    integrated = compute_integrated_score(scores_flat, weights)

    _print(f"  [{idx}] {job.company} / {job.job_title} — integrated: {integrated:.2f}")

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
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

_MAX_WORKERS_CAP = 20


def run_assess_pipeline(
    positions_file: str,
    out_dir: str = "reports",
    model: str = ASSESS_MODEL,
    temperature: float = ASSESS_TEMPERATURE,
    max_positions: int | None = None,
    cache_dir: str | None = None,
    runs: int = 1,
    workers: int | None = None,
    weights: dict[str, float] | None = None,
    calibrate: bool = False,
    calibrate_data: str | None = None,
) -> dict:
    """Score tailored documents for each URL in *positions_file*.

    Parameters
    ----------
    positions_file:
        Path to a text file with one URL per line (# comments / blank lines ignored).
    out_dir:
        Base reports directory.  A timestamped subfolder is created inside it::

            <out_dir>/<YYYYMMDD_HHmmss>/assess_<YYYYMMDD_HHmmss>.json
            <out_dir>/<YYYYMMDD_HHmmss>/assess_<YYYYMMDD_HHmmss>.csv
            <out_dir>/<YYYYMMDD_HHmmss>/raw/<hash>.json
    model:
        OpenAI model used for assessment.
    temperature:
        Sampling temperature for the assessment LLM call.
    max_positions:
        Cap on number of positions to process (None = no cap).
    cache_dir:
        Directory for caching raw assessment results.  None = no cache.
    runs:
        Reserved for future multi-judge averaging; currently always 1.
    workers:
        Number of parallel threads.  Default: min(len(urls), 20).
        If the provided value exceeds the default, the default is used with a warning.
    weights:
        Category weights for integrated score.  Defaults to _DEFAULT_WEIGHTS.

    Returns
    -------
    The aggregate report dict (also written to the timestamped JSON file).
    """
    if weights is None:
        weights = _DEFAULT_WEIGHTS

    # Capture timestamp once — folder name and filenames share the same value.
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if calibrate_data:
        dir_suffix = "_calibration_data"
    elif calibrate:
        dir_suffix = "_calibration"
    else:
        dir_suffix = ""
    run_dir = Path(out_dir) / f"{ts}{dir_suffix}"
    raw_dir = run_dir / "raw"
    report_path = run_dir / f"assess_{ts}.json"
    csv_path = run_dir / f"assess_{ts}.csv"

    urls = load_positions(positions_file)
    if max_positions is not None:
        urls = urls[:max_positions]

    # Resolve effective worker count
    default_workers = min(len(urls), _MAX_WORKERS_CAP)
    if workers is None:
        effective_workers = default_workers
    elif workers > default_workers:
        print(
            f"Warning: --workers {workers} exceeds maximum {default_workers} "
            f"(min(positions={len(urls)}, cap={_MAX_WORKERS_CAP})). "
            f"Using {default_workers}."
        )
        effective_workers = default_workers
    else:
        effective_workers = workers

    if calibrate_data:
        print(
            f"[CALIBRATION DATA MODE] Pre-generated sample documents from {calibrate_data!r} "
            "will be sent to assessment."
        )
    elif calibrate:
        print("[CALIBRATION MODE] Master resume/cover letter will be sent to assessment.")
    print(
        f"Assessing {len(urls)} position(s) with model {model} "
        f"using {effective_workers} parallel worker(s)..."
    )

    # Load shared resources once (read-only, safe to share across threads)
    resume_template = read_docx(RESUME_TEMPLATE)
    cover_template = read_docx(COVER_TEMPLATE)
    profile_str = _load_candidate_profile()

    # Build calibration index when --calibrate-data is provided.
    cal_index: tuple[dict[str, str], dict[str, str]] | None = None
    if calibrate_data:
        cal_index = build_calibration_index(calibrate_data)
        resume_idx, cover_idx = cal_index
        print(
            f"  Loaded {len(resume_idx)} resume(s): {', '.join(resume_idx.keys())}; "
            f"{len(cover_idx)} cover letter(s): {', '.join(cover_idx.keys())}"
        )

    print_lock = threading.Lock()
    calibration_errors: list[str] = []
    calibration_errors_lock = threading.Lock()

    # Submit all positions; preserve submission order in the final report
    with ThreadPoolExecutor(max_workers=effective_workers) as executor:
        futures = [
            executor.submit(
                _process_one_position,
                url, i, len(urls),
                resume_template, cover_template, profile_str,
                model, temperature, cache_dir, raw_dir, weights, print_lock,
                calibrate,
                cal_index,
                calibration_errors if cal_index is not None else None,
                calibration_errors_lock if cal_index is not None else None,
            )
            for i, url in enumerate(urls, 1)
        ]
        position_entries: list[dict] = [
            entry
            for future in futures
            if (entry := future.result()) is not None
        ]

    # Abort if any positions were missing calibration files.
    if calibration_errors:
        error_msg = (
            "Calibration data files missing for the following positions:\n"
            + "\n".join(f"  {e}" for e in calibration_errors)
        )
        raise ValueError(error_msg)

    # --- Aggregate ---
    agg = _aggregate(position_entries, weights)

    report: dict = {
        "version": "assess_report_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "calibrate": calibrate and not calibrate_data,
        "calibrate_data": bool(calibrate_data),
        "positions_count": len(position_entries),
        "positions": position_entries,
        "category_averages": agg["category_averages"],
        "integrated_score": agg["integrated_score"],
        "weights": weights,
    }

    # --- Write aggregate JSON ---
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nReport saved to {report_path}")

    # --- Write CSV ---
    _save_csv(csv_path, position_entries)
    print(f"CSV   saved to {csv_path}")

    return report
