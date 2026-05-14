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

    Templates like sample 18/19 have a full-width header (name, title, contact,
    summary) above the two-column body.  pdf_parser places the original summary
    lines in header_paras.  apply_tailored then injects the LLM's "Professional
    Summary" as a left-column section (archetype = first left-column section).

    This function:
    - Identifies original template summary lines in header_paras using a
      heuristic: long descriptive sentences that are NOT contact info (email,
      phone, url, address digits, license labels, all-caps short headers).
    - Removes those lines and adds the LLM summary body (col_id=None) so the
      renderer places it above the two-column table.
    - Removes the injected section so its heading is not rendered as a banner.

    Only runs for two-column PDF docs that have a LLM-injected summary section
    AND at least one original summary line in header_paras.
    """
    import re

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

    def _is_original_summary_line(text: str) -> bool:
        """Return True if this header_para looks like a template summary line.

        Rejects contact info (email, phone digits, url, address numbers,
        license-label lines) and very short lines — those should stay in the
        header.  Long descriptive sentences about the candidate's experience
        are treated as the original summary that the LLM should replace.
        """
        t = text.strip()
        if len(t) < 25:
            return False
        if "@" in t:                                          # email address
            return False
        if re.search(r"\d[\d\s.()\-]{5,}", t):               # phone-like digit run
            return False
        if re.search(r"\b\d{4}\b", t):                       # 4-digit year/zip/id
            return False
        if any(k in t.lower() for k in ("linkedin", "http", "www.", ".com", ".net", ".org")):
            return False
        if re.match(r"[A-Z][A-Z\s]+NO\.?\s", t):             # "LICENSE NO." style label
            return False
        if re.match(r"^[A-Z\s]+$", t) and len(t) < 40:      # short all-caps heading
            return False
        return True

    summary_indices = {
        j for j, hp in enumerate(doc.header_paras)
        if _is_original_summary_line(hp.text)
    }
    if not summary_indices:
        return  # no original summary lines to replace

    summary_sec = doc.sections[summary_idx]

    # Lift LLM summary body paragraphs into the above-table header area.
    for bp in summary_sec.body_paras:
        if bp.paragraph_profile:
            bp.paragraph_profile.column_id = None
            bp.paragraph_profile.bold = False  # summary text is never bold

    doc.header_paras = (
        [hp for j, hp in enumerate(doc.header_paras) if j not in summary_indices]
        + list(summary_sec.body_paras)
    )
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


def _remove_orphan_subsections(doc: ResumeDocument) -> None:
    """Remove PDF-parser sub-entry sections that duplicate LLM-injected roles.

    The PDF parser classifies bold role/affiliation headings (e.g. "Back-End
    Developer", "Yellow Tree Organization") as section headings.  This creates
    orphan sections separate from their parent category section (WORK EXPERIENCE,
    AFFILIATIONS).  After apply_tailored injects LLM content into the parent
    section's roles, the orphan sections remain with stale original content,
    causing visible duplication in the rendered output.

    A section is treated as an orphan sub-entry when:
    - Its title is NOT predominantly upper-case (upper-ratio < 0.70) — real
      category headings like WORK EXPERIENCE, AFFILIATIONS are all-caps.
    - At least one body_para is bold — original role/org lines are bold;
      LLM-injected body content is not bold.
    - Its column contains at least one upper-case category section — so we do
      not accidentally drop sections in a column that has only mixed-case titles.

    Additionally this function:
    - Removes bold body_paras from sections with roles (original role-header
      lines that were also parsed as roles, causing double-rendering).
    - Clears meta_lines for roles whose header uses the pipe-separated LLM
      format (company+dates already in the header; cloned meta_lines are stale).
    """
    if doc.layout.column_split_x is None:
        return

    def _is_category_heading(title: str) -> bool:
        t = title.strip()
        if not t:
            return True
        alpha = [c for c in t if c.isalpha()]
        if not alpha:
            return True
        return sum(1 for c in alpha if c.isupper()) / len(alpha) >= 0.70

    def _col_of(sec) -> "str | None":
        pp = sec.heading.paragraph_profile
        return pp.column_id if pp else None

    # Which columns have at least one ALL-CAPS category section?
    cols_with_category: "set[str | None]" = {
        _col_of(s) for s in doc.sections if _is_category_heading(s.title)
    }

    def _is_orphan(sec) -> bool:
        if _is_category_heading(sec.title):
            return False
        if _col_of(sec) not in cols_with_category:
            return False
        # Only remove if at least one body_para is bold — template sub-entries
        # have bold company/date lines; LLM-injected sections do not.
        return any(
            bp.paragraph_profile and bp.paragraph_profile.bold
            for bp in sec.body_paras
        )

    orphan_idxs = {i for i, s in enumerate(doc.sections) if _is_orphan(s)}
    if not orphan_idxs:
        return

    doc.sections = [s for i, s in enumerate(doc.sections) if i not in orphan_idxs]

    # For sections that have roles, remove ALL body_paras.  In PDF-sourced
    # experience sections, body_paras contain original role-header lines (bold)
    # and original role bullets (non-bold), both of which are already encoded
    # inside the role objects.  Keeping them causes duplicate rendering before
    # the LLM-injected roles.  Also clear stale cloned meta_lines from roles
    # whose header already carries the pipe-separated company+date string.
    for sec in doc.sections:
        if sec.roles:
            sec.body_paras = []
            for role in sec.roles:
                if role.header.text and "|" in role.header.text:
                    role.meta_lines.clear()

    # Rebuild all_paras to reflect removed sections and cleared content.
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

    def _col_order(pm: ParaModel) -> int:
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
    # Remove orphan sections created by the PDF parser from bold role/affiliation
    # headings that duplicate LLM-injected content in the parent section.
    _remove_orphan_subsections(updated)
    # Move extra LLM sections (e.g. Professional Summary) out of the left sidebar
    # column for two-column PDF templates that have no matching left-column section.
    _fix_extra_left_sections(template_ir, updated)
    render_docx(updated, style_template_path, output_path)
    return updated

