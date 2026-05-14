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
    """Normalize styling on LLM-generated content paragraphs in a PDF-sourced doc.

    Three problems are fixed here:

    1. Color bleed: PDF-extracted text_color (hyperlink blue, author styling)
       bleeds onto new content via clone_as when the updater copies it from a
       colored archetype.  Bullets and body paragraphs are reset to the DOCX
       default (no explicit color).  Section headings and role_headers keep their
       accent color — they are structural design elements, not replaced content.

    2. Heading-style bleed (oversized): when a section has no body paragraphs,
       the updater falls back to the section heading as clone archetype.  The
       heading carries bold=True and an elevated font_size_pt.  Any paragraph/
       bullet whose font_size_pt is more than 10% above the document default AND
       is bold is treated as a heading-clone artefact and normalized to body-text
       styling (bold=False, font_size_pt=default).

    3. Role-header bold bleed (same-size): when a role has no bullets, the updater
       uses orig.header as the bullet archetype.  Role headers are bold even when
       their font_size equals the document default, so new bullets inherit bold.
       Bullets are never legitimately bold in a resume, so bold is unconditionally
       cleared from all bullet semantics regardless of font size.
    """
    default_size = (doc.layout.default_font_size_pt if doc.layout else None) or 11.0

    def _fix(paras):
        for pm in paras:
            pp = pm.paragraph_profile
            if pp is None:
                continue
            # Strip PDF-extracted text colors from ALL paragraphs (including
            # section_heading and role_header).  LibreOffice has a rendering defect
            # where a paragraph with both an explicit w:color and w:ind inside a table
            # cell is not rendered — the text becomes invisible.  Since the PDF template
            # background image is not carried over, the original accent colors are
            # meaningless in the DOCX context anyway; all headings render in black.
            pp.text_color = None
            if pm.semantic == "bullet":
                # Bullets are never bold — clear unconditionally (fixes role-header
                # bold bleed when the role header is the only archetype available).
                pp.bold = False
            elif pm.semantic == "paragraph":
                # Normalize heading-style bleed: bold + oversized font on body
                # content means this paragraph was cloned from a heading archetype.
                if pp.bold and pp.font_size_pt and pp.font_size_pt > default_size * 1.1:
                    pp.bold = False
                    pp.font_size_pt = default_size

    _fix(doc.header_paras)
    _fix(doc.all_paras)
    for sec in doc.sections:
        _fix(sec.body_paras)
        for role in sec.roles:
            _fix([role.header] + list(role.header_extra) + role.meta_lines + role.bullets)


def _fix_extra_left_sections(template_ir: ResumeDocument, updated: ResumeDocument) -> None:
    """Reassign extra LLM sections from left column to right column.

    When apply_tailored creates sections not present in the template (e.g. a
    Professional Summary for a template that has none), it uses the first
    template section as an archetype.  For sidebar-layout templates the first
    section is in the left column, so all new section content inherits
    column_id='left' and left-column indents — placing it inside the narrow
    sidebar instead of the main content area.

    This function moves any section whose heading has column_id='left' but
    whose normalised title is absent from the template's left-column section
    titles into the right column, resetting indents to match the template's
    first right-column section.
    """
    if template_ir.layout.column_split_x is None:
        return

    def _norm(s: str) -> str:
        return s.lower().strip()

    template_left_titles = {
        _norm(sec.title)
        for sec in template_ir.sections
        if sec.heading.paragraph_profile
        and sec.heading.paragraph_profile.column_id == "left"
    }

    # Reference indents from the template's first right-column section.
    right_heading_indent = 0.0
    right_body_indent = 0.0
    for sec in template_ir.sections:
        h_pp = sec.heading.paragraph_profile
        if h_pp and h_pp.column_id == "right":
            right_heading_indent = h_pp.indent_left_pt
            for bp in sec.body_paras:
                if bp.paragraph_profile:
                    right_body_indent = bp.paragraph_profile.indent_left_pt
                    break
            if right_body_indent == 0.0 and sec.roles:
                rpp = sec.roles[0].header.paragraph_profile
                if rpp:
                    right_body_indent = rpp.indent_left_pt
            break

    def _move(pm, indent: float) -> None:
        pp = pm.paragraph_profile
        if pp is not None and pp.column_id == "left":
            pp.column_id = "right"
            pp.indent_left_pt = indent

    for sec in updated.sections:
        h_pp = sec.heading.paragraph_profile
        if not (h_pp and h_pp.column_id == "left"):
            continue
        if _norm(sec.title) in template_left_titles:
            continue
        _move(sec.heading, right_heading_indent)
        for bp in sec.body_paras:
            _move(bp, right_body_indent)
        for role in sec.roles:
            _move(role.header, right_body_indent)
            for pm in list(role.header_extra) + role.meta_lines + role.bullets:
                _move(pm, right_body_indent)


