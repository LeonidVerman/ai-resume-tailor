"""Command-line interface for AI Resume Tailor."""

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

from tailor.config import ASSESS_MODEL, ASSESS_TEMPERATURE, COVER_TEMPLATE, ENABLE_PLAN_REPAIR, ENABLE_TWO_PHASE, OUTPUT_DIR, RESUME_TEMPLATE
from tailor.debug import save_debug_data
from tailor.diff import diff_resume
from tailor.docx.pdf import docx_to_pdf
from tailor.docx.template_fill import normalize_cover_letter, read_docx, save_doc_from_template
from tailor.job import JobData
from tailor.job.scrape import scrape_job_url
from tailor.llm import (
    PlanParseError,
    PlanValidationError,
    _run_schema_gate,
    extract_metadata_ai,
    plan_repair_tailoring,
    plan_tailoring,
    tailor_documents,
    tailor_documents_with_plan,
    validate_plan,
)
from tailor.plan_validator import validate_plan_extended
from tailor.prompts import _read_text_file


def _confirm_overwrite(path, force=False):
    """Return True if *path* does not exist, force is set, or the user confirms."""
    if not os.path.exists(path) or force:
        return True
    answer = input(f"File already exists: {path}\nOverwrite? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "assess":
        _main_assess(sys.argv[2:])
        return
    _main_tailor()


def _main_assess(argv: list[str]) -> None:
    """Entry point for the `tailor assess` subcommand."""
    from tailor.assess import run_assess_pipeline

    parser = argparse.ArgumentParser(
        prog="tailor assess",
        description="Score tailored documents for a batch of job positions.",
    )
    parser.add_argument(
        "--positions", required=True, metavar="FILE",
        help="Path to positions file (one URL per line).",
    )
    parser.add_argument(
        "--out", default="reports", metavar="DIR",
        help="Base reports directory (default: reports). A timestamped subfolder is created inside.",
    )
    parser.add_argument(
        "--model", default=ASSESS_MODEL, metavar="MODEL",
        help=f"LLM model for assessment (default: {ASSESS_MODEL}).",
    )
    parser.add_argument(
        "--temperature", type=float, default=ASSESS_TEMPERATURE, metavar="T",
        help=f"Sampling temperature (default: {ASSESS_TEMPERATURE}).",
    )
    parser.add_argument(
        "--max_positions", type=int, default=None, metavar="N",
        help="Maximum number of positions to process.",
    )
    parser.add_argument(
        "--cache_dir", default=None, metavar="DIR",
        help="Directory for caching raw assessment results.",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="(Reserved) Number of judge runs per position.",
    )
    parser.add_argument(
        "--judge_only_after_validation_fail", default="false",
        help="(Reserved) Future flag.",
    )
    args = parser.parse_args(argv)

    run_assess_pipeline(
        positions_file=args.positions,
        out_dir=args.out,
        model=args.model,
        temperature=args.temperature,
        max_positions=args.max_positions,
        cache_dir=args.cache_dir,
        runs=args.runs,
    )


def _main_tailor() -> None:
    """Entry point for the default `tailor` (generate) command."""
    parser = argparse.ArgumentParser(
        description="Tailor a resume and cover letter to a specific job posting."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "-pu", "--position-url",
        metavar="URL",
        help="Job board URL to scrape the position from.",
    )
    source.add_argument(
        "-pd", "--position-desc",
        metavar="FILE",
        help="Path to a text file containing the position description.",
    )
    parser.add_argument(
        "-f", "--force",
        action="store_true",
        help="Overwrite existing output files without prompting.",
    )
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Save debug log only; skip docx/pdf generation.",
    )
    args = parser.parse_args()

    # --- Acquire job data ---
    if args.position_url:
        try:
            job = scrape_job_url(args.position_url)
        except RuntimeError as e:
            parser.error(str(e))
    else:
        try:
            job_text = _read_text_file(args.position_desc)
        except OSError as e:
            parser.error(f"Cannot read position description file: {e}")
        print("Extracting company & role from position description...")
        meta = extract_metadata_ai(job_text)
        job = JobData(
            company=meta.get("company", "Unknown"),
            job_title=meta.get("job_title", "Unknown"),
            description=job_text,
        )

    print(f"Detected Company: {job.company}")
    print(f"Detected Role:    {job.job_title}")

    # --- Read templates ---
    resume_template = read_docx(RESUME_TEMPLATE)
    cover_template  = read_docx(COVER_TEMPLATE)

    # --- Tailor documents (two-phase or single-pass) ---
    if ENABLE_TWO_PHASE:
        result, llm_request, phase1_debug, phase2_debug = _run_two_phase(
            job, resume_template, cover_template
        )
    else:
        print("Tailoring documents (single-pass)...")
        result, llm_request = tailor_documents(job, resume_template, cover_template)
        phase1_debug = None
        phase2_debug = None

    diff = {}
    if result.resume:
        sections = diff_resume(resume_template, result.resume)
        if sections:
            diff["resume"] = sections

    save_debug_data(
        job.company,
        job.job_title,
        {"resume": result.resume, "cover_letter": result.cover_letter},
        llm_request,
        diff=diff or None,
        phase1=phase1_debug,
        phase2=phase2_debug,
    )

    if args.debug:
        print("Debug mode: skipping docx/pdf generation.")
        return

    # --- Write output files ---
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    safe_company = "".join(
        c if c.isalnum() or c in "_-" else "_" for c in job.company
    ).strip("_")

    resume_docx = str(
        OUTPUT_DIR / os.path.basename(RESUME_TEMPLATE).replace("Template", safe_company)
    )
    cover_docx = str(
        OUTPUT_DIR / os.path.basename(COVER_TEMPLATE).replace("Template", safe_company)
    )

    if result.resume is None:
        print("Warning: LLM response did not include a resume. Skipping resume generation.")
    elif _confirm_overwrite(resume_docx, args.force):
        save_doc_from_template(RESUME_TEMPLATE, resume_docx, result.resume)
        resume_pdf = os.path.splitext(resume_docx)[0] + ".pdf"
        if _confirm_overwrite(resume_pdf, args.force):
            docx_to_pdf(resume_docx)

    if result.cover_letter is None:
        print(
            "Warning: LLM response did not include a cover letter (likely hit the output "
            "token limit). Re-run to retry, or paste a shorter job description."
        )
    elif _confirm_overwrite(cover_docx, args.force):
        save_doc_from_template(COVER_TEMPLATE, cover_docx, normalize_cover_letter(result.cover_letter))
        cover_pdf = os.path.splitext(cover_docx)[0] + ".pdf"
        if _confirm_overwrite(cover_pdf, args.force):
            docx_to_pdf(cover_docx)

    print("Documents generated successfully.")


