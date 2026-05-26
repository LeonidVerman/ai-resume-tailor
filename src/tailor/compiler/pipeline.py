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
            if pm.semantic in ("section_heading", "role_header"):
                continue  # keep template accent colors on structural headings
            # Keep text_color for paragraphs on a dark background (e.g. white text
            # on the header band) — stripping it would make the text invisible.
            bg = pp.background_color
            if bg and bg not in ("ffffff", "fefefe", "f8f8f8"):
                continue
            # Strip color from LLM-replaced content (bullets, body paragraphs, meta).
            # PDF-extracted colors bleed onto new content via clone_as; clearing them
            # ensures body text renders in the default DOCX color.
            pp.text_color = None
            if pm.semantic == "bullet":
                # Bullets are never bold — clear unconditionally (fixes role-header
                # bold bleed when the role header is the only archetype available).
                pp.bold = False
            elif pm.semantic == "paragraph":
                # Normalize heading-style bleed: bold + oversized font on
                # LLM-injected body content (para_id="") means the paragraph
                # was cloned from a heading or all-bold archetype.
                # Original template paragraphs (non-empty para_id) keep their
                # font so that candidate names (e.g. 42pt "Alexander") are
                # not reduced to body-text size.
                if not pm.para_id:
                    # LLM-injected: clear bold unconditionally
                    pp.bold = False
                    if pp.font_size_pt and pp.font_size_pt > default_size * 1.1:
                        pp.font_size_pt = default_size

    _fix(doc.header_paras)
    _fix(doc.all_paras)

    _LIGHT_BG_SET = frozenset(("ffffff", "fefefe", "f8f8f8"))

    # Apply center alignment to large-font header paragraphs on a dark background
    # (e.g. "CHARLES MCTURLAND" on the dark header bar in sample 3).  These
    # typically appear centred in the original template but alignment is not
    # reliably extracted from PDF spans.  Threshold: 20pt+ font AND dark fill.
    # Also ensure white text color on dark backgrounds: PDF parsers often fail to
    # extract the white color for dark-background text, leaving text_color=None
    # which renders as default (black) — invisible on a dark band.
    for _pm in doc.header_paras:
        _pp = _pm.paragraph_profile
        if _pp and _pp.background_color and _pp.background_color not in _LIGHT_BG_SET:
            if _pp.font_size_pt and _pp.font_size_pt >= 20.0:
                _pp.alignment = "center"
            # Set white text on dark background when no color was extracted
            if _pp.text_color is None:
                _pp.text_color = "ffffff"

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
    # Also keep sections that were originally left-column by section_id even
    # when apply_tailored changed the heading title (e.g. "Skill" → "Technical
    # Skills").  Without this, _fix_extra_left_sections would move the updated
    # left-column section to the right because its new title is not in
    # template_left_titles.
    template_left_section_ids = {
        sec.section_id
        for sec in template_ir.sections
        if sec.heading.paragraph_profile
        and sec.heading.paragraph_profile.column_id == "left"
        and sec.section_id
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

    # Semantic types that belong in the sidebar (left column) are only kept
    # there when the template ITSELF already has sidebar-type (non-main-content)
    # sections in the left column.  If the left column holds main content
    # (experience, education) rather than a sidebar, skills-type extras are
    # moved to the right column just like other unmatched extras.
    _template_has_left_sidebar = any(
        sec.semantic_type in ("skills", "other", "certifications", "languages")
        for sec in template_ir.sections
        if sec.heading.paragraph_profile
        and sec.heading.paragraph_profile.column_id == "left"
    )
    _SIDEBAR_SEMANTIC_TYPES = (
        frozenset({"skills", "certifications", "languages"})
        if _template_has_left_sidebar
        else frozenset()
    )

    for sec in updated.sections:
        h_pp = sec.heading.paragraph_profile
        if not (h_pp and h_pp.column_id == "left"):
            continue
        if _norm(sec.title) in template_left_titles:
            continue
        if sec.section_id and sec.section_id in template_left_section_ids:
            continue  # originally a left-column section — keep it there
        if sec.semantic_type in _SIDEBAR_SEMANTIC_TYPES:
            continue  # sidebar content type — always stays in left column
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

    # Match by semantic_type first so section titles like "GENERAL INFO" that
    # carry semantic_type="summary" are found even when not in SUMMARY_TITLES.
    _SUMMARY_TITLES = frozenset({"professional summary", "summary", "profile", "objective"})
    summary_idx: int | None = None
    for i, sec in enumerate(doc.sections):
        is_summary = (
            sec.semantic_type == "summary"
            or sec.title.lower().strip() in _SUMMARY_TITLES
        )
        # Only inject LLM-extra sections (no section_id).  Original template
        # sections (section_id set) already occupy their correct body position
        # and must not be promoted into the header band.
        if is_summary and sec.body_paras and not sec.section_id:
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
        if re.match(r"^\d", t):                               # street address (house number)
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

    # Build column-aware summary index sets.  For sidebar layouts the original
    # summary text lives in the left column; for merged-header layouts it lives
    # in the right column (below the candidate name).  Prefer the left-column
    # match so that sidebar templates (e.g. sample 25) inject the LLM summary
    # into the sidebar and clean up old template placeholder text there, rather
    # than mixing left and right columns and producing an ambiguous target_col.
    _sum_left = {
        j for j, hp in enumerate(doc.header_paras)
        if hp.paragraph_profile and hp.paragraph_profile.column_id == "left"
        and _is_original_summary_line(hp.text)
    }
    _sum_right = {
        j for j, hp in enumerate(doc.header_paras)
        if hp.paragraph_profile and hp.paragraph_profile.column_id == "right"
        and _is_original_summary_line(hp.text)
    }
    # Also detect full-width (col=None) original summary lines that appear in
    # templates where the header is above the two-column body or single-column.
    _sum_none = {
        j for j, hp in enumerate(doc.header_paras)
        if hp.paragraph_profile and hp.paragraph_profile.column_id is None
        and _is_original_summary_line(hp.text)
    }

    if _sum_left:
        summary_indices: set[int] = _sum_left
        _target_col: "str | None" = "left"
    elif _sum_right:
        summary_indices = _sum_right
        _target_col = "right"
    elif _sum_none:
        summary_indices = _sum_none
        _target_col = None  # full-width above table or single-column
    else:
        summary_indices = set()
        _target_col = None  # determined below

    summary_sec = doc.sections[summary_idx]

    if not summary_indices:
        # No original summary lines to replace.  For single-column templates
        # keep the LLM summary as a body section (no header injection needed).
        if doc.layout.column_split_x is None:
            return
        # Two-column: no pre-existing summary → append LLM summary BELOW the
        # name/title block so it renders above the two-column table.
        if not doc.header_paras:
            return
        # Inherit the column from existing header content so the summary lands
        # in the right cell when the name/title are in the right column (e.g.
        # sidebar templates where name is right-aligned, like sample 2 & 3),
        # or above the table when the header is truly full-width (like sample 14).
        _existing_cols = [
            hp.paragraph_profile.column_id
            for hp in doc.header_paras
            if hp.paragraph_profile and hp.paragraph_profile.column_id in ("left", "right")
        ]
        if "right" in _existing_cols:
            _target_col = "right"
        elif "left" in _existing_cols:
            _target_col = "left"
        else:
            _target_col = None  # place above the two-column table

        for bp in summary_sec.body_paras:
            if bp.paragraph_profile:
                bp.paragraph_profile.column_id = _target_col
                bp.paragraph_profile.bold = False
        doc.header_paras = list(doc.header_paras) + list(summary_sec.body_paras)
        doc.sections = [s for i, s in enumerate(doc.sections) if i != summary_idx]
    else:

        # Lift LLM summary body paragraphs into the header area.
        # When placing in the right column, normalise indent_left_pt to 0 so the
        # summary starts flush with the right-column content below it (matching the
        # indentation of WORK EXPERIENCE body text) rather than inheriting a deeper
        # indent from the heading archetype used to clone the body paragraphs.
        for bp in summary_sec.body_paras:
            if bp.paragraph_profile:
                bp.paragraph_profile.column_id = _target_col
                bp.paragraph_profile.bold = False  # summary text is never bold
                if _target_col == "right":
                    # Flush with the right-column content and centre-align to
                    # match the typical template style for summary paragraphs.
                    bp.paragraph_profile.indent_left_pt = 0.0
                    bp.paragraph_profile.alignment = "center"

        if _target_col == "left":
            # Sidebar layout: the summary replaces the original profile text in the
            # left column.  Also remove any remaining original left-column AND
            # right-column header items that are template placeholders — dates,
            # old section headings, experience fragments.  Items with para_id set
            # are from the original template; items with para_id='' were created
            # by apply_tailored and must be kept.
            # Exception: preserve original items that are clearly the candidate
            # name/title (large font ≥ 20pt, or above-table col=None items).
            # These must appear in the rendered output regardless of column.
            # All other original col=left/right template placeholders are dropped
            # (dates, old education fragments, experience descriptions).
            _kept = []
            for j, hp in enumerate(doc.header_paras):
                if j in summary_indices:
                    continue
                pp = hp.paragraph_profile
                col = pp.column_id if pp else None
                if pp and hp.para_id and col in ("left", "right"):
                    # Keep only very large items (name, full-page title).
                    # 20pt threshold separates names (~24-40pt) from body text.
                    pp_size = pp.font_size_pt or 0.0
                    if pp_size >= 20.0 and hp.text.strip():
                        _kept.append(hp)
                    # else: original placeholder — drop
                else:
                    # col=None (above-table) items always kept; LLM items (para_id='') kept
                    _kept.append(hp)
            doc.header_paras = _kept + list(summary_sec.body_paras)
            doc.sections = [s for i, s in enumerate(doc.sections) if i != summary_idx]
        elif _target_col == "right":
            # Right-column injection.
            # After removing the matched summary lines, also drop any remaining
            # col=right original items that are very short and look like leftover
            # sentence fragments from the template summary (e.g. "experiences.").
            # Keep: the first non-empty col=right item (the candidate name) and
            # any item that is col=None (above-table) or LLM-generated (para_id='').
            _first_right_kept = False
            kept: list = []
            for j, hp in enumerate(doc.header_paras):
                if j in summary_indices:
                    continue
                pp = hp.paragraph_profile
                col = pp.column_id if pp else None
                if col == "right" and hp.para_id:
                    if not _first_right_kept and hp.text.strip():
                        _first_right_kept = True
                        kept.append(hp)
                    elif len(hp.text.strip()) >= 25:
                        kept.append(hp)
                    # else: short col=right original fragment — drop (leftover)
                else:
                    kept.append(hp)
            doc.header_paras = kept + list(summary_sec.body_paras)
            doc.sections = [s for i, s in enumerate(doc.sections) if i != summary_idx]
        else:
            # _target_col is None: full-width header above the two-column body
            # (samples 18, 38) or single-column template (sample 33).
            # Remove the original summary lines from header_paras; keep name/title.
            kept = [hp for j, hp in enumerate(doc.header_paras) if j not in summary_indices]
            if doc.layout.column_split_x is not None:
                # Two-column: inject LLM summary above the table (col=None = full-width)
                doc.header_paras = kept + list(summary_sec.body_paras)
                doc.sections = [s for i, s in enumerate(doc.sections) if i != summary_idx]
            else:
                # Single-column: just remove original summary — keep the LLM
                # summary section in doc.sections so it renders in the body.
                doc.header_paras = kept

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
      This runs for ALL PDF docs (single- and two-column) to prevent duplication
      in experience sections where the updater preserves orig.body_paras verbatim.
    - Stale cloned meta_lines are cleared for pipe-format role headers (company
      and dates are already in the header; the meta_line copy is redundant).
    """
    # Phase 2 Priority 2: clear body_paras for any PDF section that has roles.
    # _update_experience_section returns body_paras=orig.body_paras in non-layout-
    # bound mode (all PDF paths), so original role text duplicates the LLM roles.
    # Run unconditionally before the two-column guard so single-column PDFs benefit.
    _body_cleared = False
    for sec in doc.sections:
        if sec.roles and sec.body_paras:
            sec.body_paras = []
            _body_cleared = True
            for role in sec.roles:
                if role.header.text and "|" in role.header.text:
                    role.meta_lines.clear()

    if doc.layout.column_split_x is None:
        if _body_cleared:
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
            doc.all_paras = new_all
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
    """Apply the template's section-heading capitalisation style to LLM-injected sections.

    For two-column PDF layouts, the convention is determined from the left-column
    (template) sections and applied only to right-column (LLM-injected) sections.
    This prevents verbatim template sub-entries (e.g. 'Samira Hadid' in REFERENCES)
    from being incorrectly uppercased while still normalising extra LLM sections
    ('Technical Skills' → 'TECHNICAL SKILLS', 'Additional' → 'ADDITIONAL').
    """
    if doc.source_kind != "pdf":
        return
    if doc.layout.column_split_x is None:
        return  # only meaningful for two-column PDF layouts

    def _col(sec) -> "str | None":
        pp = sec.heading.paragraph_profile
        return pp.column_id if pp else None

    # Determine convention from left-column (template) section headings.
    left_titles = [s.title.strip() for s in doc.sections if _col(s) == "left" and s.title.strip()]
    if not left_titles:
        return

    all_caps = sum(1 for t in left_titles if t == t.upper())
    if all_caps / len(left_titles) < 0.5:
        return

    # Apply ONLY to right-column (LLM-injected extra) sections.
    for sec in doc.sections:
        if _col(sec) != "right":
            continue
        t = sec.title.strip()
        if t and t != t.upper():
            sec.title = t.upper()
            sec.heading = sec.heading.with_text(t.upper())


def _apply_role_header_case_convention(
    template_ir: ResumeDocument, doc: ResumeDocument
) -> None:
    """Apply the template's role-header capitalisation to LLM-updated role entries.

    Detects whether the original template uses ALL CAPS for role headers
    (e.g. "RESTAURANT MANAGER | COMPANY | DATE") and if so, uppercases the
    LLM-provided role header text.  This reproduces the visual style of
    templates like sample 8 (May Riley) where every role title is in ALL CAPS.

    Only applies to PDF-sourced documents.  Looks at the ORIGINAL template's
    role headers to detect the convention (updated headers may already be mixed
    case due to the LLM output).
    """
    if doc.source_kind != "pdf":
        return

    # Collect original template role header texts (before LLM updates).
    orig_role_headers: list[str] = [
        role.header.text.strip()
        for sec in template_ir.sections
        if sec.semantic_type == "experience"
        for role in sec.roles
        if role.header.text.strip()
    ]
    if not orig_role_headers:
        return

    # Check if ALL CAPS is the predominant convention.
    all_caps_count = sum(
        1 for t in orig_role_headers
        if t and t.replace("|", "").replace(" ", "").isupper() and any(c.isalpha() for c in t)
    )
    if all_caps_count < len(orig_role_headers) * 0.6:
        return  # not predominantly ALL CAPS

    # Apply ALL CAPS to updated role headers.
    # Mutate .text in-place so the same ParaModel object referenced in all_paras
    # reflects the change (with_text() creates a new object that all_paras won't see).
    for sec in doc.sections:
        if sec.semantic_type != "experience":
            continue
        for role in sec.roles:
            t = role.header.text.strip()
            if t and t != t.upper():
                role.header.text = t.upper()


def _inject_skills_into_section_body(doc: ResumeDocument) -> None:
    """Replace a skills sub-block inside a section's body_paras with LLM skills.

    Some templates embed a "SKILLS & ABILITIES" sub-section inside another
    section's body (e.g. within a CONTACT section).  The PDF parser cannot split
    it off as a standalone section because there is no clear section break, so the
    skills content lands in the parent section's body_paras.

    When apply_tailored creates a separate extra "Technical Skills" section because
    there is no top-level skills section to match, the result is a redundant heading
    ("TECHNICAL SKILLS") appearing in the rendered output AND the original SKILLS &
    ABILITIES content remaining unchanged in the parent section body.

    This function detects that pattern and:
    1. Finds a body section with a skills-like sub-heading inside its body_paras.
    2. Finds the extra skills section added by the LLM.
    3. Replaces the original skills bullets with the LLM skill lines.
    4. Removes the extra skills section so its heading doesn't render separately.
    """
    if doc.layout.column_split_x is None:
        return

    # Find the extra skills section (has section_id absent from any template
    # section, meaning it's a new/extra section without an original counterpart).
    extra_skills: "ResumeSection | None" = None
    extra_skills_idx: int | None = None
    for i, sec in enumerate(doc.sections):
        if sec.semantic_type == "skills" and not sec.section_id:
            extra_skills = sec
            extra_skills_idx = i
            break
    if extra_skills is None:
        return

    # Find a parent section whose body_paras contain a skills sub-heading.
    _SKILL_KEYWORDS = ("skill", "abilit", "competenc", "expertise")
    parent_sec = None
    skill_start_idx: int | None = None
    for sec in doc.sections:
        for j, bp in enumerate(sec.body_paras):
            txt_lower = bp.text.lower()
            if any(k in txt_lower for k in _SKILL_KEYWORDS) and len(bp.text) < 30:
                parent_sec = sec
                skill_start_idx = j
                break
        if parent_sec is not None:
            break

    if parent_sec is None or skill_start_idx is None:
        return

    # Replace body_paras from skill_start_idx onwards with the LLM skill lines.
    # Keep the heading paragraph (the sub-section label like "SKILLS &", "ABILITIES")
    # and any immediately following paragraph that is also part of the heading.
    heading_end = skill_start_idx + 1
    while heading_end < len(parent_sec.body_paras):
        bp = parent_sec.body_paras[heading_end]
        txt = bp.text.strip().lower()
        if any(k in txt for k in _SKILL_KEYWORDS) and len(bp.text) < 15:
            heading_end += 1  # multi-line heading (e.g. "SKILLS &" + "ABILITIES")
        else:
            break

    # Use the first non-heading body_para as clone archetype for the skill lines.
    archetype = (
        parent_sec.body_paras[heading_end]
        if heading_end < len(parent_sec.body_paras)
        else parent_sec.body_paras[skill_start_idx]
    )

    skill_lines = [line for line in extra_skills.body_paras if line.text.strip()]
    new_skill_paras = [
        archetype.clone_as(line.text, "paragraph") for line in skill_lines
    ]

    parent_sec.body_paras = (
        list(parent_sec.body_paras[:heading_end]) + new_skill_paras
    )

    # Remove the extra skills section so its heading doesn't appear twice.
    doc.sections = [s for i, s in enumerate(doc.sections) if i != extra_skills_idx]


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

    # Capture footer paras BEFORE apply_tailored replaces section content.
    # These are body paragraphs that carry a dark background_color (detected as
    # a footer band via pixel sampling in parse_pdf).  apply_tailored replaces
    # section body content with LLM output, so footer paras would otherwise be lost.
    _footer_bg = template_ir.layout.footer_bg_color
    if _footer_bg:
        _LIGHT = frozenset(("ffffff", "fefefe", "f8f8f8"))
        _footer_paras = []
        for _sec in template_ir.sections:
            for _pm in _sec.body_paras:
                _pp = _pm.paragraph_profile
                if _pp and _pp.background_color and _pp.background_color not in _LIGHT:
                    _footer_paras.append(_pm)
        template_ir.footer_paras = _footer_paras

    llm_sections = parse_llm_output(llm_text)
    llm_sections = apply_layout_fitting(template_ir, llm_sections, skip_compaction=True)
    updated = apply_tailored(template_ir, llm_sections, classification=classification)

    # Re-inject footer paras if they were lost during apply_tailored
    if template_ir.footer_paras and not updated.footer_paras:
        updated.footer_paras = list(template_ir.footer_paras)

    # Clear PDF-extracted text colors from all content paragraphs before rendering.
    _clear_pdf_content_colors(updated)
    # For two-column templates with a full-width header: move the LLM-injected
    # Professional Summary body into header_paras so it renders above the table.
    _inject_llm_summary_into_header(updated)
    # Merge extra LLM skills section into an existing section's skills sub-block
    # (e.g. "SKILLS & ABILITIES" inside a CONTACT section body) so the skills
    # content appears in the correct place rather than as a separate banner.
    _inject_skills_into_section_body(updated)
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
    # Apply the template's role-header capitalisation to LLM-updated role entries
    # (e.g. if template uses ALL CAPS roles, keep that convention in the output).
    _apply_role_header_case_convention(template_ir, updated)

    # Re-sort sections by column for PDF two-column documents.
    # Done AFTER _fix_extra_left_sections so LLM-injected extra sections (e.g.
    # Technical Skills) already have col_id="right" and sort correctly after
    # the template's left-column sections rather than interleaving with them.
    #
    # We use a STABLE sort on col_order only (left=1 before right=2) so that
    # the within-column order from apply_tailored is preserved.  Sorting by
    # y_top_pt was incorrect for multi-page PDFs where page-relative y values
    # are not monotone across the full document (a section at the top of page 2
    # has a smaller y than a section at the bottom of page 1, causing page 2
    # sections to sort before page 1 sections in the same column).
    if (
        updated.source_kind == "pdf"
        and updated.layout.column_split_x is not None
    ):
        from tailor.compiler.models import ParaModel as _ParaModel  # local import

        def _sec_col_key(sec) -> int:
            pp = sec.heading.paragraph_profile
            return (
                1 if (pp and pp.column_id == "left")
                else 2 if (pp and pp.column_id == "right")
                else 0
            )

        updated.sections.sort(key=_sec_col_key)  # Python sort is stable

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