def _inject_llm_summary_into_header(doc: ResumeDocument) -> None:
    """Promote LLM-injected Professional Summary into the merged header area.

    Templates like sample 18 have a full-width header (name, title, summary)
    above the two-column body.  pdf_parser places the original summary lines in
    header_paras.  apply_tailored then injects the LLM's "Professional Summary"
    as a left-column section (the archetype is the first left-column section).

    This function moves the LLM summary body into header_paras (col_id=None so
    the renderer places it above the two-column table), replaces the original
    template summary lines, and removes the injected section so its heading is
    not rendered as a left-column "PROFESSIONAL SUMMARY" banner.

    Only runs for two-column PDF docs where header_paras contain original
    summary text beyond the name/title lines.
    """
    if doc.layout.column_split_x is None:
        return

    SUMMARY_TITLES = frozenset({"professional summary", "summary", "profile", "objective"})
    summary_idx: int | None = None
    for i, sec in enumerate(doc.sections):
        if sec.title.lower().strip() in SUMMARY_TITLES and sec.body_paras:
            summary_idx = i
            break
    if summary_idx is None:
        return

    summary_sec = doc.sections[summary_idx]
    default_size = doc.layout.default_font_size_pt or 11.0

    # Split header_paras into name/title lines (large font or bold) and the
    # original-template summary lines (body-sized, not bold) that follow them.
    name_title_end = 0
    for j, hp in enumerate(doc.header_paras):
        pp = hp.paragraph_profile
        if pp and ((pp.font_size_pt or 0) > default_size * 1.2 or pp.bold):
            name_title_end = j + 1

    # Skip if there are no original summary lines to replace.
    if name_title_end >= len(doc.header_paras):
        return

    # Lift LLM summary body paragraphs into the above-table header area.
    for bp in summary_sec.body_paras:
        if bp.paragraph_profile:
            bp.paragraph_profile.column_id = None

    doc.header_paras = doc.header_paras[:name_title_end] + list(summary_sec.body_paras)
    doc.sections = [s for i, s in enumerate(doc.sections) if i != summary_idx]

    # Rebuild all_paras so the renderer sees the updated structure.
    from tailor.compiler.models import ParaModel
    new_all: list[ParaModel] = list(doc.header_paras)
    for sec in doc.sections:
        new_all.append(sec.heading)
        new_all.extend(sec.body_paras)
        for role in sec.roles:
            new_all.append(role.header)
            new_all.extend(role.header_extra)
            new_all.extend(role.meta_lines)
            new_all.extend(role.bullets)

    def _col_order(pm) -> int:
        col = pm.paragraph_profile.column_id if pm.paragraph_profile else None
        return 1 if col == "left" else (2 if col == "right" else 0)

    new_all.sort(key=_col_order)
    doc.all_paras = new_all


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
    # For two-column templates with a full-width header: move the LLM-injected
    # Professional Summary body into header_paras so it renders above the table.
    _inject_llm_summary_into_header(updated)
    # Move extra LLM sections (e.g. Professional Summary) out of the left sidebar
    # column for two-column PDF templates that have no matching left-column section.
    _fix_extra_left_sections(template_ir, updated)
    render_docx(updated, style_template_path, output_path)
    return updated