def _run_two_phase(
    job: JobData,
    resume_template: str,
    cover_template: str,
):
    """Run Phase 1 (plan) then Phase 2 (write).  Exits with an error if
    Phase 1 fails after both attempts.

    Attempt 1: ALWAYS calls plan_tailoring (phase1.txt).
    Attempt 2: ALWAYS calls plan_repair_tailoring (phase1_repair.txt),
               passing the last parsed plan + both error lists.

    Validation order per attempt:
      A) JSON parse  — caught as json.JSONDecodeError
      B) Schema gate — _run_schema_gate (types + required keys)
      C) v1 content  — validate_plan (theme count, quote length, role_level)
      D) v2.1 deep   — validate_plan_extended

    Returns
    -------
    result, llm_request, phase1_debug, phase2_debug
    """
    print("Phase 1: generating tailoring plan...")
    plan = None
    p1_messages = None
    p1_meta = None

    # State passed to the repair attempt.
    prev_raw_plan: dict = {}        # last successfully parsed plan dict (empty if JSON failed)
    prev_raw_text: str | None = None  # raw LLM text when JSON parse failed
    schema_errors: list[str] = []
    validation_errors: list[str] = []

    for attempt in range(1, 3):
        is_repair = attempt > 1

        # ------------------------------------------------------------------
        # Step 0: call the LLM
        # Attempt 1: ALWAYS phase1.txt (PLAN prompt)
        # Attempt 2: phase1_repair.txt (REPAIR prompt) when we have
        #            a broken plan or raw text to repair — otherwise rerun
        #            phase1.txt (guardrail: repair cannot help with
        #            an empty INVALID_PLAN and no RAW_TEXT).
        # ------------------------------------------------------------------
        if is_repair and not ENABLE_PLAN_REPAIR:
            break  # repair disabled; don't try a second time

        try:
            if not is_repair:
                logger.info("Phase 1 attempt %d: prompt=PLAN", attempt)
                raw_plan, p1_messages, p1_meta = plan_tailoring(
                    job, resume_template, cover_template
                )
            else:
                # Repair is only useful when attempt 1 returned a valid-but-schema-invalid
                # JSON object.  A truncated/broken JSON string (prev_raw_text only) cannot
                # be meaningfully fixed by the repair model — rerun the planner instead.
                can_repair = bool(prev_raw_plan)
                if can_repair:
                    logger.info("Phase 1 attempt %d: prompt=REPAIR", attempt)
                    raw_plan, p1_messages, p1_meta = plan_repair_tailoring(
                        prev_raw_plan, schema_errors, validation_errors,
                        job, resume_template, cover_template,
                        raw_text=prev_raw_text,
                    )
                else:
                    # Guardrail: repair with empty INVALID_PLAN and no RAW_TEXT
                    # would produce a partial plan — rerun planner instead.
                    logger.info(
                        "Phase 1 attempt %d: prompt=PLAN "
                        "(repair guardrail: INVALID_PLAN={} and no RAW_TEXT)",
                        attempt,
                    )
                    raw_plan, p1_messages, p1_meta = plan_tailoring(
                        job, resume_template, cover_template
                    )
        except PlanParseError as exc:
            schema_errors = [f"invalid_json: {exc}"]
            validation_errors = []
            prev_raw_plan = {}
            prev_raw_text = exc.raw_content
            print(f"Warning: Phase 1 attempt {attempt} returned invalid JSON.")
            continue
        except Exception as exc:
            schema_errors = [f"api_error: {exc}"]
            validation_errors = []
            prev_raw_plan = {}
            prev_raw_text = None
            print(f"Warning: Phase 1 attempt {attempt} failed: {exc}")
            continue

        # JSON parse succeeded — clear stale raw_text (we have a proper dict now)
        prev_raw_text = None

        # ------------------------------------------------------------------
        # Step A: schema gate (types + required keys)
        # If this fails, deep validation would crash — skip straight to repair.
        # ------------------------------------------------------------------
        gate_errors = _run_schema_gate(raw_plan)
        if gate_errors:
            schema_errors = gate_errors
            validation_errors = []
            prev_raw_plan = raw_plan
            summary = gate_errors[0] + (
                f" (+{len(gate_errors) - 1} more)" if len(gate_errors) > 1 else ""
            )
            print(f"Warning: Phase 1 attempt {attempt} schema invalid: {summary}")
            continue

        # ------------------------------------------------------------------
        # Step B: v1 content checks (theme count, quote length)
        #         validate_plan also coerces role_level in place.
        # ------------------------------------------------------------------
        try:
            validate_plan(raw_plan)
        except PlanValidationError as exc:
            schema_errors = []
            validation_errors = [str(exc)]
            prev_raw_plan = raw_plan
            print(f"Warning: Phase 1 attempt {attempt} v1 validation error: {exc}")
            continue

        # ------------------------------------------------------------------
        # Step C: v2.1 deep checks (evidence saturation, role intent, etc.)
        # ------------------------------------------------------------------
        extended_errors = validate_plan_extended(raw_plan)
        if extended_errors:
            schema_errors = []
            validation_errors = extended_errors
            prev_raw_plan = raw_plan
            print(
                f"Warning: Phase 1 attempt {attempt} extended validation: "
                f"{len(extended_errors)} error(s)."
            )
            continue

        # ------------------------------------------------------------------
        # All checks passed.
        # ------------------------------------------------------------------
        plan = raw_plan
        break

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
        print("Error: Phase 1 failed after all attempts. Cannot produce tailored documents.")
        sys.exit(1)

    print("Phase 2: writing tailored documents...")
    result, p2_messages, p2_meta = tailor_documents_with_plan(
        plan, job, resume_template, cover_template
    )

    # p2_meta already contains writer_packet, attempts[], final_validation_ok
    phase2_debug = p2_meta

    if not p2_meta.get("final_validation_ok", True):
        n = len(p2_meta.get("attempts", []))
        print(f"Warning: Phase 2 validation failed after {n} attempt(s). "
              "Best-effort output used. See debug file for details.")

    return result, p2_messages, phase1_debug, phase2_debug
