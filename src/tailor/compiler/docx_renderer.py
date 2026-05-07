"""Render a ResumeDocument to a fresh DOCX file.

Strategy
--------
1. Copy the original template DOCX to the output path (preserves styles,
   page setup, headers, footers, and the numbering definitions).
2. Strip all w:p and w:tbl elements from the body (keeping the final w:sectPr
   which holds page margins and dimensions).
3. For each paragraph in ResumeDocument.all_paras, clone the paragraph's
   xml_proto (the deepcopy stored at parse time) and append it to the body.
4. Set the paragraph text by clearing all run text and writing new_text on the
   first run (same pattern as Phase 14's _replace_text_preserving_format).

Layout-blocks path (USE_LAYOUT_BLOCK_RENDERER or deserialized IR)
------------------------------------------------------------------
When layout_blocks are present and either the flag is on or no runtime xml_proto
exists, render_docx calls _render_from_layout_blocks instead of the default
section-based loop.  The blocks preserve physical document order so that
newspaper-column and table layouts are not flattened into a single column.
"""
from __future__ import annotations

import logging
import shutil
from copy import deepcopy
from typing import TYPE_CHECKING

from docx import Document

from tailor.compiler.models import (
    LayoutParagraphBlock,
    LayoutTableBlock,
    ParaModel,
    ResumeDocument,
    TableBlock,
)

if TYPE_CHECKING:
    pass

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Paragraph text replacement (in cloned XML)
# ---------------------------------------------------------------------------

def _strip_non_column_section_break(p_elem) -> None:
    """Remove w:sectPr from paragraph pPr ONLY when it does not define a multi-column layout.

    Section properties embedded in a paragraph's pPr mark the end of a document
    section.  When those sectPr entries merely switch page size (single-column,
    w:cols absent or w:num="1"), they act as pure page-break markers and should
    be stripped from the rendered output — otherwise the section boundary creates
    an unwanted hard page break even when the content could flow naturally.

    Multi-column sectPr (w:cols w:num ≥ 2) are intentionally preserved so that
    templates with newspaper-style column layouts (e.g. sample 31 with 2- or
    3-column sections) retain their visual structure.
    """
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        return
    sectPr = pPr.find(f"{{{_W}}}sectPr")
    if sectPr is None:
        return
    cols = sectPr.find(f"{{{_W}}}cols")
    if cols is not None:
        num = cols.get(f"{{{_W}}}num")
        if num is not None and int(num) >= 2:
            return  # multi-column layout — keep sectPr intact
    pPr.remove(sectPr)


def _strip_section_break(p_elem) -> None:
    """Remove ALL w:sectPr from paragraph pPr unconditionally (used in non-layout path)."""
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is not None:
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is not None:
            pPr.remove(sectPr)


def _strip_column_break(p_elem) -> None:
    """Remove w:br type='column' runs from a paragraph.

    Column breaks in the template force content to start at the top of the
    next column.  After tailoring, column placement is determined by content
    volume flowing naturally through the w:cols grid — explicit column breaks
    are not needed and produce spurious layout jumps when section headings
    are cloned from a paragraph that happened to carry one.
    """
    for r_elem in list(p_elem.findall(f"{{{_W}}}r")):
        for br in list(r_elem.findall(f"{{{_W}}}br")):
            if br.get(f"{{{_W}}}type") == "column":
                r_elem.remove(br)


def _ensure_keep_next(p_elem) -> None:
    """Add w:keepNext to the paragraph pPr if not already present.

    w:keepNext tells Word/LibreOffice: keep this paragraph on the same page as
    the following paragraph.  Applied to role-header paragraphs, this prevents
    the common resume defect where a role title is stranded at the bottom of a
    page while all of its bullets appear on a sparse continuation page.

    No-op when keepNext already exists (idempotent).  Only adds keepNext; never
    removes it, so the caller does not need to check original template state.
    """
    from lxml import etree as _etree
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        pPr = _etree.Element(f"{{{_W}}}pPr")
        p_elem.insert(0, pPr)
    if pPr.find(f"{{{_W}}}keepNext") is None:
        _etree.SubElement(pPr, f"{{{_W}}}keepNext")


def apply_trailing_section_justification(
    docx_path: str,
    pdf_path: str,
) -> bool:
    """Two-pass sparse-final-page fix: detect sparse page, add section spacing.

    After an initial render+PDF-convert, if the last continuation page is sparse
    (content fills < 65% of page height after a well-packed previous page), this
    function opens the rendered DOCX and adds ``w:spacing w:before`` to the
    trailing section-heading paragraphs.  The extra spacing distributes the
    bottom whitespace evenly between sections, making the page look designed
    rather than incomplete.

    Target: 80% fill on the sparse page.  Extra spacing is distributed across
    the last N section-heading paragraphs (max N=4, capped at 36 pts each to
    preserve aesthetics).  The caller must re-convert DOCX → PDF after this
    returns True.

    Returns True when spacing was added (re-render required); False when the
    page is not sparse or the fix could not be applied.
    """
    try:
        from tailor.eval.extractor import extract
        from tailor.eval.layout_grader.pdf_scorer import (
            _find_sparse_continuation_pages,
        )

        gen = extract(pdf_path)
        sparse = _find_sparse_continuation_pages(gen)
        if not sparse:
            return False

        page_num, fill_frac, bottom_empty, area_ratio, n_lines, n_blocks, _ = sparse[0]

        # Require some real content on the sparse page (not nearly empty)
        if area_ratio < 0.08:
            _log.debug("SPARSE_FIX_SKIP: area_ratio %.2f too low for justification", area_ratio)
            return False

        # Calculate extra spacing needed to reach 80% fill
        page = gen.pages[page_num - 1]
        page_h = page.height or 842.0
        cb = page.content_bbox
        if not cb:
            return False
        content_span = cb[3] - cb[1]
        target_span = page_h * 0.80
        extra_pts = target_span - content_span
        if extra_pts <= 5:
            return False

        # Open DOCX and find trailing section-heading paragraphs
        from docx import Document
        from lxml import etree as _etree

        doc = Document(docx_path)
        _SECTION_KWS = frozenset({
            "education", "skills", "technical", "certification", "award",
            "project", "publication", "volunteer", "language", "interest",
        })
        heading_elems = []
        for para in doc.paragraphs:
            txt = para.text.strip()
            if not txt or len(txt) > 60:
                continue
            txt_lo = txt.lower()
            # Section heading: short, keyword-matching, no leading bullet char
            if any(kw in txt_lo for kw in _SECTION_KWS) and txt[0] not in "•-*·▪":
                heading_elems.append(para._p)

        if len(heading_elems) < 2:
            _log.debug("SPARSE_FIX_SKIP: too few section headings found (%d)", len(heading_elems))
            return False

        # Take the last min(4, N) headings — most likely to be on the sparse page
        n_use = min(len(heading_elems), 4)
        targets = heading_elems[-n_use:]

        # Cap per-heading increment at 36 pts (720 twips) to preserve aesthetics
        extra_twips_each = int(extra_pts / n_use * 20)   # 1 pt = 20 twips
        extra_twips_each = max(60, min(extra_twips_each, 720))  # 3–36 pts

        for p_elem in targets:
            pPr = p_elem.find(f"{{{_W}}}pPr")
            if pPr is None:
                pPr = _etree.SubElement(p_elem, f"{{{_W}}}pPr")
                p_elem.insert(0, pPr)
            spacing = pPr.find(f"{{{_W}}}spacing")
            if spacing is None:
                spacing = _etree.SubElement(pPr, f"{{{_W}}}spacing")
            existing = int(spacing.get(f"{{{_W}}}before", "0"))
            spacing.set(f"{{{_W}}}before", str(existing + extra_twips_each))

        doc.save(docx_path)
        _log.info(
            "SPARSE_PAGE_JUSTIFICATION: page %d fill=%.1f%% → target 80%%; "
            "added %d twips (%d pts) to %d trailing section headers",
            page_num, fill_frac * 100, extra_twips_each, extra_twips_each // 20, n_use,
        )
        return True

    except Exception:
        _log.warning("Sparse page justification failed", exc_info=True)
        return False


