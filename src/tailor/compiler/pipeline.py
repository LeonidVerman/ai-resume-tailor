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
import re
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
    """Clean up PDF-parser sub-entry sections misclassified as top-level sections.

    The PDF parser classifies bold role/affiliation headings (e.g. "Back-End
    Developer", "Yellow Tree Organization") as section headings, creating orphan
    sections separate from their parent (WORK EXPERIENCE, AFFILIATIONS).

    Identification heuristic — a section is an orphan when:
    - Its title is NOT predominantly upper-case (ratio < 0.70).
    - At least one body_para is bold (original template sub-entries have bold
      company/date lines; LLM-injected sections do not).
    - Its column contains at least one ALL-CAPS category section.

    Treatment by column:
    - LEFT-column orphans (experience sub-entries such as "Back-End Developer"):
      removed.  The LLM-injected roles in WORK EXPERIENCE already carry the
      correct content.
    - RIGHT-column orphans (affiliation/reference sub-entries such as "Yellow
      Tree Organization"): absorbed into the LAST ALL-CAPS right-column section
      (typically AFFILIATIONS).  Their bold org-name headings are preserved as
      body_paras, restoring the original formatting and replacing the LLM-
      overwritten body content with the original template entries.

    Additionally:
    - For sections with roles, all body_paras are cleared (original role-header
      lines and bullets already encoded in role objects cause double-rendering).
    - Stale cloned meta_lines are cleared for pipe-format role headers (company
      and dates are already in the header; the meta_line copy is redundant).
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

    left_orphan_idxs = {
        i for i, s in enumerate(doc.sections)
        if _is_orphan(s) and _col_of(s) == "left"
    }
    right_orphan_idxs = {
        i for i, s in enumerate(doc.sections)
        if _is_orphan(s) and _col_of(s) == "right"
    }
    orphan_idxs = left_orphan_idxs | right_orphan_idxs

    # Clear stale body_paras for sections that have role objects.  PDF-sourced
    # experience sections often have body_paras containing the original role-header
    # lines and bullets (not recognised as role objects due to non-standard
    # formatting) alongside proper role objects for entries the parser DID parse.
    # Keeping both causes duplicate or misplaced rendering; since the role objects
    # carry the LLM-updated content, the body_paras are redundant.
    # Run unconditionally (before the orphan early-return) so section-row table
    # layouts with no orphans still benefit from this cleanup.
    _body_cleared = False
    for sec in doc.sections:
        if sec.roles and sec.body_paras:
            sec.body_paras = []
            _body_cleared = True
            for role in sec.roles:
                if role.header.text and "|" in role.header.text:
                    role.meta_lines.clear()

    if not orphan_idxs:
        if _body_cleared:
            # Rebuild all_paras to reflect the cleared body_paras.
            from tailor.compiler.models import ParaModel as _PM
            new_all: "list[_PM]" = list(doc.header_paras)
            for sec in doc.sections:
                new_all.append(sec.heading)
                new_all.extend(sec.body_paras)
                for role in sec.roles:
                    new_all.append(role.header)
                    new_all.extend(role.header_extra)
                    new_all.extend(role.meta_lines)
                    new_all.extend(role.bullets)

            def _col_ord(pm: "_PM") -> int:
                col = pm.paragraph_profile.column_id if pm.paragraph_profile else None
                return 1 if col == "left" else (2 if col == "right" else 0)

            new_all.sort(key=_col_ord)
            doc.all_paras = new_all
        return

    # Right-column orphans are affiliation/reference sub-entries with bold
    # org-name headings (e.g. "Yellow Tree Organization").  Rather than
    # discarding them, absorb them into the LAST ALL-CAPS right-column section
    # (typically AFFILIATIONS), restoring the bold org-name headers and the
    # original template content instead of the LLM-overwritten version.
    if right_orphan_idxs:
        right_cat_secs = [
            (i, s) for i, s in enumerate(doc.sections)
            if _col_of(s) == "right"
            and _is_category_heading(s.title)
            and i not in orphan_idxs
        ]
        if right_cat_secs:
            _, parent_sec = right_cat_secs[-1]   # last ALL-CAPS right section
            absorbed: list = []
            for i in sorted(right_orphan_idxs):
                orphan = doc.sections[i]
                absorbed.append(orphan.heading)  # bold org-name heading
                absorbed.extend(orphan.body_paras)
            # Replace LLM-injected content with the original template sub-entries.
            parent_sec.body_paras = absorbed

    doc.sections = [s for i, s in enumerate(doc.sections) if i not in orphan_idxs]

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


_CONTACT_FOOTER_RE = re.compile(
    r"[@]|\d{3,}|https?://|www\.", re.IGNORECASE
)


def _strip_template_footer_bullets(template_ir: ResumeDocument) -> None:
    """Remove contact/footer items that ended up as role bullets in template IR.

    Single-page PDFs don't trigger the header/footer deduplication pass, so
    phone numbers, emails, and addresses at the page bottom can land inside the
    last role's bullet list.  Keeping them pollutes bullet archetypes for the
    LLM-generated content (wrong size, indent, and italic).
    """
    for sec in template_ir.sections:
        for role in sec.roles:
            cleaned = [b for b in role.bullets if not _CONTACT_FOOTER_RE.search(b.text)]
            if len(cleaned) < len(role.bullets):
                role.bullets = cleaned


def _normalize_bullet_styles(doc: ResumeDocument) -> None:
    """Make bullet font size and italic consistent within each section.

    For PDF-sourced documents, bullets may have mixed styling depending on
    which template paragraph was used as the clone archetype:
    - Roles with original bullets: bullets inherit the template bullet's size/italic.
    - Roles with NO original bullets: bullets inherit the role-header size (larger)
      and non-italic, giving them a visually inconsistent appearance.

    Fix: per-section, compute the canonical bullet size (mode of sizes ≤ document
    default × 1.1) and canonical italic (majority vote among normally-sized bullets),
    then apply to all bullets in that section.
    """
    if doc.source_kind != "pdf":
        return
    default_size = (doc.layout.default_font_size_pt if doc.layout else None) or 11.0
    _size_ceil = default_size * 1.1

    from collections import Counter

    for sec in doc.sections:
        all_bullets = [b for role in sec.roles for b in role.bullets if b.paragraph_profile]
        if not all_bullets:
            continue

        # Canonical size: most common size among normally-sized bullets.
        normal_sizes = [
            b.paragraph_profile.font_size_pt for b in all_bullets
            if b.paragraph_profile.font_size_pt and b.paragraph_profile.font_size_pt <= _size_ceil
        ]
        canonical_size = (
            Counter(normal_sizes).most_common(1)[0][0] if normal_sizes else default_size
        )

        # Canonical italic: majority vote from normally-sized bullets.
        normal_italics = [
            b.paragraph_profile.italic for b in all_bullets
            if b.paragraph_profile.font_size_pt and b.paragraph_profile.font_size_pt <= _size_ceil
        ]
        canonical_italic = (
            bool(sum(normal_italics) > len(normal_italics) / 2) if normal_italics else False
        )

        for b in all_bullets:
            pp = b.paragraph_profile
            if pp is None:
                continue
            if pp.font_size_pt and pp.font_size_pt > _size_ceil:
                pp.font_size_pt = canonical_size
            pp.italic = canonical_italic

        # Per-role bullet indent normalization: align each role's bullets to
        # that role's own header indent.  Template roles may have bullets at
        # different x-positions in the source PDF (e.g. one role at 27 pt,
        # another at 43 pt), causing visual inconsistency after cloning.
        for role in sec.roles:
            header_pp = role.header.paragraph_profile
            if header_pp is None:
                continue
            target_indent = header_pp.indent_left_pt
            for b in role.bullets:
                bpp = b.paragraph_profile
                if bpp is not None and abs(bpp.indent_left_pt - target_indent) > 2.0:
                    bpp.indent_left_pt = target_indent


def _apply_heading_case_convention(doc: ResumeDocument) -> None:
    """Apply the template's section-heading capitalisation style to all sections.

    Detects whether the majority (≥ 50 %) of existing section headings are
    ALL-CAPS or Title-Case and transforms any outliers to match.  This ensures
    LLM-injected sections ('Technical Skills', 'Additional') follow the same
    visual style as template sections ('GENERAL INFO', 'WORK HISTORY').
    """
    if doc.source_kind != "pdf":
        return
    titles = [s.title.strip() for s in doc.sections if s.title.strip()]
    if not titles:
        return

    all_caps = sum(1 for t in titles if t == t.upper())
    # Only apply a convention when the majority clearly agree.
    if all_caps / len(titles) >= 0.5:
        for sec in doc.sections:
            t = sec.title.strip()
            if t and t != t.upper():
                sec.title = t.upper()
                sec.heading = sec.heading.with_text(t.upper())


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
    # Remove contact/footer items (phone, email) that landed in role bullets on
    # single-page PDFs — they would otherwise become LLM bullet archetypes and
    # produce wrong size, indent, and italic on generated bullets.
    _strip_template_footer_bullets(template_ir)
    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(template_ir, llm_sections)
    updated = apply_tailored(template_ir, llm_sections, classification=classification)

    # Clear PDF-extracted text colors from all content paragraphs before rendering.
    _clear_pdf_content_colors(updated)
    # For two-column templates with a full-width header: move the LLM-injected
    # Professional Summary body into header_paras so it renders above the table.
    _inject_llm_summary_into_header(updated)
    # Remove orphan sections and clear stale body_paras for sections with roles.
    _remove_orphan_subsections(updated)
    # Move extra LLM sections (e.g. Professional Summary) out of the left sidebar
    # column for two-column PDF templates that have no matching left-column section.
    _fix_extra_left_sections(template_ir, updated)
    # Normalize bullet font size and italic within each section so all bullets
    # share the same style regardless of which archetype was used for cloning.
    _normalize_bullet_styles(updated)
    # Apply the template's section-heading capitalisation convention to LLM-injected
    # sections (e.g. 'Technical Skills' → 'TECHNICAL SKILLS' when all template
    # section headings are ALL-CAPS).
    _apply_heading_case_convention(updated)

    # Re-sort sections by (column, y_top_pt) for PDF two-column documents.
    # Done AFTER _fix_extra_left_sections so LLM-injected extra sections (e.g.
    # Technical Skills) already have col_id="right" and sort correctly after
    # the template's left-column sections rather than interleaving with them.
    if (
        updated.source_kind == "pdf"
        and updated.layout.column_split_x is not None
    ):
        from tailor.compiler.models import ParaModel as _ParaModel  # local import

        def _sec_col_y_key(sec) -> "tuple[int, float]":
            pp = sec.heading.paragraph_profile
            col_order = (
                1 if (pp and pp.column_id == "left")
                else 2 if (pp and pp.column_id == "right")
                else 0
            )
            return (col_order, pp.y_top_pt if pp else 0.0)

        updated.sections.sort(key=_sec_col_y_key)

        if not updated.layout.section_row_table:
            # Re-order all_paras to reflect the new section order while preserving
            # each para's existing position within its section.  Rebuilding from
            # scratch would add body_paras that apply_tailored excluded, causing
            # duplicate content.  Instead we map each existing para to its section
            # index (post-sort) and use its original all_paras position as tiebreaker.
            sec_order: "dict[int, int]" = {}
            for si, sec in enumerate(updated.sections):
                sec_order[id(sec.heading)] = si
                for pm in sec.body_paras:
                    sec_order[id(pm)] = si
                for role in sec.roles:
                    for pm in [role.header, *role.header_extra, *role.meta_lines, *role.bullets]:
                        sec_order[id(pm)] = si

            orig_pos = {id(pm): i for i, pm in enumerate(updated.all_paras)}

            updated.all_paras.sort(
                key=lambda pm: (sec_order.get(id(pm), -1), orig_pos.get(id(pm), 0))
            )

    render_docx(updated, style_template_path, output_path)
    return updated

