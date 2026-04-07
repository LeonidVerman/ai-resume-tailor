"""Top-level compiler pipeline: template + LLM text → output DOCX.

Two entry points:
- compile_resume(template_path, llm_text, output_path) — DOCX template on disk
- compile_resume_from_ir(template_ir, llm_text, output_path) — deserialized IR
  (used for PDF-sourced resumes; template_path is the CLI default DOCX for
   page geometry / style inheritance only).
"""
from __future__ import annotations

import logging

from tailor.compiler.docx_parser import parse_docx
from tailor.compiler.docx_renderer import render_docx
from tailor.compiler.layout import apply_layout_fitting
from tailor.compiler.models import ResumeDocument
from tailor.compiler.text_parser import parse_llm_output
from tailor.compiler.updater import apply_tailored

log = logging.getLogger(__name__)


def compile_resume(template_path: str, llm_text: str, output_path: str) -> None:
    """Parse *template_path*, apply *llm_text*, render to *output_path*.

    Parameters
    ----------
    template_path:
        Path to the master resume DOCX template.
    llm_text:
        Plain-text LLM output (resume only, not cover letter).
    output_path:
        Destination path for the rendered DOCX.

    Raises
    ------
    ValueError
        If section/role anchors in the LLM output don't match the template
        (see updater module).
    """
    original = parse_docx(template_path)
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(original, llm_sections)
    updated = apply_tailored(original, llm_sections)
    render_docx(updated, template_path, output_path)
    log.debug(
        "compile_resume: %d sections, %d total paras → %s",
        len(updated.sections),
        len(updated.all_paras),
        output_path,
    )


def compile_resume_from_ir(
    template_ir: ResumeDocument,
    llm_text: str,
    output_path: str,
    style_template_path: str,
) -> None:
    """Apply *llm_text* to a pre-parsed *template_ir* and render to *output_path*.

    Used for PDF-sourced resumes where the template IR was serialized at upload
    time and stored in the database.

    Parameters
    ----------
    template_ir:
        Deserialized ResumeDocument (source_kind='pdf').
    llm_text:
        Plain-text LLM output (resume only, not cover letter).
    output_path:
        Destination path for the rendered DOCX.
    style_template_path:
        Path to a DOCX file used only for page geometry / style inheritance
        (e.g. the CLI default resume template).  Content is stripped; PDF-
        sourced paragraphs are rendered via para_builder.

    Raises
    ------
    ValueError
        If section/role anchors in the LLM output don't match the template IR.
    """
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(template_ir, llm_sections)
    updated = apply_tailored(template_ir, llm_sections)
    render_docx(updated, style_template_path, output_path)
    log.debug(
        "compile_resume_from_ir: %d sections, %d total paras → %s",
        len(updated.sections),
        len(updated.all_paras),
        output_path,
    )

