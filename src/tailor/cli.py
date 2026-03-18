"""Command-line interface for AI Resume Tailor."""

import argparse
import json
import logging
import os
import sys

logger = logging.getLogger(__name__)

from tailor.config import ASSESS_MODEL, ASSESS_TEMPERATURE, COVER_TEMPLATE, OUTPUT_DIR, RESUME_TEMPLATE
from tailor.core_generation.llm import extract_metadata_ai, tailor_documents
from tailor.debug import save_debug_data
from tailor.diff import diff_resume
from tailor.docx.pdf import docx_to_pdf
from tailor.docx.template_fill import normalize_cover_letter, read_docx, save_doc_from_template
from tailor.job import JobData
from tailor.job.scrape import scrape_job_url
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
        "--workers", type=int, default=None, metavar="N",
        help="Number of parallel threads (default: min(positions, 20)).",
    )
    parser.add_argument(
        "--runs", type=int, default=1,
        help="(Reserved) Number of judge runs per position.",
    )
    parser.add_argument(
        "--judge_only_after_validation_fail", default="false",
        help="(Reserved) Future flag.",
    )
    parser.add_argument(
        "--calibrate", action="store_true", default=False,
        help=(
            "Calibration mode: send the unmodified master resume and cover letter "
            "to assessment instead of the tailored output. "
            "The cover letter date, company, and role are still substituted. "
            "All other assessment inputs (writer_packet, etc.) are unchanged."
        ),
    )
    parser.add_argument(
        "--calibrate-data", default=None, metavar="DIR", dest="calibrate_data",
        help=(
            "Calibration-with-data mode: assess pre-generated resume/cover letter "
            "files from DIR instead of the tailored output. "
            "Files must follow the naming convention "
            "<Name>_Resume_<Company>.docx / <Name>_Cover_Letter_<Company>.docx. "
            "Each position must have a matching pair; the run aborts if any are missing. "
            "Default samples directory: tests/samples. "
            "Mutually exclusive with --calibrate."
        ),
    )
    args = parser.parse_args(argv)

    if args.calibrate and args.calibrate_data:
        parser.error("--calibrate and --calibrate-data are mutually exclusive.")

    run_assess_pipeline(
        positions_file=args.positions,
        out_dir=args.out,
        model=args.model,
        temperature=args.temperature,
        max_positions=args.max_positions,
        cache_dir=args.cache_dir,
        runs=args.runs,
        workers=args.workers,
        calibrate=args.calibrate,
        calibrate_data=args.calibrate_data,
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
        except Exception:
            import traceback
            from tailor.job.scrape import get_scrape_failure_message
            traceback.print_exc()
            print(get_scrape_failure_message(args.position_url), file=sys.stderr)
            sys.exit(1)
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

    # --- Tailor documents ---
    print("Tailoring documents...")
    result, llm_request = tailor_documents(job, resume_template, cover_template)

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
