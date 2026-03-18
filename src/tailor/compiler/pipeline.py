"""Top-level compiler pipeline: template + LLM text → output DOCX.

This is the single entry point used by save_doc_from_template.
"""
from __future__ import annotations

import logging

from tailor.compiler.docx_parser import parse_docx
from tailor.compiler.docx_renderer import render_docx
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
    updated = apply_tailored(original, llm_sections)
    render_docx(updated, template_path, output_path)
    log.debug(
        "compile_resume: %d sections, %d total paras → %s",
        len(updated.sections),
        len(updated.all_paras),
        output_path,
    )
