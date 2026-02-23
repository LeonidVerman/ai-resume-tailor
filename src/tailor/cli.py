"""Command-line interface for AI Resume Tailor."""

import argparse
import os

from tailor.config import COVER_TEMPLATE, OUTPUT_DIR, RESUME_TEMPLATE
from tailor.debug import save_debug_data
from tailor.diff import diff_resume_experience
from tailor.docx.pdf import docx_to_pdf
from tailor.docx.template_fill import read_docx, save_doc_from_template
from tailor.job import JobData
from tailor.job.scrape import scrape_job_url
from tailor.llm import extract_metadata_ai, tailor_documents
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

    # --- Tailor documents ---
    print("Tailoring documents...")
    result, llm_request = tailor_documents(job, resume_template, cover_template)

    diff = {}
    if result.resume:
        diff["resume"] = diff_resume_experience(resume_template, result.resume)

    save_debug_data(
        job.company,
        job.job_title,
        {"resume": result.resume, "cover_letter": result.cover_letter},
        llm_request,
        diff=diff or None,
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
        save_doc_from_template(COVER_TEMPLATE, cover_docx, result.cover_letter)
        cover_pdf = os.path.splitext(cover_docx)[0] + ".pdf"
        if _confirm_overwrite(cover_pdf, args.force):
            docx_to_pdf(cover_docx)

    print("Documents generated successfully.")
