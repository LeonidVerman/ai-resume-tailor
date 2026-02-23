"""Command-line interface for AI Resume Tailor."""

import argparse
import os

from tailor.config import COVER_TEMPLATE, ENABLE_PLAN_REPAIR, ENABLE_TWO_PHASE, OUTPUT_DIR, RESUME_TEMPLATE
from tailor.debug import save_debug_data
from tailor.diff import diff_resume
from tailor.docx.pdf import docx_to_pdf
from tailor.docx.template_fill import normalize_cover_letter, read_docx, save_doc_from_template
from tailor.job import JobData
from tailor.job.scrape import scrape_job_url
from tailor.llm import (
    PlanValidationError,
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
    """Run Phase 1 (plan) then Phase 2 (write).  Falls back to single-pass on
    Phase 1 failure after one retry.

    Returns
    -------
    result, llm_request, phase1_debug, phase2_debug
    """
    print("Phase 1: generating tailoring plan...")
    plan = None
    p1_messages = None
    p1_meta = None
    validation_errors: list[str] = []
    last_raw_plan: dict | None = None

    for attempt in range(1, 3):  # up to 2 attempts
        try:
            if attempt == 1:
                raw_plan, p1_messages, p1_meta = plan_tailoring(
                    job, resume_template, cover_template
                )
            else:
                # Attempt 2: use plan repair if enabled and we have a previous plan
                if ENABLE_PLAN_REPAIR and last_raw_plan is not None:
                    raw_plan, p1_messages, p1_meta = plan_repair_tailoring(
                        last_raw_plan, validation_errors, job, resume_template, cover_template
                    )
                else:
                    raw_plan, p1_messages, p1_meta = plan_tailoring(
                        job, resume_template, cover_template
                    )

            last_raw_plan = raw_plan
            plan = validate_plan(raw_plan)

            # Extended v2.1 checks
            extended_errors = validate_plan_extended(raw_plan)
            if extended_errors:
                raise PlanValidationError(
                    f"v2.1 extended validation failed: {'; '.join(extended_errors)}"
                )

            break
        except PlanValidationError as exc:
            msg = f"Phase 1 validation error (attempt {attempt}): {exc}"
            print(f"Warning: {msg}")
            validation_errors.append(msg)
            plan = None
        except Exception as exc:
            msg = f"Phase 1 failed (attempt {attempt}): {exc}"
            print(f"Warning: {msg}")
            validation_errors.append(msg)
            plan = None

    phase1_debug = {
        "llm_request": p1_messages,
        "llm_response_raw": p1_meta.get("raw_response") if p1_meta else None,
        "plan_json": plan,
        "model": p1_meta.get("model") if p1_meta else None,
        "usage": p1_meta.get("usage") if p1_meta else None,
        "validation_errors": validation_errors,
    }

    if plan is None:
        # Fall back to single-pass
        print("Warning: Phase 1 failed after retries. Falling back to single-pass tailoring.")
        result, llm_request = tailor_documents(job, resume_template, cover_template)
        return result, llm_request, phase1_debug, None

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
