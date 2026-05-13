"""Top-level compiler pipeline: template + LLM text → output DOCX.

Two entry points:
- compile_resume(template_path, llm_text, output_path) — DOCX template on disk
- compile_resume_from_ir(template_ir, llm_text, output_path) — deserialized IR
  (used for PDF-sourced resumes; template_path is the CLI default DOCX for
   page geometry / style inheritance only).

Both entry points accept an optional *classification* (ClassificationOutput)
that constrains how apply_tailored updates section content.  When None the
pipeline behaves identically to before (fully backward-compatible).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tailor.compiler.docx_parser import parse_docx
from tailor.compiler.docx_renderer import render_docx
from tailor.compiler.layout import apply_layout_fitting
from tailor.compiler.models import ResumeDocument
from tailor.compiler.text_parser import parse_llm_output
from tailor.compiler.updater import apply_tailored

if TYPE_CHECKING:
    from tailor.compiler.classification_models import ClassificationOutput

log = logging.getLogger(__name__)


def compile_resume(
    template_path: str,
    llm_text: str,
    output_path: str,
    classification: "ClassificationOutput | None" = None,
) -> None:
    """Parse *template_path*, apply *llm_text*, render to *output_path*.

    Parameters
    ----------
    template_path:
        Path to the master resume DOCX template.
    llm_text:
        Plain-text LLM output (resume only, not cover letter).
    output_path:
        Destination path for the rendered DOCX.
    classification:
        Optional upload-time classification (ClassificationOutput).  When
        provided, section update behavior is constrained by rewrite_policy,
        preserve_heading, and preserve_body_structure.  None → existing behavior.

    Raises
    ------
    ValueError
        If section/role anchors in the LLM output don't match the template
        (see updater module).
    """
    original = parse_docx(template_path)
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(original, llm_sections)
    updated = apply_tailored(original, llm_sections, classification=classification)
    render_docx(updated, template_path, output_path)
    log.debug(
        "compile_resume: %d sections, %d total paras → %s",
        len(updated.sections),
        len(updated.all_paras),
        output_path,
    )
    return updated


def compile_resume_from_ir(
    template_ir: ResumeDocument,
    llm_text: str,
    output_path: str,
    style_template_path: str,
    classification: "ClassificationOutput | None" = None,
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
    classification:
        Optional upload-time classification (ClassificationOutput).  When
        provided, section update behavior is constrained by rewrite_policy,
        preserve_heading, and preserve_body_structure.  None → existing behavior.

    Raises
    ------
    ValueError
        If section/role anchors in the LLM output don't match the template IR.
    """
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(template_ir, llm_sections)
    updated = apply_tailored(template_ir, llm_sections, classification=classification)
    render_docx(updated, style_template_path, output_path)
    log.debug(
        "compile_resume_from_ir: %d sections, %d total paras → %s",
        len(updated.sections),
        len(updated.all_paras),
        output_path,
    )
    return updated


def _clear_pdf_content_colors(doc: ResumeDocument) -> None:
    """Strip text_color from all non-section-heading paragraphs in a PDF-sourced doc.

    PDF-extracted colors bleed onto LLM-generated content when the updater
    clones paragraphs from colored archetypes (e.g. section headings used as
    body-paragraph templates for empty sections, or role headers used as bullet
    archetypes).  Section headings retain their accent color; all other content
    uses the DOCX default text color.
    """
    from tailor.compiler.models import ParagraphProfile

    def _clear(paras):
        for pm in paras:
            if pm.semantic != "section_heading" and pm.paragraph_profile is not None:
                pm.paragraph_profile.text_color = None

    _clear(doc.header_paras)
    _clear(doc.all_paras)
    for sec in doc.sections:
        _clear(sec.body_paras)
        for role in sec.roles:
            _clear([role.header] + list(role.header_extra) + role.meta_lines + role.bullets)


def compile_resume_from_pdf(
    pdf_path: str,
    llm_text: str,
    output_path: str,
    style_template_path: str,
    classification: "ClassificationOutput | None" = None,
) -> ResumeDocument:
    """Parse *pdf_path* directly into IR, apply *llm_text*, render to *output_path*.

    *style_template_path* must be a DOCX file used only for page geometry.
    """
    from tailor.compiler.pdf_parser import parse_pdf

    with open(pdf_path, "rb") as f:
        template_ir = parse_pdf(f.read())
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(template_ir, llm_sections)
    updated = apply_tailored(template_ir, llm_sections, classification=classification)
    # Clear PDF-extracted text colors from all content paragraphs before rendering.
    # This prevents colors from the original PDF (hyperlink blues, author styling)
    # from bleeding onto LLM-generated replacement content via clone_as archetypes.
    _clear_pdf_content_colors(updated)
    render_docx(updated, style_template_path, output_path)
    return updated