def _strip_last_rendered_page_breaks(p_elem) -> None:
    """Remove w:lastRenderedPageBreak elements from a cloned paragraph.

    These markers record where the page break fell during the *template's*
    last render.  After tailoring the content changes, so the markers are
    stale and mislead PDF converters / DOCX viewers that honour them as
    hard breaks, producing misplaced forced page breaks in the output.
    Word itself ignores them and recalculates, but LibreOffice, Google Docs,
    and DOCX→PDF converters do not.
    """
    for el in p_elem.findall(f".//{{{_W}}}lastRenderedPageBreak"):
        parent = el.getparent()
        if parent is not None:
            parent.remove(el)


def _set_run_text(r_elem, portion: str) -> None:
    """Write *portion* into the first w:t of *r_elem* (already cleared)."""
    from lxml import etree
    t_elems = r_elem.findall(f"{{{_W}}}t")
    if t_elems:
        t_elems[0].text = portion
        if portion and (portion[0] == " " or portion[-1] == " "):
            t_elems[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    elif portion:
        t = etree.SubElement(r_elem, f"{{{_W}}}t")
        t.text = portion
        if portion[0] == " " or portion[-1] == " ":
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


def _set_para_text(p_elem, text: str) -> None:
    """Set text on p_elem in-place, preserving all per-run formatting.

    Text is distributed across runs proportionally to their original character
    lengths.  This preserves per-run formatting differences (e.g. bold on the
    first word only, w:caps on a trailing institution name) when the new text
    has a similar structure to the original.

    If no runs exist a minimal w:r/w:t structure is created.

    Note: w:br elements in the XML proto already provide in-paragraph line
    breaks.  Strip any '\\n' characters from *text* before distributing so
    that the line break is not written twice (once into w:t and once via w:br).
    """
    # w:br elements provide the line break; avoid doubling by stripping \n from text.
    text = text.replace("\n", "")
    all_runs: list = []
    for child in p_elem:
        tag = child.tag
        if tag == f"{{{_W}}}r":
            all_runs.append(child)
        elif tag == f"{{{_W}}}hyperlink":
            for r in child.findall(f"{{{_W}}}r"):
                all_runs.append(r)
        elif tag == f"{{{_W}}}sdt":
            # Content controls (w:sdt) wrap runs in w:sdtContent.
            # Include them so their text gets cleared and replaced rather
            # than surviving alongside a newly-appended run (which would
            # cause the text to appear doubled when the output is read back).
            sdt_content = child.find(f"{{{_W}}}sdtContent")
            if sdt_content is not None:
                for r in sdt_content.findall(f".//{{{_W}}}r"):
                    all_runs.append(r)

    if not all_runs:
        # No runs — create a minimal one
        from lxml import etree
        r = etree.SubElement(p_elem, f"{{{_W}}}r")
        t = etree.SubElement(r, f"{{{_W}}}t")
        t.text = text
        if text and (text[0] == " " or text[-1] == " "):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        return

    # Collect original text length of each run (used for proportional split).
    # Two kinds of runs are excluded from content distribution:
    #
    # "Structural spacers" (ws_text): runs whose direct w:t text is purely
    #   non-breaking/zero-width whitespace (e.g. a leading '\xa0' indent run).
    #   They keep their original text and are restored immediately after clearing.
    #
    # "VML text" runs (vml_indices): runs that contain a w:pict element with
    #   nested w:t elements inside a VML text box (v:textbox → w:txbxContent).
    #   _get_para_text reads these recursively, but we cannot clear or rewrite
    #   them via the normal w:t path — they must be left untouched.  Their text
    #   contribution is stripped from the front of *text* so the remaining portion
    #   is distributed only across the plain (non-VML) runs.
    _WS = frozenset("\xa0\u00ad\u2009\u200b\u200c\u200d\ufeff")
    orig_lens: list[int] = []
    ws_text: dict[int, str] = {}   # index → original text for whitespace-only runs
    vml_indices: set[int] = set()  # indices of runs with VML-embedded text
    vml_text_str = ""              # concatenated text from all VML runs (in order)
    for i, r in enumerate(all_runs):
        t_elems = r.findall(f"{{{_W}}}t")
        run_text = "".join(e.text or "" for e in t_elems)
        chars = len(run_text)
        chars += sum(len(e.text or "") for e in r.findall(f"{{{_W}}}delText"))
        if chars > 0 and all(c in _WS for c in run_text):
            ws_text[i] = run_text
            chars = 0   # exclude from proportional distribution
        elif r.find(f"{{{_W}}}pict") is not None:
            nested_t = r.findall(f".//{{{_W}}}t")
            if nested_t:
                vml_text_str += "".join(t.text or "" for t in nested_t)
                vml_indices.add(i)
                chars = 0   # exclude from proportional distribution
        orig_lens.append(chars)
    total_orig = sum(orig_lens)

    # Strip the VML-contributed text from the front of *text*.  The VML content
    # is already correct and will not be rewritten, so only the remainder needs
    # to be distributed across the plain runs.
    if vml_text_str and text.startswith(vml_text_str):
        text = text[len(vml_text_str):]

    # Clear text from all runs (direct w:t children only; VML content is untouched).
    for r in all_runs:
        for t in r.findall(f"{{{_W}}}t"):
            t.text = ""
        for t in r.findall(f"{{{_W}}}delText"):
            t.text = ""

    # Restore whitespace-only runs to their original text immediately.
    for i, r in enumerate(all_runs):
        if i in ws_text:
            _set_run_text(r, ws_text[i])

    # Determine which runs will receive distributed text.
    content_indices = [i for i in range(len(all_runs)) if i not in ws_text and i not in vml_indices]

    if not content_indices:
        return  # all text is in VML boxes or structural spacers — nothing to write

    if total_orig == 0 or len(content_indices) == 1:
        _set_run_text(all_runs[content_indices[0]], text)
        return

    # Distribute new text proportionally across content runs only.
    last_ci = content_indices[-1]

    assigned = 0
    cumulative = 0
    for i, (r, olen) in enumerate(zip(all_runs, orig_lens)):
        if i in ws_text or i in vml_indices:
            continue   # already handled above
        if i == last_ci:
            portion = text[assigned:]
        else:
            cumulative += olen
            end = round(len(text) * cumulative / total_orig) if total_orig else 0
            portion = text[assigned:end]
            assigned = end
        _set_run_text(r, portion)

    # Strip any <w:tab/> elements left over in runs after text distribution.
    # Tab-column role headers and bullet paragraphs (e.g. \u25cf + <w:tab/> +
    # text) use tab stops for original alignment.  After setting new LLM text
    # the alignment comes from the text itself (pipe separators or paragraph
    # indent), so residual <w:tab/> elements only produce mid-word tab
    # characters when the paragraph is read back.
    for r in all_runs:
        for tab in list(r.findall(f"{{{_W}}}tab")):
            r.remove(tab)


# ---------------------------------------------------------------------------
# Item renderers
# ---------------------------------------------------------------------------

def _render_para(pm: ParaModel, body, sectPr, preserve_section_break: bool = False) -> None:
    """Render a single ParaModel and insert it before sectPr (or append).

    When *preserve_section_break* is True, embedded ``w:sectPr`` elements in
    the paragraph's ``w:pPr`` are kept intact rather than stripped.  Pass
    True for header-section paragraphs in multi-column templates so the
    section boundary (e.g. single-column header → two-column body) is
    preserved in the output.
    """
    if pm.style.xml_proto is not None:
        clone = deepcopy(pm.style.xml_proto)
        _strip_last_rendered_page_breaks(clone)
        if not preserve_section_break:
            _strip_section_break(clone)
        _set_para_text(clone, pm.text)
    elif pm.paragraph_profile is not None:
        from tailor.compiler.para_builder import build_para_element
        clone = build_para_element(pm)
    else:
        raise ValueError(
            f"Paragraph '{pm.text[:60]}' has no xml_proto or paragraph_profile; "
            "cannot render it.  New paragraphs must be created via "
            "ParaModel.clone_as()."
        )
    if sectPr is not None:
        sectPr.addprevious(clone)
    else:
        body.append(clone)


def _apply_pdf_page_geometry(sectPr, layout) -> None:
    """Overwrite pgSz and pgMar in *sectPr* with the source PDF's page geometry.

    Called for PDF-sourced documents so the output DOCX uses the same paper
    size and margins as the source, keeping the round-trip page count stable.
    Header/footer/gutter margins from the template are preserved.
    """
    from lxml import etree

    if sectPr is None:
        return

    # Page size
    pgSz = sectPr.find(f"{{{_W}}}pgSz")
    if pgSz is None:
        pgSz = etree.SubElement(sectPr, f"{{{_W}}}pgSz")
    pgSz.set(f"{{{_W}}}w", str(int(layout.page_width_pt * 20)))
    pgSz.set(f"{{{_W}}}h", str(int(layout.page_height_pt * 20)))

    # Page margins (preserve header/footer/gutter attributes if present).
    # margin_top_pt is accurate (first non-whitespace text block Y position).
    # margin_bottom_pt can be inaccurate for non-full single-page PDFs where
    # blank space below the last line is incorrectly measured as "margin".
    # Clamping bottom to min(bottom, top) avoids this: for multi-page PDFs the
    # last block on page 1 is typically near the real bottom margin so B < T is
    # plausible; for single-page PDFs B is huge so clamping keeps it ≤ T.
    pgMar = sectPr.find(f"{{{_W}}}pgMar")
    if pgMar is None:
        pgMar = etree.SubElement(sectPr, f"{{{_W}}}pgMar")
    top_twips = int(layout.margin_top_pt * 20)
    bot_twips = int(min(layout.margin_bottom_pt, layout.margin_top_pt) * 20)
    pgMar.set(f"{{{_W}}}top",    str(top_twips))
    pgMar.set(f"{{{_W}}}bottom", str(bot_twips))
    pgMar.set(f"{{{_W}}}left",   str(int(layout.margin_left_pt * 20)))
    pgMar.set(f"{{{_W}}}right",  str(int(layout.margin_right_pt * 20)))


def _get_text_area_width_twips(sectPr) -> int:
    """Return the text-area width in twips from a sectPr element."""
    pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    page_w = int(pgSz.get(f"{{{_W}}}w", "12240")) if pgSz is not None else 12240
    if pgMar is not None:
        left = int(pgMar.get(f"{{{_W}}}left", "1440"))
        right = int(pgMar.get(f"{{{_W}}}right", "1440"))
    else:
        left = right = 1440
    return max(1, page_w - left - right)


def _render_pdf_two_col(doc: "ResumeDocument", body, sectPr, doc_part=None) -> None:
    """Render a two-column PDF-sourced document as a borderless DOCX table.

    Creates a full-page-width single-row w:tbl pushed to the page left edge
    via a negative w:tblInd (bypassing the left margin).  Left cell width =
    layout.left_col_width_twips (from the visual sidebar boundary); right cell
    fills the remainder.  The left cell receives cell shading from
    layout.left_col_bg_color when available.  All paragraphs tagged
    column_id='left' go into the left cell; everything else goes right.
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    layout = doc.layout

    # Read page geometry from sectPr
    pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    page_w_twips = int(pgSz.get(f"{{{_W}}}w", "12240")) if pgSz is not None else 12240
    left_margin_twips = (
        int(pgMar.get(f"{{{_W}}}left", "1440")) if pgMar is not None else 1440
    )

    # Column widths: from layout (computed from visual sidebar drawing edge).
    # Fall back to proportional split if layout widths are absent.
    left_w = layout.left_col_width_twips or (page_w_twips // 3)
    right_w = layout.right_col_width_twips or (page_w_twips - left_w)
    total_w = left_w + right_w

    # Table element
    tbl = etree.Element(f"{{{_W}}}tbl")

    # Table properties: full page width, negative indent to reach the page
    # left edge (past the left margin), fixed layout, no borders, no cell margins.
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW.set(f"{{{_W}}}w", str(total_w))
    tblW.set(f"{{{_W}}}type", "dxa")

    # Negative tblInd pushes the table left by left_margin_twips so the
    # sidebar reaches the physical page edge (matching the PDF visual).
    tblInd = etree.SubElement(tblPr, f"{{{_W}}}tblInd")
    tblInd.set(f"{{{_W}}}w", str(-left_margin_twips))
    tblInd.set(f"{{{_W}}}type", "dxa")

    # Fixed layout so Word honours the explicit column widths.
    tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
    tblLayout.set(f"{{{_W}}}type", "fixed")

    tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
        brd.set(f"{{{_W}}}val", "none")

    tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        m = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
        m.set(f"{{{_W}}}w", "0")
        m.set(f"{{{_W}}}type", "dxa")

    # Single row
    tr = etree.SubElement(tbl, f"{{{_W}}}tr")

    # Left cell
    left_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    left_tcPr = etree.SubElement(left_tc, f"{{{_W}}}tcPr")
    left_tcW = etree.SubElement(left_tcPr, f"{{{_W}}}tcW")
    left_tcW.set(f"{{{_W}}}w", str(left_w))
    left_tcW.set(f"{{{_W}}}type", "dxa")
    if layout.left_col_bg_color:
        shd = etree.SubElement(left_tcPr, f"{{{_W}}}shd")
        shd.set(f"{{{_W}}}val", "clear")
        shd.set(f"{{{_W}}}color", "auto")
        shd.set(f"{{{_W}}}fill", layout.left_col_bg_color)

    # Right cell
    right_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    right_tcPr = etree.SubElement(right_tc, f"{{{_W}}}tcPr")
    right_tcW = etree.SubElement(right_tcPr, f"{{{_W}}}tcW")
    right_tcW.set(f"{{{_W}}}w", str(right_w))
    right_tcW.set(f"{{{_W}}}type", "dxa")
    if layout.right_col_bg_color:
        shd = etree.SubElement(right_tcPr, f"{{{_W}}}shd")
        shd.set(f"{{{_W}}}val", "clear")
        shd.set(f"{{{_W}}}color", "auto")
        shd.set(f"{{{_W}}}fill", layout.right_col_bg_color)

    # Sort ALL paragraphs purely by column_id so that the cell contents match
    # the source PDF columns regardless of how _group_sections classified them.
    # Paragraphs with no column_id (column_id is None) did not belong to either
    # column and are rendered as full-width elements ABOVE the two-column table.
    # Using column_id directly prevents the header_paras heuristic from placing
    # left-column employment sections above the table when their section names
    # are not in the known-headings vocabulary.
    above_paras = [
        pm for pm in doc.all_paras
        if not (pm.paragraph_profile and pm.paragraph_profile.column_id in ("left", "right"))
    ]
    left_paras = [
        pm for pm in doc.all_paras
        if pm.paragraph_profile and pm.paragraph_profile.column_id == "left"
    ]
    right_paras = [
        pm for pm in doc.all_paras
        if pm.paragraph_profile and pm.paragraph_profile.column_id == "right"
    ]

    for pm in left_paras:
        left_tc.append(build_para_element(pm, doc_part=doc_part))
    # DOCX requires at least one paragraph per cell
    if not left_paras:
        etree.SubElement(left_tc, f"{{{_W}}}p")

    for pm in right_paras:
        right_tc.append(build_para_element(pm, doc_part=doc_part))
    if not right_paras:
        etree.SubElement(right_tc, f"{{{_W}}}p")

    # Build header element(s): if any above-table paragraph has a background
    # colour, wrap all above_paras in a single-cell full-page-width table so
    # the shading is applied at cell level (one continuous band).
    header_bg = next(
        (pm.paragraph_profile.background_color
         for pm in above_paras
         if pm.paragraph_profile and pm.paragraph_profile.background_color),
        None,
    )
    if header_bg and above_paras:
        hdr_tbl = etree.Element(f"{{{_W}}}tbl")
        hdr_tblPr = etree.SubElement(hdr_tbl, f"{{{_W}}}tblPr")
        hdr_tblW = etree.SubElement(hdr_tblPr, f"{{{_W}}}tblW")
        hdr_tblW.set(f"{{{_W}}}w", str(total_w))
        hdr_tblW.set(f"{{{_W}}}type", "dxa")
        hdr_tblInd = etree.SubElement(hdr_tblPr, f"{{{_W}}}tblInd")
        hdr_tblInd.set(f"{{{_W}}}w", str(-left_margin_twips))
        hdr_tblInd.set(f"{{{_W}}}type", "dxa")
        hdr_tblLayout = etree.SubElement(hdr_tblPr, f"{{{_W}}}tblLayout")
        hdr_tblLayout.set(f"{{{_W}}}type", "fixed")
        hdr_borders = etree.SubElement(hdr_tblPr, f"{{{_W}}}tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            brd = etree.SubElement(hdr_borders, f"{{{_W}}}{side}")
            brd.set(f"{{{_W}}}val", "none")
        hdr_cellMar = etree.SubElement(hdr_tblPr, f"{{{_W}}}tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            m = etree.SubElement(hdr_cellMar, f"{{{_W}}}{side}")
            m.set(f"{{{_W}}}w", "0")
            m.set(f"{{{_W}}}type", "dxa")
        hdr_tr = etree.SubElement(hdr_tbl, f"{{{_W}}}tr")
        hdr_tc = etree.SubElement(hdr_tr, f"{{{_W}}}tc")
        hdr_tcPr = etree.SubElement(hdr_tc, f"{{{_W}}}tcPr")
        hdr_tcW = etree.SubElement(hdr_tcPr, f"{{{_W}}}tcW")
        hdr_tcW.set(f"{{{_W}}}w", str(total_w))
        hdr_tcW.set(f"{{{_W}}}type", "dxa")
        hdr_shd = etree.SubElement(hdr_tcPr, f"{{{_W}}}shd")
        hdr_shd.set(f"{{{_W}}}val", "clear")
        hdr_shd.set(f"{{{_W}}}color", "auto")
        hdr_shd.set(f"{{{_W}}}fill", header_bg)
        for pm in above_paras:
            hdr_tc.append(build_para_element(pm, doc_part=doc_part, skip_bg_shd=True))
        header_elements = [hdr_tbl]
    else:
        header_elements = [build_para_element(pm, doc_part=doc_part) for pm in above_paras]

    # Insert header element(s) before the body table, then the body table itself.
    if sectPr is not None:
        for elem in header_elements:
            sectPr.addprevious(elem)
        sectPr.addprevious(tbl)
    else:
        for elem in header_elements:
            body.append(elem)
        body.append(tbl)


def _render_table_block(tb: TableBlock, doc: "ResumeDocument", body, sectPr) -> None:
    """Clone a TableBlock's xml_proto, update paragraph text, and insert it."""
    clone = deepcopy(tb.xml_proto)
    clone_paras = clone.findall(f".//{{{_W}}}p")

    if len(clone_paras) == len(tb.para_models):
        # Happy path: counts match — update each paragraph in place.
        for p_elem, pm in zip(clone_paras, tb.para_models):
            _set_para_text(p_elem, pm.text)
    # else: count mismatch (shouldn't happen unless LLM restructured the table);
    # fall through and insert the unmodified clone so the layout is preserved.

    if sectPr is not None:
        sectPr.addprevious(clone)
    else:
        body.append(clone)


# ---------------------------------------------------------------------------
# Numbering patch helpers
# ---------------------------------------------------------------------------

def _patch_bullet_numbering(d) -> None:
    """Replace Symbol-font \\uf0b7 bullet chars with Unicode '•' in numbering defs.

    The default python-docx template (and many DOCX templates) defines its bullet
    list using the Symbol private-use character \\uf0b7 with w:rFonts=Symbol.
    LibreOffice lacks Symbol font, so the bullet is invisible.  This patches all
    such lvlText entries to use the standard Unicode bullet U+2022 with Calibri
    font so every renderer shows a visible bullet marker.
    """
    try:
        num_part = d.part.numbering_part
    except Exception:
        return
    elem = num_part._element
    for lvlText in elem.findall(f".//{{{_W}}}lvlText"):
        if lvlText.get(f"{{{_W}}}val", "") == "\uf0b7":
            lvlText.set(f"{{{_W}}}val", "\u2022")
            lvl = lvlText.getparent()
            if lvl is None:
                continue
            # Fix Symbol font → Calibri so all renderers can display the bullet
            rPr = lvl.find(f"{{{_W}}}rPr")
            if rPr is not None:
                for fonts in rPr.findall(f"{{{_W}}}rFonts"):
                    for attr in (f"{{{_W}}}ascii", f"{{{_W}}}hAnsi", f"{{{_W}}}cs", f"{{{_W}}}eastAsia"):
                        if fonts.get(attr, "").lower() in ("symbol", "wingdings"):
                            fonts.set(attr, "Calibri")
            # Remove the w:tabs with pos="0" from the level pPr.
            # That tab goes backwards (past the bullet's hanging indent) and
            # causes LibreOffice/xhtml2pdf to mis-position the bullet marker.
            lvl_pPr = lvl.find(f"{{{_W}}}pPr")
            if lvl_pPr is not None:
                for tabs in lvl_pPr.findall(f"{{{_W}}}tabs"):
                    lvl_pPr.remove(tabs)


# ---------------------------------------------------------------------------
# DOCX native two-column rendering
# ---------------------------------------------------------------------------

_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"


def _fix_anchor_layout_in_cell(cell_elem) -> None:
    """Set layoutInCell='0' on every floating anchor inside *cell_elem*.

    Floating anchors with layoutInCell='1' interpret their position offsets
    relative to the containing table cell instead of the page.  For background
    decoration shapes that use absolute page-relative coordinates (e.g. the
    full-page grey header/sidebar drawing group in the veeva_03 template), this
    causes the drawing to shift down when the paragraph that owns it is placed
    inside a table cell.  Setting layoutInCell='0' restores page-relative
    positioning so the drawing always appears at its intended page coordinates.
    """
    for anchor in cell_elem.findall(f".//{{{_WP}}}anchor"):
        anchor.set("layoutInCell", "0")


def _render_docx_native_two_col(
    doc: "ResumeDocument",
    body,
    sectPr,
    left_w: int,
    right_w: int,
    col_space: int,
    header_para_ids: frozenset,
) -> None:
    """Render a DOCX template whose body uses native w:cols two-column layout.

    Word's sequential column flow (col1→col2→page2-col1→page2-col2) causes
    sidebar content to spill across columns and main content to jump to the
    wrong column after a page overflow.  Converting to a table gives each
    column an independent text stream that stays in its column across pages.

    Layout:
    - Full-width header section (paragraphs up to and including the embedded
      sectPr boundary) is rendered normally before the table.
    - A single-row borderless table holds the two-column body:
        Left cell  (left_w twips)  : left-column body (contact, edu, skills)
        Right cell (right_w twips) : section content (summary, experience …)

    col_space is preserved as right padding on the left cell so the visual
    gap between columns matches the original template.
    """
    from lxml import etree

    # Find the embedded-sectPr boundary in header_paras.
    # Paragraphs before (and including) it are full-width; those after are
    # the left-column body content.
    has_sectPr_boundary = False
    split_after = len(doc.header_paras)  # fallback: all header_paras are left-col
    for i, pm in enumerate(doc.header_paras):
        if pm.style.xml_proto is not None:
            pPr = pm.style.xml_proto.find(f"{{{_W}}}pPr")
            if pPr is not None and pPr.find(f"{{{_W}}}sectPr") is not None:
                split_after = i + 1
                has_sectPr_boundary = True
                break

    full_width_paras = doc.header_paras[:split_after]
    left_col_paras = doc.header_paras[split_after:]
    right_col_paras = doc.all_paras[len(doc.header_paras):]

    # Render full-width header paras WITHOUT preserving the embedded sectPr.
    # In the original template the sectPr boundary separated the full-width
    # header section from the two-column body section.  Now that the body is
    # rendered as a table (single-column), the section break is unnecessary
    # and causes LibreOffice to insert a blank page before the table.
    # Stripping it collapses the entire document into one section; the body
    # sectPr (already stripped of w:cols) governs page geometry uniformly.
    for pm in full_width_paras:
        _render_para(pm, body, sectPr, preserve_section_break=False)

    # The sectPr boundary paragraph is a zero-height section marker in native
    # Word flow.  In our table layout it becomes a regular paragraph above the
    # table and contributes ~12pt of line height, shifting all left-column
    # content downward by ~13–14pt.  This misaligns text with the fixed-position
    # icon/heading shapes anchored to the page.
    # Fix: patch the boundary paragraph's spacing to use exact line-height of
    # 20 twips (1pt) so it contributes negligible vertical space before the table.
    # The paragraph must remain present (removing it entirely causes LibreOffice to
    # push the table to the next page due to page-anchor interaction with the
    # background drawing in the first left-cell paragraph).
    if has_sectPr_boundary and sectPr is not None:
        boundary_p = sectPr.getprevious()
        if boundary_p is not None and boundary_p.tag == f"{{{_W}}}p":
            _pPr = boundary_p.find(f"{{{_W}}}pPr")
            if _pPr is None:
                _pPr = etree.SubElement(boundary_p, f"{{{_W}}}pPr")
                boundary_p.insert(0, _pPr)
            _sp = _pPr.find(f"{{{_W}}}spacing")
            if _sp is None:
                _sp = etree.SubElement(_pPr, f"{{{_W}}}spacing")
            _sp.set(f"{{{_W}}}line", "20")
            _sp.set(f"{{{_W}}}lineRule", "exact")
            _sp.set(f"{{{_W}}}before", "0")
            _sp.set(f"{{{_W}}}after", "0")

    # Build the two-column table.
    tbl = etree.Element(f"{{{_W}}}tbl")

    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    # In native w:cols layout the inter-column space (col_space) is EXTERNAL to
    # both columns — it sits in the gap between them and is NOT part of either
    # column's text area.  Do NOT add col_space as a cell right margin: that
    # would reduce the left cell content area from left_w to (left_w - col_space),
    # causing paragraphs with ind-right values (icon gaps) and ind-left values
    # (location indent) to wrap, shifting the sidebar sections out of alignment
    # with their floating section-heading shapes.
    # Keeping the cell width = left_w and adding NO cell right margin preserves
    # the original paragraph text area exactly.  The visual inter-column gap is
    # implicit in the right-side slack between the table and the page margin.
    tblW.set(f"{{{_W}}}w", str(left_w + right_w))
    tblW.set(f"{{{_W}}}type", "dxa")
    tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
    tblLayout.set(f"{{{_W}}}type", "fixed")
    tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
        brd.set(f"{{{_W}}}val", "none")
    # Zero all cell margins so no extra insets are added inside any cell.
    tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        m = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
        m.set(f"{{{_W}}}w", "0")
        m.set(f"{{{_W}}}type", "dxa")

    tr = etree.SubElement(tbl, f"{{{_W}}}tr")

    # Left cell (narrow sidebar).  Width = left_w, no cell margins: the usable
    # text area equals left_w, matching the original native column width exactly.
    left_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    left_tcPr = etree.SubElement(left_tc, f"{{{_W}}}tcPr")
    left_tcW = etree.SubElement(left_tcPr, f"{{{_W}}}tcW")
    left_tcW.set(f"{{{_W}}}w", str(left_w))
    left_tcW.set(f"{{{_W}}}type", "dxa")
    left_vAlign = etree.SubElement(left_tcPr, f"{{{_W}}}vAlign")
    left_vAlign.set(f"{{{_W}}}val", "top")

    for pm in left_col_paras:
        _render_para(pm, left_tc, None)
    if not left_col_paras:
        etree.SubElement(left_tc, f"{{{_W}}}p")
    # Floating anchors positioned relative to the page must not be re-anchored
    # to the cell origin — force page-relative layout for all anchors in the cell.
    _fix_anchor_layout_in_cell(left_tc)

    # Right cell (main content)
    right_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    right_tcPr = etree.SubElement(right_tc, f"{{{_W}}}tcPr")
    right_tcW = etree.SubElement(right_tcPr, f"{{{_W}}}tcW")
    right_tcW.set(f"{{{_W}}}w", str(right_w))
    right_tcW.set(f"{{{_W}}}type", "dxa")
    right_vAlign = etree.SubElement(right_tcPr, f"{{{_W}}}vAlign")
    right_vAlign.set(f"{{{_W}}}val", "top")

    for pm in right_col_paras:
        _render_para(pm, right_tc, None)
    if not right_col_paras:
        etree.SubElement(right_tc, f"{{{_W}}}p")
    _fix_anchor_layout_in_cell(right_tc)

    if sectPr is not None:
        sectPr.addprevious(tbl)
    else:
        body.append(tbl)


# ---------------------------------------------------------------------------
# Layout-blocks rendering (Option B: XML prototype path)
# ---------------------------------------------------------------------------

def _build_para_lookup(doc: ResumeDocument) -> dict[str, ParaModel]:
    """Build para_id → ParaModel from all semantic-model paragraphs.

    Visits paragraphs in priority order — semantic sections first (they carry
    the LLM-updated text), then ``all_paras`` as a gap-filler.  When duplicate
    IDs are encountered (can happen when apply_tailored rebuilds all_paras),
    the first-seen entry wins and the duplicate is logged.

    Paragraphs with ``para_id=""`` are excluded from the map.
    """
    seen: dict[str, ParaModel] = {}

    def _add(pm: ParaModel) -> None:
        if not pm.para_id:
            return
        if pm.para_id in seen:
            _log.debug("LAYOUT_BLOCK_DUPLICATE_PARA_ID: %r", pm.para_id)
        else:
            seen[pm.para_id] = pm

    for pm in doc.header_paras:
        _add(pm)
    for sec in doc.sections:
        _add(sec.heading)
        for role in sec.roles:
            _add(role.header)
            for pm in role.header_extra:
                _add(pm)
            for pm in role.meta_lines:
                _add(pm)
            for pm in role.bullets:
                _add(pm)
        for pm in sec.body_paras:
            _add(pm)
    # Fallback: all_paras may contain paragraphs not yet in semantic sections
    for pm in (doc.all_paras or []):
        if pm.para_id and pm.para_id not in seen:
            seen[pm.para_id] = pm
    return seen


def _render_from_layout_blocks(
    doc: ResumeDocument,
    body,
    sectPr,
) -> None:
    """Render *doc* from its serialized layout_blocks (XML prototype strings).

    This path reproduces the original physical document order instead of the
    semantic section order, preserving newspaper-column layouts, table cell
    widths, merged cells, and column-break structure.

    Para-ID lookup
    --------------
    Every rendered paragraph is identified by its stable ``para_id``.  When a
    ``para_id`` resolves to a ``ParaModel`` in the semantic model the paragraph
    text is replaced with the updated value.  When not found, the original text
    from the XML prototype is kept and ``LAYOUT_BLOCK_MISSING_PARA_ID`` is logged.

    Column / section preservation
    ------------------------------
    Column breaks (``w:br type="column"``) and embedded section properties
    (``w:sectPr`` inside ``w:pPr``) are **not** stripped so that newspaper-
    column layouts (e.g. sample 31) retain their two-column visual structure.
    Only ``w:lastRenderedPageBreak`` elements are removed (always stale).

    Unbound new content
    -------------------
    LLM-added paragraphs that exist in ``all_paras`` but have no entry in
    ``layout_blocks`` (e.g. extra bullets with ``para_id=""``) are not placed.
    Their count is logged as ``LAYOUT_UNBOUND_CONTENT_NOT_RENDERED``.
    """
    from lxml import etree

    _log.debug("LAYOUT_BLOCK_RENDERER_USED: rendering %d blocks", len(doc.layout_blocks))  # type: ignore[arg-type]

    para_lookup = _build_para_lookup(doc)

    # Collect para_ids referenced by layout_blocks to detect unbound content.
    lb_para_ids: set[str] = set()
    for block in doc.layout_blocks:  # type: ignore[union-attr]
        if isinstance(block, LayoutTableBlock):
            lb_para_ids.update(pid for pid in block.para_ids if pid)
        elif isinstance(block, LayoutParagraphBlock) and block.para_id:
            lb_para_ids.add(block.para_id)

    # Detect unbound content: paragraphs in all_paras not captured by layout_blocks.
    # Two cases:
    #   (a) para_id set but not in lb_para_ids — updated para whose layout slot was lost
    #   (b) para_id="" with non-empty text — new paragraph from clone_as (LLM extra bullet)
    unbound_count = sum(
        1 for pm in (doc.all_paras or [])
        if (
            (pm.para_id and pm.para_id not in lb_para_ids)
            or (not pm.para_id and pm.text.strip())
        )
    )
    if unbound_count:
        _log.debug(
            "LAYOUT_UNBOUND_CONTENT_NOT_RENDERED: %d paragraphs not in layout_blocks",
            unbound_count,
        )

    for block in doc.layout_blocks:  # type: ignore[union-attr]
        if isinstance(block, LayoutTableBlock):
            tbl_elem = etree.fromstring(block.xml_proto_xml)
            all_p = tbl_elem.findall(f".//{{{_W}}}p")
            patched = 0
            for para_id, p_elem in zip(block.para_ids, all_p):
                pm = para_lookup.get(para_id)
                if pm is not None:
                    _set_para_text(p_elem, pm.text)
                    patched += 1
                else:
                    _log.debug("LAYOUT_BLOCK_MISSING_PARA_ID: table para_id=%r", para_id)
                    # keep original text — surplus / unmatched template cells
            _log.debug(
                "TABLE_BLOCK_XML_PATCHED: table_id=%r  patched=%d/%d",
                block.table_id, patched, len(block.para_ids),
            )
            elem: Any = tbl_elem

        else:
            # LayoutParagraphBlock
            if not block.xml_proto_xml:
                # No XML prototype: fall back to para_builder or runtime xml_proto
                pm = para_lookup.get(block.para_id) if block.para_id else None
                if pm is None:
                    continue
                if pm.style.xml_proto is not None:
                    from copy import deepcopy
                    elem = deepcopy(pm.style.xml_proto)
                    _strip_last_rendered_page_breaks(elem)
                    _set_para_text(elem, pm.text)
                elif pm.paragraph_profile is not None:
                    from tailor.compiler.para_builder import build_para_element
                    elem = build_para_element(pm)
                else:
                    _log.debug(
                        "LAYOUT_BLOCK_RENDERER_FALLBACK: para_id=%r has no xml_proto or profile",
                        block.para_id,
                    )
                    continue
                pass  # (keepNext injection removed — was causing extra pages)
            else:
                elem = etree.fromstring(block.xml_proto_xml)
                _strip_last_rendered_page_breaks(elem)
                # Strip single-column sectPr (page-size-only section breaks) to prevent
                # stale section boundaries from creating forced page breaks.  Multi-column
                # sectPr (w:cols w:num≥2) are preserved for newspaper-style column layouts.
                _strip_non_column_section_break(elem)
                pm = para_lookup.get(block.para_id) if block.para_id else None
                if pm is not None:
                    _set_para_text(elem, pm.text)
                    _log.debug("PARAGRAPH_BLOCK_XML_PATCHED: para_id=%r", block.para_id)
                else:
                    if block.para_id:
                        _log.debug("LAYOUT_BLOCK_MISSING_PARA_ID: para_id=%r", block.para_id)
                    # Structural/orphan paragraph — insert verbatim (original text kept)

        if sectPr is not None:
            sectPr.addprevious(elem)
        else:
            body.append(elem)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def render_docx(doc: ResumeDocument, template_path: str, output_path: str) -> None:
    """Render *doc* to *output_path*, using *template_path* for styles and page setup.

    Parameters
    ----------
    doc:
        Updated ResumeDocument (produced by updater.apply_tailored).
    template_path:
        Path to the original DOCX template.  Styles, page geometry, headers,
        and footers are inherited from here.
    output_path:
        Destination path for the rendered DOCX.

    Raises
    ------
    ValueError
        If any paragraph in doc.all_paras has no xml_proto (cannot render).
    """
    # Start from a copy of the template so styles and document settings are preserved
    shutil.copy(template_path, output_path)
    d = Document(output_path)
    # PDF-sourced docs use numPr-based bullets; patch Symbol \uf0b7 → Unicode •
    # so LibreOffice (which lacks Symbol font) renders the bullet marker.
    if doc.source_kind == "pdf":
        _patch_bullet_numbering(d)
    body = d.element.body

    # Remove ALL body children, then re-append sectPr last.
    # Removing only w:p and w:tbl would leave nested paragraphs inside
    # w:sdt (content controls) which survive and produce duplicate content.
    sectPr = body.find(f"{{{_W}}}sectPr")
    for child in list(body):
        body.remove(child)
    if sectPr is not None:
        body.append(sectPr)

    # Layout-blocks path: activated when layout_blocks are present and either:
    #   (a) USE_LAYOUT_BLOCK_RENDERER=true  — explicit flag, enforces physical
    #       layout order even at runtime; LLM-added unbound content is not
    #       placed (logged as LAYOUT_UNBOUND_CONTENT_NOT_RENDERED).
    #   (b) No runtime xml_proto objects detected — deserialized from DB;
    #       para_builder fallback would lose all DOCX formatting otherwise.
    #
    # PDF sources never use this path (layout_blocks is only built for DOCX).
    if doc.layout_blocks is not None and doc.source_kind != "pdf":
        from tailor.config import USE_LAYOUT_BLOCK_RENDERER
        _has_runtime_xml = any(
            pm.style.xml_proto is not None for pm in doc.all_paras[:10]
        )
        _use_lb = USE_LAYOUT_BLOCK_RENDERER or not _has_runtime_xml
        if _use_lb:
            _render_from_layout_blocks(doc, body, sectPr)
            d.save(output_path)
            return
        _log.debug(
            "LAYOUT_BLOCK_RENDERER_FALLBACK: layout_blocks present but runtime xml_proto "
            "detected and USE_LAYOUT_BLOCK_RENDERER=false — using default render path"
        )

    # For PDF-sourced documents, override the template page geometry with the
    # source PDF's paper size and margins so the round-trip page count is stable.
    if doc.source_kind == "pdf" and sectPr is not None:
        _apply_pdf_page_geometry(sectPr, doc.layout)

    # PDF sources with a detected two-column layout: render as a borderless
    # two-cell table so that sidebar and main content are placed in separate
    # columns with correct widths, indentation, and background colours.
    if doc.source_kind == "pdf" and doc.layout.column_split_x is not None:
        _render_pdf_two_col(doc, body, sectPr, doc_part=d.part)
        d.save(output_path)
        return

    # Use the table-aware body_items path only when the document actually has
    # TableBlock entries.  For flat DOCX documents (no tables) body_items holds
    # only ParaModel objects reflecting the ORIGINAL parse; structural changes
    # made by apply_tailored (extra bullets, dropped roles) live in all_paras.
    # PDF-sourced / deserialised documents have body_items=None.
    has_table_blocks = doc.body_items is not None and any(
        isinstance(item, TableBlock) for item in doc.body_items
    )
    render_items = doc.body_items if has_table_blocks else doc.all_paras

    # For flat (non-table) DOCX documents, preserve embedded w:sectPr elements
    # in header paragraphs.  In templates that use Word's native multi-column
    # section formatting, the header-section boundary is encoded as a w:sectPr
    # inside a paragraph's w:pPr.  Stripping it (the normal behaviour) would
    # collapse the full-width merged header into the same column grid as the
    # body, misplacing the name/title block into the narrow left column.
    # Using object identity (id()) is safe: apply_tailored always forwards the
    # original header_paras objects unchanged.
    header_para_ids: frozenset[int] = (
        frozenset(id(pm) for pm in doc.header_paras)
        if not has_table_blocks and doc.header_paras
        else frozenset()
    )

    # DOCX templates with a native two-column body (w:cols num=2): convert the
    # column layout to a borderless table so that the left (sidebar) and right
    # (main) columns each form independent text streams that stay in their
    # column across page breaks.  Word's sequential w:cols flow causes sidebar
    # content to spill into the right column and main content to jump to the
    # wrong column after a page overflow — both are fixed by the table approach.
    if doc.source_kind == "docx" and not has_table_blocks and doc.header_paras:
        cols_elem = sectPr.find(f"{{{_W}}}cols") if sectPr is not None else None
        col_elems = cols_elem.findall(f"{{{_W}}}col") if cols_elem is not None else []
        _left_w_raw = int(col_elems[0].get(f"{{{_W}}}w", "0")) if len(col_elems) == 2 else 0
        _right_w_raw = int(col_elems[1].get(f"{{{_W}}}w", "0")) if len(col_elems) == 2 else 0
        # Only treat as a sidebar layout (and convert to table) when the left
        # column is substantially narrower than the right — ratio < 0.6 covers
        # typical sidebar templates (veeva_03: 2848/7362 ≈ 0.39) while leaving
        # balanced two-column layouts (ratios 0.7–1.3) in normal w:cols flow.
        _is_unequal_two_col = (
            cols_elem is not None
            and cols_elem.get(f"{{{_W}}}num") == "2"
            and len(col_elems) == 2
            and _left_w_raw > 0
            and _right_w_raw > 0
            and _left_w_raw < _right_w_raw * 0.6
        )
        if _is_unequal_two_col:
            left_w = _left_w_raw
            right_w = _right_w_raw
            col_space = int(col_elems[0].get(f"{{{_W}}}space", "0"))
            # Switch body section to single-column flow; the table provides
            # the two-column layout instead.
            sectPr.remove(cols_elem)
            _render_docx_native_two_col(
                doc, body, sectPr, left_w, right_w, col_space, header_para_ids
            )
            d.save(output_path)
            return

    for item in render_items:
        if isinstance(item, TableBlock):
            _render_table_block(item, doc, body, sectPr)
        else:
            _render_para(item, body, sectPr, preserve_section_break=id(item) in header_para_ids)

    d.save(output_path)
