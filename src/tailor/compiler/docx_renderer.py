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

def _ensure_continuous(sectPr) -> None:
    """Set w:type w:val='continuous' on *sectPr*, creating the element if absent.

    Called when a header-section boundary sectPr (nextPage by default) must be
    preserved to define the 1-column header / 2-column body topology, but must not
    create an unwanted hard page break.  Converting to continuous keeps the section
    boundary intact while allowing the header and body to flow on the same page.
    """
    from lxml import etree as _etree
    _type = sectPr.find(f"{{{_W}}}type")
    if _type is None:
        _type = _etree.SubElement(sectPr, f"{{{_W}}}type")
    _type.set(f"{{{_W}}}val", "continuous")


def _strip_non_column_section_break(
    p_elem,
    main_pgSz_w: "str | None" = None,
    main_pgSz_h: "str | None" = None,
    main_is_multicolumn: bool = False,
) -> None:
    """Remove w:sectPr from paragraph pPr ONLY when it acts as a pure page-break marker.

    Section properties embedded in a paragraph's pPr mark the end of a document
    section.  Four categories must be preserved intact:

    1. Multi-column sectPr (w:cols w:num ≥ 2) — newspaper-column layouts
       (e.g. sample 31) that define 2- or 3-column body sections.

    2. Continuous section breaks (w:type w:val="continuous") regardless of column
       count — these create same-page layout transitions such as a 1-column header
       region followed by a 2-column body (e.g. sample 3: 33 pt white name in a
       full-width banner above the 2-col sidebar+experience layout).  Stripping a
       continuous 1-col sectPr would collapse that boundary, placing the large
       banner text inside the narrow sidebar column and breaking the topology.

    3. When the main document body is 2+ column (main_is_multicolumn=True): ALL
       embedded single-column sectPrs define the header-section boundary before the
       multi-column body.  Stripping them would place the header content (name, photo,
       title) inside the narrow 2-column body, causing vertical text fragmentation.
       This applies to samples 19, 20, 23 where the main sectPr uses 2 columns.

    4. sectPr whose pgSz matches the main document page size — these are legitimate
       section-structure boundaries (e.g. a 1-column header section before a 2-column
       body in templates like samples 17, 28 where the main sectPr is 1-column but
       subsequent embedded sectPrs define 2-column body regions).  Stale sectPrs
       (e.g. a US-Letter sectPr inside an A4 template) have DIFFERENT page dimensions
       and are still stripped.

    All other sectPr (nextPage / evenPage / oddPage with single-column AND different
    page size, in a 1-column document body) are stale page-break markers from the
    template's last render and are stripped so content flows without forced breaks.
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
            return  # multi-column layout — preserve intact
    # Continuous section breaks define same-page layout topology (1-col header →
    # 2-col body etc.).  Never strip them — doing so collapses the section boundary
    # and places header-area content inside the narrow sidebar column.
    type_elem = sectPr.find(f"{{{_W}}}type")
    if type_elem is not None and type_elem.get(f"{{{_W}}}val", "") == "continuous":
        return  # continuous break — preserve (no page break, defines layout geometry)
    # When the main body is multi-column, embedded 1-col sectPrs are header boundaries.
    # Stripping them collapses the header (name/photo) into the 2-col body → fragmented.
    # Convert to continuous so the boundary is preserved without creating a page break.
    if main_is_multicolumn:
        _ensure_continuous(sectPr)
        return  # preserve as continuous: header-to-2-col-body section boundary
    # Same-pgSz sectPrs are legitimate header-to-body section boundaries.
    # Stale sectPrs (template page-size switches, e.g. US-Letter inside A4) have
    # different dimensions and fall through to the strip below.
    # Convert to continuous to avoid creating an unwanted page break while still
    # preserving the section layout boundary.
    if main_pgSz_w is not None and main_pgSz_h is not None:
        this_pgSz = sectPr.find(f"{{{_W}}}pgSz")
        if this_pgSz is not None:
            this_w = this_pgSz.get(f"{{{_W}}}w")
            this_h = this_pgSz.get(f"{{{_W}}}h")
            if this_w == main_pgSz_w and this_h == main_pgSz_h:
                _ensure_continuous(sectPr)
                return  # same page dimensions → structural boundary, preserve as continuous
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


def _strip_text_wrapping_breaks(p_elem) -> None:
    """Remove w:br type='textWrapping' elements from runs in a paragraph.

    Template paragraphs sometimes encode multi-line content using soft-return
    breaks (w:br type='textWrapping').  When LLM text replaces the original
    content, _set_para_text distributes the new (often shorter) text
    proportionally across the original runs.  Each run then gets only a few
    characters, and the surviving br elements force a line break between each
    tiny fragment — producing "character-by-character" rendering with 1-3 chars
    per line.  Stripping the breaks before text replacement lets the new content
    flow naturally at full paragraph width.

    Only called when content is being actively replaced (pm is not None), so
    verbatim-preserved paragraphs keep their original break structure.
    """
    for r_elem in list(p_elem.findall(f"{{{_W}}}r")):
        for br in list(r_elem.findall(f"{{{_W}}}br")):
            if br.get(f"{{{_W}}}type") == "textWrapping":
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


def _clear_sdt_placeholder(p_elem) -> None:
    """Flatten run-level SDT content controls inside a patched paragraph.

    Some Word templates wrap each text segment in a w:sdt content control so
    that the template author can tag placeholders.  When we patch the
    paragraph text via _set_para_text the runs inside w:sdtContent are updated,
    but LibreOffice may still render the old placeholder text from the SDT's
    w:placeholder docPart reference even after w:showingPlcHdr is removed.

    The most reliable fix is to flatten the SDT: replace each w:sdt child of
    the paragraph with the runs from its w:sdtContent.  This removes all SDT
    overhead and lets LibreOffice render our updated runs directly.

    Only run-level SDTs (direct w:sdt children of w:p) are flattened.
    Paragraph-level SDTs (where w:p is inside w:sdtContent) are handled by
    clearing w:showingPlcHdr from the enclosing w:sdt instead, because those
    SDTs span the entire paragraph and flattening them would require replacing
    the paragraph itself — a much larger structural change.
    """
    sdt_ns = f"{{{_W}}}sdt"
    sdt_pr_ns = f"{{{_W}}}sdtPr"
    showing_ns = f"{{{_W}}}showingPlcHdr"
    sdt_content_ns = f"{{{_W}}}sdtContent"

    # Pattern A: p_elem lives inside w:sdtContent — clear showingPlcHdr only.
    parent = p_elem.getparent()
    if parent is not None and parent.tag == sdt_content_ns:
        sdt = parent.getparent()
        if sdt is not None and sdt.tag == sdt_ns:
            sdt_pr = sdt.find(sdt_pr_ns)
            if sdt_pr is not None:
                showing = sdt_pr.find(showing_ns)
                if showing is not None:
                    sdt_pr.remove(showing)
        return  # paragraph-level SDT handled; skip run-level flattening

    # Pattern B: flatten each direct w:sdt child of the paragraph.
    # Find SDT children in reverse order so index-based insertion stays valid.
    sdt_children = p_elem.findall(sdt_ns)
    for sdt_elem in sdt_children:
        sdt_content = sdt_elem.find(sdt_content_ns)
        if sdt_content is None:
            continue
        # Collect runs (and other inline content) from sdtContent.
        runs = list(sdt_content)
        if not runs:
            continue
        # Insert the runs at the position of the sdt element.
        idx = list(p_elem).index(sdt_elem)
        for offset, run in enumerate(runs):
            p_elem.insert(idx + offset, run)
        p_elem.remove(sdt_elem)


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
    # Exception: when the target text is empty (""), also clear VML text so
    # that paragraphs blanked by the updater (e.g. duplicated summary placeholders)
    # do not retain their original VML text-box content in the output.
    if not text and vml_indices:
        for i in vml_indices:
            r = all_runs[i]
            for t in r.findall(f".//{{{_W}}}t"):
                t.text = ""
    elif vml_text_str and text.startswith(vml_text_str):
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

    # Tab-column overflow guard: when a paragraph uses pure-tab separator runs
    # (no <w:t>, only <w:tab/>) as column dividers and the new text is significantly
    # longer than the original total, proportional distribution would cut words
    # mid-column.  Put the full text in the first content run and clear the rest so
    # it wraps within the first column rather than being sliced across columns.
    # This applies to skills-grid rows when the LLM provides long categorised lines.
    # It does NOT trigger for round-trip identical text (same length) or when the
    # text is shorter — those cases use proportional distribution as before.
    _pure_tab_idx: set[int] = {
        i for i, r in enumerate(all_runs)
        if not any((t.text or "") for t in r.findall(f"{{{_W}}}t"))
        and r.findall(f"{{{_W}}}tab")
    }
    if _pure_tab_idx and len(text) > total_orig * 1.1:
        _first_ci = content_indices[0]
        _set_run_text(all_runs[_first_ci], text)
        for ci in content_indices[1:]:
            _set_run_text(all_runs[ci], "")
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

    # Strip <w:tab/> elements from runs that also carry text content.
    # Tab-column role headers and bullet paragraphs (e.g. \u25cf + <w:tab/> +
    # text) use tab stops for original alignment.  After setting new LLM text
    # the alignment comes from the text itself, so residual <w:tab/> elements
    # inside text-bearing runs produce mid-word tab characters when read back.
    # Pure-tab separator runs (no <w:t> text) must be left intact so that
    # contact-row paragraphs (email <tab> phone <tab> LinkedIn) keep their
    # even distribution across tab stops.
    for r in all_runs:
        has_text_content = any((t.text or "") for t in r.findall(f"{{{_W}}}t"))
        if has_text_content:
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
        _clear_sdt_placeholder(clone)
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


def _strip_override_table_style_font_compat(d) -> None:
    """Neutralize style-template settings that cause LibreOffice to suppress paragraphs.

    Two problematic settings are removed from the style template when rendering
    PDF-sourced two-column layouts:

    1. overrideTableStyleFontSizeAndJustification compat setting (val='1'):
       causes LibreOffice to ignore explicit run-level font sizes in table cells,
       making large section headings and role entries invisible.

    2. w:tblLayout w:type="fixed" in the default TableNormal style:
       combined with fixed-width cell tables, this causes LibreOffice to clip
       indented paragraph content outside the cell's paint region.  Removing
       the tblLayout element from the style lets individual table-level
       tblLayout (already set on our generated table) govern layout exclusively.
    """
    try:
        from docx.oxml.ns import qn

        # Remove the compat setting
        settings_part = d.settings.element
        compat = settings_part.find(qn("w:compat"))
        if compat is not None:
            for cs in list(compat.findall(qn("w:compatSetting"))):
                if cs.get(qn("w:name")) == "overrideTableStyleFontSizeAndJustification":
                    compat.remove(cs)

        # Remove tblLayout from the default TableNormal style
        styles_part = d.part.styles._element
        for style in styles_part.findall(qn("w:style")):
            if (
                style.get(qn("w:type")) == "table"
                and style.get(qn("w:default")) == "1"
            ):
                tblPr = style.find(qn("w:tblPr"))
                if tblPr is not None:
                    for tblLayout in list(tblPr.findall(qn("w:tblLayout"))):
                        tblPr.remove(tblLayout)
                break

        # Remove w:sz / w:szCs from docDefaults rPrDefault.
        # When docDefaults specifies a small font size (e.g. sz=22 = 11pt) and a
        # table-cell paragraph carries an explicit large font (e.g. sz=60 = 30pt),
        # LibreOffice positions the glyph relative to the small default baseline
        # rather than the paragraph's own font metrics, placing the text outside
        # the visible line box (invisible).  Removing the default sz leaves font
        # sizing entirely to per-paragraph / per-run properties, which LibreOffice
        # handles correctly for all indent and font-size combinations.
        styles_elem = d.part.styles._element
        doc_defaults = styles_elem.find(qn("w:docDefaults"))
        if doc_defaults is not None:
            rpr_default = doc_defaults.find(qn("w:rPrDefault"))
            if rpr_default is not None:
                rpr = rpr_default.find(qn("w:rPr"))
                if rpr is not None:
                    for tag in (qn("w:sz"), qn("w:szCs")):
                        for el in list(rpr.findall(tag)):
                            rpr.remove(el)
    except Exception:
        pass  # graceful: proceed without stripping if styles can't be modified


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


def _render_pdf_section_row_table(doc: "ResumeDocument", body, sectPr, doc_part=None) -> None:
    """Render a section-row PDF as a DOCX table with one row per section.

    The PDF has a two-column table where left cell = section label and
    right cell = section body content.  Each section gets its own table
    row so that labels and bodies stay side-by-side (vs. all labels in one
    cell and all bodies in another, which is the independent-column layout).
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    layout = doc.layout

    pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    page_w_twips = int(pgSz.get(f"{{{_W}}}w", "12240")) if pgSz is not None else 12240
    left_margin_twips = int(pgMar.get(f"{{{_W}}}left", "1440")) if pgMar is not None else 1440
    right_margin_twips = int(pgMar.get(f"{{{_W}}}right", "1440")) if pgMar is not None else 1440

    left_w = layout.left_col_width_twips or (page_w_twips // 3)
    right_w_max = page_w_twips - right_margin_twips - left_w
    right_w = min(layout.right_col_width_twips or right_w_max, right_w_max)
    right_w = max(right_w, 2000)
    total_w = left_w + right_w

    # Build the section-row table
    tbl = etree.Element(f"{{{_W}}}tbl")
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW_elem = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW_elem.set(f"{{{_W}}}w", str(total_w))
    tblW_elem.set(f"{{{_W}}}type", "dxa")
    tblInd = etree.SubElement(tblPr, f"{{{_W}}}tblInd")
    tblInd.set(f"{{{_W}}}w", str(-left_margin_twips))
    tblInd.set(f"{{{_W}}}type", "dxa")
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

    def _add_top_border(tcPr_elem):
        """Add a thin top separator line to a table cell."""
        tc_brd = etree.SubElement(tcPr_elem, f"{{{_W}}}tcBorders")
        top_brd = etree.SubElement(tc_brd, f"{{{_W}}}top")
        top_brd.set(f"{{{_W}}}val", "single")
        top_brd.set(f"{{{_W}}}sz", "6")
        top_brd.set(f"{{{_W}}}color", "000000")

    def _shift_para_indent(p_elem, delta_twips: int) -> None:
        """Subtract delta_twips from the left indent of a w:p element (floor at 0)."""
        if delta_twips <= 0:
            return
        pPr = p_elem.find(f"{{{_W}}}pPr")
        if pPr is None:
            return
        ind = pPr.find(f"{{{_W}}}ind")
        if ind is not None:
            cur = int(ind.get(f"{{{_W}}}left", "0"))
            ind.set(f"{{{_W}}}left", str(max(0, cur - delta_twips)))

    def _section_right_paras(section):
        """Yield (ParaModel, is_role_header) for all right-cell paras in a section."""
        if section.semantic_type == "experience" and section.roles:
            _has_orphan = any(bp.semantic == "role_header" for bp in section.body_paras)
            if _has_orphan:
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip():
                        yield bp
            for role in section.roles:
                yield role.header
                yield from role.meta_lines
                yield from role.bullets
        else:
            yield from section.body_paras

    def _min_indent_twips(section) -> int:
        """Return the minimum indent across all right-cell paras in a section (twips)."""
        vals = [
            int(pm.paragraph_profile.indent_left_pt * 20)
            for pm in _section_right_paras(section)
            if pm.paragraph_profile and pm.paragraph_profile.indent_left_pt > 0
        ]
        return min(vals) if vals else 0

    for section in doc.sections:
        tr = etree.SubElement(tbl, f"{{{_W}}}tr")

        # Compute per-section indent baseline to normalise all body content
        # to the same visual left edge within the right cell.  Different PDF
        # templates place right-column content at varying x offsets (e.g. skills
        # at x=231 vs. role headers at x=215); subtracting the section minimum
        # makes each section's content start flush at the right-cell left edge.
        sec_min_ind = _min_indent_twips(section)

        # Left cell: section heading
        left_tc = etree.SubElement(tr, f"{{{_W}}}tc")
        left_tcPr = etree.SubElement(left_tc, f"{{{_W}}}tcPr")
        left_tcW = etree.SubElement(left_tcPr, f"{{{_W}}}tcW")
        left_tcW.set(f"{{{_W}}}w", str(left_w))
        left_tcW.set(f"{{{_W}}}type", "dxa")
        _add_top_border(left_tcPr)

        heading_elem = build_para_element(section.heading, doc_part=doc_part)
        pPr = heading_elem.find(f"{{{_W}}}pPr")
        if pPr is not None:
            ind = pPr.find(f"{{{_W}}}ind")
            if ind is not None:
                cur = int(ind.get(f"{{{_W}}}left", "0"))
                ind.set(f"{{{_W}}}left", str(cur + left_margin_twips))
            else:
                new_ind = etree.SubElement(pPr, f"{{{_W}}}ind")
                new_ind.set(f"{{{_W}}}left", str(left_margin_twips))
        left_tc.append(heading_elem)

        # Right cell: section body content
        right_tc = etree.SubElement(tr, f"{{{_W}}}tc")
        right_tcPr = etree.SubElement(right_tc, f"{{{_W}}}tcPr")
        right_tcW = etree.SubElement(right_tcPr, f"{{{_W}}}tcW")
        right_tcW.set(f"{{{_W}}}w", str(right_w))
        right_tcW.set(f"{{{_W}}}type", "dxa")
        _add_top_border(right_tcPr)

        def _append(pm, combined_text=None):
            """Build and append a para element with normalised indent."""
            src = pm.with_text(combined_text) if combined_text else pm
            p_elem = build_para_element(src, doc_part=doc_part)
            _shift_para_indent(p_elem, sec_min_ind)
            right_tc.append(p_elem)

        if section.semantic_type == "experience" and section.roles:
            _has_orphan = any(bp.semantic == "role_header" for bp in section.body_paras)
            if _has_orphan:
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip():
                        _append(bp)
            for role in section.roles:
                if role.header_extra and "|" not in role.header.text:
                    _combined = role.header.text.strip() + " | " + " | ".join(
                        he.text.strip() for he in role.header_extra if he.text.strip()
                    )
                    _append(role.header, _combined)
                else:
                    _append(role.header)
                for pm in role.meta_lines:
                    _append(pm)
                for pm in role.bullets:
                    _append(pm)
        else:
            for pm in section.body_paras:
                _append(pm)

        if not right_tc.findall(f"{{{_W}}}p"):
            etree.SubElement(right_tc, f"{{{_W}}}p")

    # Render header paras (name, title, license) as full-width above the table
    header_elements = [build_para_element(pm, doc_part=doc_part) for pm in doc.header_paras]

    if sectPr is not None:
        for elem in header_elements:
            sectPr.addprevious(elem)
        sectPr.addprevious(tbl)
    else:
        for elem in header_elements:
            body.append(elem)
        body.append(tbl)


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
    right_margin_twips = (
        int(pgMar.get(f"{{{_W}}}right", "1440")) if pgMar is not None else 1440
    )

    # Column widths.  The table is pushed to the physical page left edge via
    # tblInd=-left_margin, so the table spans from x=0 to x=left_w+right_w.
    # right_w must not exceed page_w - right_margin - left_w; otherwise the
    # table overflows the right margin and LibreOffice clips the right cell.
    left_w = layout.left_col_width_twips or (page_w_twips // 3)
    right_w_max = page_w_twips - right_margin_twips - left_w
    right_w = min(
        layout.right_col_width_twips or right_w_max,
        right_w_max,
    )
    right_w = max(right_w, 2000)  # floor: prevent degenerate right cell
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
        p_elem = build_para_element(pm, doc_part=doc_part)
        # Shift all left-cell content right by left_margin_twips so it sits at
        # the same x position as in the source PDF.  paragraph indent_left_pt is
        # measured from the column origin (= page_margin_left), but the left
        # cell starts at the physical page left edge (x=0), so we add the margin.
        pPr = p_elem.find(f"{{{_W}}}pPr")
        if pPr is not None:
            ind = pPr.find(f"{{{_W}}}ind")
            if ind is not None:
                cur = int(ind.get(f"{{{_W}}}left", "0"))
                ind.set(f"{{{_W}}}left", str(cur + left_margin_twips))
            else:
                new_ind = etree.SubElement(pPr, f"{{{_W}}}ind")
                new_ind.set(f"{{{_W}}}left", str(left_margin_twips))
        left_tc.append(p_elem)
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
            _clear_sdt_placeholder(p_elem)
    # else: count mismatch (shouldn't happen unless LLM restructured the table);
    # fall through and insert the unmodified clone so the layout is preserved.

    if sectPr is not None:
        sectPr.addprevious(clone)
    else:
        body.append(clone)


def _zero_para_spacing(p_elem) -> None:
    """Strip vertical spacing from an empty spacer paragraph.

    Applied to empty paragraphs that follow the summary body anchor and precede
    the main table content (samples 13/14).  Zeroing space_before + space_after
    compresses the whitespace gap between the header summary and the table,
    allowing the table to start on page 1 rather than being pushed to page 2.
    """
    from lxml import etree as _etree
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        pPr = _etree.SubElement(p_elem, f"{{{_W}}}pPr")
        p_elem.insert(0, pPr)
    spacing = pPr.find(f"{{{_W}}}spacing")
    if spacing is None:
        spacing = _etree.SubElement(pPr, f"{{{_W}}}spacing")
    spacing.set(f"{{{_W}}}before", "0")
    spacing.set(f"{{{_W}}}after", "0")
    spacing.set(f"{{{_W}}}line", "240")
    spacing.set(f"{{{_W}}}lineRule", "auto")


def _make_inline_summary_para(reference_p_elem, text: str):
    """Create a <w:p> for inline summary injection.

    Clones the reference paragraph's XML structure (to inherit cell/section
    context), then strips heading-style and keepNext properties so the injected
    paragraph uses default body formatting, and sets the summary text.
    """
    from copy import deepcopy as _dc
    from lxml import etree as _etree
    new_p = _dc(reference_p_elem)
    pPr = new_p.find(f"{{{_W}}}pPr")
    if pPr is None:
        pPr = _etree.SubElement(new_p, f"{{{_W}}}pPr")
        new_p.insert(0, pPr)
    # Remove heading paragraph style so it inherits default body font
    pStyle = pPr.find(f"{{{_W}}}pStyle")
    if pStyle is not None:
        pPr.remove(pStyle)
    # Remove keepNext (avoids gluing summary to the next para)
    for kn in pPr.findall(f"{{{_W}}}keepNext"):
        pPr.remove(kn)
    # Add a small spacing_after so the summary breathes slightly
    spacing = pPr.find(f"{{{_W}}}spacing")
    if spacing is None:
        spacing = _etree.SubElement(pPr, f"{{{_W}}}spacing")
    spacing.set(f"{{{_W}}}after", "80")   # ~4pt
    spacing.set(f"{{{_W}}}line", "240")
    spacing.set(f"{{{_W}}}lineRule", "auto")
    _set_para_text(new_p, text)
    return new_p


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
_DML = "http://schemas.openxmlformats.org/drawingml/2006/main"
_WPS = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"


def _fix_anchor_layout_in_cell(cell_elem) -> None:
    """Selectively apply layoutInCell to anchored drawings inside a table cell.

    Full-page behindDoc backgrounds (cx ≥ 7 M EMU, cy ≥ 10 M EMU) get
    layoutInCell='0' so they use page-level coordinates and cover the full
    page regardless of cell boundaries.

    All other anchors are left UNCHANGED.  Foreground drawings (photo, contact
    icons, etc.) must keep their original layoutInCell value so they appear
    only on the page where their anchor paragraph's cell content is visible,
    not bleeding onto overflow continuation pages where the cell is empty.
    """
    for anchor in cell_elem.findall(f".//{{{_WP}}}anchor"):
        if anchor.get("behindDoc") != "1":
            continue
        ext = anchor.find(f"{{{_WP}}}extent")
        if ext is None:
            continue
        try:
            cx = int(ext.get("cx", "0"))
            cy = int(ext.get("cy", "0"))
        except ValueError:
            continue
        if cx >= 7_000_000 and cy >= 10_000_000:
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


# ---------------------------------------------------------------------------
# Layout-blocks: overflow-page continuation helpers  (Tasks 1–3)
# ---------------------------------------------------------------------------


def _is_solid_bg_anchor(anchor) -> bool:
    """True iff *anchor* is a full-page solid-colour background shape (no blip)."""
    if anchor is None or anchor.get("behindDoc") != "1":
        return False
    extent = anchor.find(f"{{{_WP}}}extent")
    if extent is None:
        return False
    try:
        cx = int(extent.get("cx", "0"))
        cy = int(extent.get("cy", "0"))
    except ValueError:
        return False
    if cx < 7_000_000 or cy < 10_000_000:
        return False
    if anchor.find(f".//{{{_DML}}}blip") is not None:
        return False
    if anchor.find(f".//{{{_WPS}}}txbx") is not None:
        return False
    return True


def _is_bg_anchor_any(anchor) -> bool:
    """True iff *anchor* is a full-page background shape — solid colour OR raster image.

    Wider than _is_solid_bg_anchor: also accepts blip-based backgrounds so that
    templates like sample 16 (background image inside a right-column layout block)
    can have their background cloned onto overflow pages.  Still rejects text boxes.
    """
    if anchor is None or anchor.get("behindDoc") != "1":
        return False
    extent = anchor.find(f"{{{_WP}}}extent")
    if extent is None:
        return False
    try:
        cx = int(extent.get("cx", "0"))
        cy = int(extent.get("cy", "0"))
    except ValueError:
        return False
    if cx < 7_000_000 or cy < 10_000_000:
        return False
    if anchor.find(f".//{{{_WPS}}}txbx") is not None:
        return False
    return True


def _find_and_move_bg_to_start(body, sectPr, allow_blip: bool = False) -> None:
    """Guarantee the page-background drawing anchors page 1.

    allow_blip=False (default, regular rendering path):
      Scans DIRECT body children only for solid-colour backgrounds (no blip).
      Used for single-column and native-two-col templates to avoid adding
      spurious clone paragraphs that would change roundtrip structure.

    allow_blip=True (two-col table conversion path):
      Scans ALL paragraphs including table cells and also accepts raster-image
      (blip) backgrounds.  Used after the 2-cell table is built so that blip
      backgrounds inside right-column content (sample 16) are cloned onto the
      overflow page.

    Body-level paragraphs: moved to body start (fixes sample 32 reversed-background
      pattern) then cloned before sectPr.
    Table-cell paragraphs: NOT moved (would break table); clone only.

    Safe no-op when no qualifying drawing is found.
    """
    from copy import deepcopy
    from lxml import etree

    _anchor_test = _is_bg_anchor_any if allow_blip else _is_solid_bg_anchor
    _para_iter = (
        body.findall(f".//{{{_W}}}p")   # recurse into cells when allow_blip
        if allow_blip
        else body.findall(f"{{{_W}}}p")  # direct children only otherwise
    )

    bg_para = None
    for p in _para_iter:
        for drawing in p.findall(f".//{{{_W}}}drawing"):
            anchor = drawing.find(f".//{{{_WP}}}anchor")
            if _anchor_test(anchor):
                bg_para = p
                break
        if bg_para is not None:
            break

    if bg_para is None:
        return

    # Determine if bg_para is a direct body child (not inside a table cell)
    bg_parent = bg_para.getparent()
    bg_is_body_level = bg_parent is not None and bg_parent.tag == f"{{{_W}}}body"

    # Move to body start only for body-level paragraphs not already first
    if bg_is_body_level:
        body_paras = [c for c in body if c.tag == f"{{{_W}}}p"]
        if body_paras and bg_para is not body_paras[0]:
            body.remove(bg_para)
            body_paras[0].addprevious(bg_para)

    # Clone with page-relative (0, 0) for overflow pages — always
    clone_p = deepcopy(bg_para)
    pPr = clone_p.find(f"{{{_W}}}pPr")
    if pPr is None:
        pPr = etree.SubElement(clone_p, f"{{{_W}}}pPr")
        clone_p.insert(0, pPr)
    spacing = pPr.find(f"{{{_W}}}spacing")
    if spacing is None:
        spacing = etree.SubElement(pPr, f"{{{_W}}}spacing")
    spacing.set(f"{{{_W}}}after", "0")
    spacing.set(f"{{{_W}}}line", "20")
    spacing.set(f"{{{_W}}}lineRule", "exact")
    for anchor in clone_p.findall(f".//{{{_WP}}}anchor"):
        for tag in ("positionH", "positionV"):
            pos = anchor.find(f"{{{_WP}}}{tag}")
            if pos is not None:
                pos.set("relativeFrom", "page")
                off_el = pos.find(f"{{{_WP}}}posOffset")
                if off_el is not None:
                    off_el.text = "0"

    if sectPr is not None:
        sectPr.addprevious(clone_p)
    else:
        body.append(clone_p)

    _log.debug(
        "OVERFLOW_BG: bg_para found (body_level=%s), cloned for overflow page",
        bg_is_body_level,
    )


def _find_single_col_break_idx(layout_blocks) -> "int | None":
    """Return index of the sole column-break paragraph block, or None.

    Returns None when there are 0 or 2+ column breaks — those layouts use a
    different structure and should be left to native w:cols handling.
    """
    breaks = [
        i for i, lb in enumerate(layout_blocks)
        if isinstance(lb, LayoutParagraphBlock)
        and lb.xml_proto_xml
        and 'type="column"' in lb.xml_proto_xml
    ]
    return breaks[0] if len(breaks) == 1 else None


def _header_paras_have_blip_bg(doc, layout_blocks) -> bool:
    """True if any HEADER-PARA block contains a full-page behindDoc image drawing.

    The critical distinction between blip-background templates:

    - Blip in a HEADER block → that paragraph is rendered as a standalone body
      element BEFORE the 2-cell table.  LibreOffice then creates a blank middle
      page from the interaction of the full-page image height with the table
      row — skip table conversion for these templates (samples 18, 23).

    - Blip in a NON-HEADER block → the paragraph stays INSIDE a table cell.
      LibreOffice handles this correctly and table conversion proceeds normally
      (sample 16, where the background image is inside the right-column content).
    """
    header_para_ids = frozenset(
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    )
    from lxml import etree as _et
    for lb in layout_blocks:
        if not isinstance(lb, LayoutParagraphBlock) or not lb.xml_proto_xml:
            continue
        if lb.para_id not in header_para_ids:
            continue
        if "behindDoc" not in lb.xml_proto_xml or "blip" not in lb.xml_proto_xml:
            continue
        try:
            elem = _et.fromstring(lb.xml_proto_xml)
            for anchor in elem.findall(f".//{{{_WP}}}anchor"):
                if anchor.get("behindDoc") != "1":
                    continue
                ext = anchor.find(f"{{{_WP}}}extent")
                if ext is None:
                    continue
                try:
                    cx = int(ext.get("cx", "0"))
                    cy = int(ext.get("cy", "0"))
                except ValueError:
                    continue
                if cx >= 7_000_000 and cy >= 10_000_000:
                    if anchor.find(f".//{{{_DML}}}blip") is not None:
                        return True
        except Exception:
            pass
    return False


def _render_block_into_elem(block, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn):
    """Render one LayoutParagraphBlock → lxml element (None if empty)."""
    from lxml import etree
    if not block.xml_proto_xml:
        return None
    elem = etree.fromstring(block.xml_proto_xml)
    _strip_last_rendered_page_breaks(elem)
    _strip_non_column_section_break(elem, main_pgSz_w, main_pgSz_h, main_is_multicolumn)
    _strip_column_break(elem)
    pm = para_lookup.get(block.para_id) if block.para_id else None
    if pm is not None:
        _strip_text_wrapping_breaks(elem)
        _set_para_text(elem, pm.text)
        _clear_sdt_placeholder(elem)
    return elem


def _extract_first_tblPr(layout_blocks):
    """Return a deep copy of tblPr from the first LayoutTableBlock, or None."""
    from copy import deepcopy
    from lxml import etree as _et
    for lb in layout_blocks:
        if isinstance(lb, LayoutTableBlock) and lb.xml_proto_xml:
            try:
                tbl_el = _et.fromstring(lb.xml_proto_xml)
                tblPr = tbl_el.find(f"{{{_W}}}tblPr")
                if tblPr is not None:
                    return deepcopy(tblPr)
            except Exception:
                pass
    return None


def _add_tbl_no_borders(tblPr) -> None:
    """Append no-border tblBorders to *tblPr*."""
    from lxml import etree
    tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
        brd.set(f"{{{_W}}}val", "none")


def _add_tbl_zero_cell_margins(tblPr) -> None:
    """Append zero tblCellMar to *tblPr*."""
    from lxml import etree
    tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        m = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
        m.set(f"{{{_W}}}w", "0")
        m.set(f"{{{_W}}}type", "dxa")


_EMU_PER_TWIP = 635  # 914400 EMU/inch ÷ 1440 twip/inch


def _fix_col_relative_anchors(elem, col_x_emu: int) -> None:
    """Convert posH relativeFrom='column' → relativeFrom='page' for behindDoc backgrounds.

    When w:cols is removed from sectPr (table conversion), any anchor that uses
    posH relativeFrom='column' loses its correct reference: the 'column' it
    addressed (e.g. the right column at x=5599 twips) is replaced by the single
    remaining column at x=margin_left.  This causes full-page background images
    to shift hundreds of twips to the left, damaging page 1 layout.

    Fix: for behindDoc, large-extent anchors, compute the absolute page x-position
    (col_x_emu + posH_offset_emu) and rewrite the anchor as page-relative.
    For very tall anchors (full-page height) also convert posV paragraph-relative
    to page-relative offset=0 so the background starts at the page top.

    Only behindDoc anchors are touched; foreground drawings are left unchanged.
    """
    for anchor in elem.findall(f".//{{{_WP}}}anchor"):
        if anchor.get("behindDoc") != "1":
            continue
        ext = anchor.find(f"{{{_WP}}}extent")
        if ext is None:
            continue
        try:
            cx = int(ext.get("cx", "0"))
            cy = int(ext.get("cy", "0"))
        except ValueError:
            continue
        if cx < 7_000_000 or cy < 10_000_000:
            continue
        # Fix posH: column-relative → absolute page position
        posH = anchor.find(f"{{{_WP}}}positionH")
        if posH is not None and posH.get("relativeFrom") == "column":
            off_el = posH.find(f"{{{_WP}}}posOffset")
            if off_el is not None:
                try:
                    abs_x = col_x_emu + int(off_el.text or "0")
                    posH.set("relativeFrom", "page")
                    off_el.text = str(abs_x)
                except ValueError:
                    pass
        # Fix posV: paragraph-relative → page top for full-page backgrounds
        posV = anchor.find(f"{{{_WP}}}positionV")
        if posV is not None and posV.get("relativeFrom") == "paragraph":
            posV.set("relativeFrom", "page")
            off_el = posV.find(f"{{{_WP}}}posOffset")
            if off_el is not None:
                off_el.text = "0"


def _detect_accent_color(layout_blocks) -> "str | None":
    """Return the most-used non-black/white hex color across all layout blocks.

    Used to pick a table column-divider color that matches the template's visual
    accent color (e.g. the green used for section headings and phone text in
    sample 16) when no source tblPr border style is available.
    """
    from lxml import etree as _et
    counts: dict[str, int] = {}
    for lb in layout_blocks:
        if not isinstance(lb, LayoutParagraphBlock) or not lb.xml_proto_xml:
            continue
        try:
            elem = _et.fromstring(lb.xml_proto_xml)
            for rPr in elem.findall(f".//{{{_W}}}rPr"):
                color = rPr.find(f"{{{_W}}}color")
                if color is None:
                    continue
                val = (color.get(f"{{{_W}}}val") or "").upper()
                if val and val not in ("AUTO", "000000", "FFFFFF"):
                    counts[val] = counts.get(val, 0) + 1
        except Exception:
            pass
    return max(counts, key=counts.get) if counts else None


def _split_right_col_identity(right_blocks):
    """Separate pre-content identity blocks from body-content blocks in the right column.

    In two-column templates (e.g. sample 16) the right column often starts with
    candidate identity paragraphs — the name, title, and associated empty spacers —
    before any actual content sections (PROFILE, EXPERIENCES, SKILLS, etc.).

    These identity paragraphs are:
    - CENTERED (w:jc val='center') — distinguishing them from body content
    - Empty spacers between the centered identity paragraphs
    - All appear BEFORE the first non-centered, non-empty body paragraph

    They must NOT go into the right table cell because when the table row spans
    pages the right cell begins fresh on the overflow page, causing the name and
    title to appear on page 2.  Instead they are silently dropped: the template's
    behindDoc composite background image already provides the visual representation
    of the name and title on page 1.  The blip anchor paragraph is kept as the
    FIRST element of the right cell so the background image still covers page 1.

    Returns (blip_blocks, identity_dropped, content_blocks):
      blip_blocks    — behindDoc large-extent anchor paragraphs (kept at cell front)
      identity_dropped — centered + empty paragraphs before first content (dropped)
      content_blocks — body content from first non-centered non-empty paragraph
    """
    from lxml import etree as _et

    blip_blocks: list = []
    dropped: list = []
    i = 0
    while i < len(right_blocks):
        blk = right_blocks[i]
        if not isinstance(blk, LayoutParagraphBlock) or not blk.xml_proto_xml:
            # Non-paragraph block (table) → treat as content start
            break
        try:
            elem = _et.fromstring(blk.xml_proto_xml)
        except Exception:
            break

        # Is it a large behindDoc anchor (background image)?
        is_blip = False
        for anc in elem.findall(f".//{{{_WP}}}anchor"):
            if anc.get("behindDoc") != "1":
                continue
            ext = anc.find(f"{{{_WP}}}extent")
            if ext is None:
                continue
            try:
                if int(ext.get("cx", "0")) >= 7_000_000 and int(ext.get("cy", "0")) >= 10_000_000:
                    is_blip = True
                    break
            except ValueError:
                pass
        if is_blip:
            blip_blocks.append(blk)
            i += 1
            continue

        # Is it a centered paragraph?
        pPr = elem.find(f"{{{_W}}}pPr")
        jc_val = ""
        if pPr is not None:
            jc_el = pPr.find(f"{{{_W}}}jc")
            if jc_el is not None:
                jc_val = jc_el.get(f"{{{_W}}}val", "")

        # Non-empty text in the paragraph?
        text = "".join(t.text or "" for t in elem.findall(f".//{{{_W}}}t")).strip()

        if jc_val == "center":
            # Centered paragraph (name, title, etc.) — drop
            dropped.append(blk)
            i += 1
            continue

        if not text:
            # Empty spacer before we've seen real content — drop
            dropped.append(blk)
            i += 1
            continue

        # First non-centered non-empty paragraph: content starts here
        break

    content_blocks = right_blocks[i:]
    _log.debug(
        "RIGHT_COL_IDENTITY_STRIP: blip=%d dropped=%d content=%d",
        len(blip_blocks), len(dropped), len(content_blocks),
    )
    return blip_blocks, dropped, content_blocks


def _render_layout_two_col_table(
    doc,
    body,
    sectPr,
    col_break_idx: int,
    main_pgSz_w,
    main_pgSz_h,
    main_is_multicolumn: bool,
) -> None:
    """Convert a native two-column layout into a 2-cell table for overflow stability.

    Task 2 — table borders:
        Uses the first LayoutTableBlock tblPr from each column (right preferred)
        so templates with styled borders (e.g. sample 16 green borders) retain
        them on overflow pages.  Falls back to no-borders when none found.

    Task 3 — same column / same lane:
        Left cell  → layout_blocks[:col_break_idx] excluding header-para blocks
        Right cell → layout_blocks[col_break_idx+1:]
        Header blocks (para_id in doc.header_paras) are rendered as normal
        paragraphs BEFORE the table so they are never inside a repeating row.

    Removes w:cols from sectPr so LibreOffice uses table layout, not native
    columns, for the rendered body — preventing the overflow column-jump.
    """
    from copy import deepcopy
    from lxml import etree

    para_lookup = _build_para_lookup(doc)

    # Column widths from sectPr w:cols/w:col elements
    _pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    _pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    _page_w = int(_pgSz.get(f"{{{_W}}}w", "12240")) if _pgSz is not None else 12240
    _mar_left = int(_pgMar.get(f"{{{_W}}}left", "0")) if _pgMar is not None else 0
    _mar_right = int(_pgMar.get(f"{{{_W}}}right", "0")) if _pgMar is not None else 0
    _text_area = max(_page_w - _mar_left - _mar_right, 1)

    # Parse column widths and inter-column space from sectPr.
    # _col_space is needed for both the width calculation and the column-x-offset
    # computation used when converting column-relative anchor positions to page-relative.
    left_w = right_w = _col_space = 0
    if sectPr is not None:
        cols_elem = sectPr.find(f"{{{_W}}}cols")
        if cols_elem is not None:
            col_elems = cols_elem.findall(f"{{{_W}}}col")
            if len(col_elems) == 2:
                try:
                    left_w = int(col_elems[0].get(f"{{{_W}}}w", "0"))
                    right_w = int(col_elems[1].get(f"{{{_W}}}w", "0"))
                    _col_space = int(col_elems[0].get(f"{{{_W}}}space", "0"))
                except ValueError:
                    pass
            elif len(col_elems) == 1:
                # Only first column explicit; compute second from text area (sample 16).
                try:
                    left_w = int(col_elems[0].get(f"{{{_W}}}w", "0"))
                    _col_space = int(col_elems[0].get(f"{{{_W}}}space", "0"))
                    right_w = max(_text_area - left_w - _col_space, 1)
                except ValueError:
                    pass
    _was_stretched = False
    if left_w == 0 or right_w == 0:
        left_w = right_w = _text_area // 2
        _was_stretched = True
    else:
        _col_sum = left_w + right_w
        if _col_sum < _text_area * 0.92:
            left_w = round(left_w / _col_sum * _text_area)
            right_w = _text_area - left_w
            _was_stretched = True

    # The column gap (_col_space) separates the two columns in the original w:cols
    # layout but is absent from the 2-cell table.  Without it, right-cell content
    # starts _col_space twips to the LEFT of where the original right column started,
    # causing text to overlap the insideV border and the left-column photo (#61/#60).
    # Fix: expand the right cell by _col_space and add that amount as a left cell
    # margin so the right-cell text starts at the original right-column X position.
    # Skip when the widths were already stretched to fill _text_area — in that case
    # the proportional scaling implicitly absorbed the gap.
    _right_col_gap = _col_space if (not _was_stretched and _col_space > 0) else 0

    # Precompute column left-edge x-positions in EMU for anchor-position correction.
    # When w:cols is removed (below), any anchor using posH relativeFrom='column'
    # would use the wrong reference; _fix_col_relative_anchors corrects this BEFORE
    # the table is rendered, preserving page-1 layout (sample 16 background image).
    _left_col_x_emu = _mar_left * _EMU_PER_TWIP
    _right_col_x_emu = _left_col_x_emu + (left_w + _col_space) * _EMU_PER_TWIP

    # Remove w:cols so LibreOffice does not double-apply column flow to the table
    if sectPr is not None:
        cols_to_remove = sectPr.find(f"{{{_W}}}cols")
        if cols_to_remove is not None:
            sectPr.remove(cols_to_remove)

    # All left-column blocks go into the left cell — INCLUDING the contact/header
    # paragraphs.  Previously these were extracted and rendered as body-level
    # paragraphs BEFORE the table, but that consumed vertical space on page 1
    # that caused LibreOffice to push the entire table to page 2.  With the
    # table starting at the very top of the body, the page-1 area is fully
    # available and the row splits correctly at the overflow point.
    # The identity (name, title) paragraphs in the right column are kept as-is:
    # they appear at the TOP of the right cell on page 1, and the overflow on
    # page 2 begins only after those paragraphs — they do NOT repeat on page 2.
    left_blocks = list(doc.layout_blocks[:col_break_idx])  # type: ignore[index]
    right_blocks = list(doc.layout_blocks[col_break_idx + 1:])  # type: ignore[index]

    # Build tblPr: inherit borders from source table when available (Task 2).
    # When no source table exists, use no visible borders but add an insideV
    # border matching the template's accent color as a column-divider line.
    # This preserves the vertical lane separator on overflow pages without
    # cloning the blip background image (which contains foreground elements).
    source_tblPr = _extract_first_tblPr(right_blocks) or _extract_first_tblPr(left_blocks)
    _accent = _detect_accent_color(left_blocks + right_blocks)

    tbl = etree.Element(f"{{{_W}}}tbl")
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW_el = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW_el.set(f"{{{_W}}}w", str(left_w + right_w + _right_col_gap))
    tblW_el.set(f"{{{_W}}}type", "dxa")
    tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
    tblLayout.set(f"{{{_W}}}type", "fixed")

    if source_tblPr is not None:
        for child_tag in ("tblBorders", "tblCellMar", "tblCellSpacing", "tblLook"):
            src_child = source_tblPr.find(f"{{{_W}}}{child_tag}")
            if src_child is not None:
                tblPr.append(deepcopy(src_child))
        if source_tblPr.find(f"{{{_W}}}tblBorders") is None:
            _add_tbl_no_borders(tblPr)
        if source_tblPr.find(f"{{{_W}}}tblCellMar") is None:
            _add_tbl_zero_cell_margins(tblPr)
    else:
        _add_tbl_no_borders(tblPr)
        # When the template uses an accent color (e.g. sample 16 green), use it
        # for the insideV border as a column divider on overflow pages.
        if _accent:
            tblBorders = tblPr.find(f"{{{_W}}}tblBorders")
            if tblBorders is not None:
                iv = tblBorders.find(f"{{{_W}}}insideV")
                if iv is not None:
                    iv.set(f"{{{_W}}}val", "single")
                    iv.set(f"{{{_W}}}sz", "6")
                    iv.set(f"{{{_W}}}color", _accent)
        _add_tbl_zero_cell_margins(tblPr)

    tr = etree.SubElement(tbl, f"{{{_W}}}tr")

    def _fill_cell(tc, blocks, col_x_emu: int, extra_paras=None) -> None:
        for blk in blocks:
            if isinstance(blk, LayoutTableBlock):
                tbl_el = etree.fromstring(blk.xml_proto_xml)
                for para_id, p_el in zip(blk.para_ids, tbl_el.findall(f".//{{{_W}}}p")):
                    pm = para_lookup.get(para_id)
                    if pm is not None:
                        _set_para_text(p_el, pm.text)
                        _clear_sdt_placeholder(p_el)
                tc.append(tbl_el)
            else:
                el = _render_block_into_elem(
                    blk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn
                )
                if el is not None:
                    # Convert column-relative background anchor positions to page-relative
                    # BEFORE w:cols is used by LibreOffice: prevents background image
                    # from shifting when the column reference changes (sample 16 fix).
                    _fix_col_relative_anchors(el, col_x_emu)
                    tc.append(el)
        # Append extra unbound paragraphs (LLM overflow content)
        if extra_paras:
            for pm in extra_paras:
                if pm.style.xml_proto is not None:
                    from copy import deepcopy
                    _xel = deepcopy(pm.style.xml_proto)
                    _strip_last_rendered_page_breaks(_xel)
                    _set_para_text(_xel, pm.text)
                    _clear_sdt_placeholder(_xel)
                    tc.append(_xel)
                elif pm.paragraph_profile is not None:
                    from tailor.compiler.para_builder import build_para_element
                    tc.append(build_para_element(pm))
        has_content = any(c.tag != f"{{{_W}}}tcPr" for c in list(tc))
        if not has_content:
            etree.SubElement(tc, f"{{{_W}}}p")
        _fix_anchor_layout_in_cell(tc)

    left_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    left_tcPr = etree.SubElement(left_tc, f"{{{_W}}}tcPr")
    left_tcW = etree.SubElement(left_tcPr, f"{{{_W}}}tcW")
    left_tcW.set(f"{{{_W}}}w", str(left_w))
    left_tcW.set(f"{{{_W}}}type", "dxa")
    etree.SubElement(left_tcPr, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
    _fill_cell(left_tc, left_blocks, _left_col_x_emu)

    right_tc = etree.SubElement(tr, f"{{{_W}}}tc")
    right_tcPr = etree.SubElement(right_tc, f"{{{_W}}}tcPr")
    right_tcW = etree.SubElement(right_tcPr, f"{{{_W}}}tcW")
    right_tcW.set(f"{{{_W}}}w", str(right_w + _right_col_gap))
    right_tcW.set(f"{{{_W}}}type", "dxa")
    if _right_col_gap > 0:
        # Cell-level left margin overrides the table-level zero margin, pushing
        # right-column text to start at the original right-column X position.
        right_tcMar = etree.SubElement(right_tcPr, f"{{{_W}}}tcMar")
        right_tcMar_left = etree.SubElement(right_tcMar, f"{{{_W}}}left")
        right_tcMar_left.set(f"{{{_W}}}w", str(_right_col_gap))
        right_tcMar_left.set(f"{{{_W}}}type", "dxa")
    etree.SubElement(right_tcPr, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
    _unbound_extra = [pm for pm in (doc.all_paras or []) if not pm.para_id and pm.text.strip()]
    _fill_cell(right_tc, right_blocks, _right_col_x_emu, extra_paras=_unbound_extra if _unbound_extra else None)

    if sectPr is not None:
        sectPr.addprevious(tbl)
    else:
        body.append(tbl)

    _log.debug(
        "LAYOUT_TWO_COL_TABLE: left=%d/%d-twips right=%d/%d-twips gap=%d-twips accent=%s",
        len(left_blocks), left_w, len(right_blocks), right_w, _right_col_gap, _accent,
    )


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

    # Extract main document page dimensions and column count to identify legitimate
    # section boundaries.  sectPrs that match these dimensions (or when the body is
    # multi-column) are header-section boundaries rather than stale page-size markers.
    _main_pgSz_w: "str | None" = None
    _main_pgSz_h: "str | None" = None
    _main_is_multicolumn: bool = False
    if sectPr is not None:
        _mpgSz = sectPr.find(f"{{{_W}}}pgSz")
        if _mpgSz is not None:
            _main_pgSz_w = _mpgSz.get(f"{{{_W}}}w")
            _main_pgSz_h = _mpgSz.get(f"{{{_W}}}h")
        _mcols = sectPr.find(f"{{{_W}}}cols")
        if _mcols is not None:
            _mnum = _mcols.get(f"{{{_W}}}num")
            if _mnum is not None and int(_mnum) >= 2:
                _main_is_multicolumn = True

    # Task 3 — same-lane column continuation: convert native w:cols to a 2-cell
    # table so right-column overflow stays in the right column on the next page.
    # Guards:
    #  • Ratio > 0.80: skip when col break is in the last 20% of blocks — those
    #    templates have a very short right column; the table's row-height coupling
    #    produces extra overflow pages instead of fixing them.
    #  • Blip background: skip when the template contains a full-page raster image
    #    as a behindDoc drawing.  Those templates have fixed-position layouts where
    #    the 2-cell table causes blank middle pages and PDF reading-order regressions.
    if _main_is_multicolumn:
        _col_break_idx = _find_single_col_break_idx(doc.layout_blocks)  # type: ignore[arg-type]
        if _col_break_idx is not None:
            _total_blocks = len(doc.layout_blocks)  # type: ignore[arg-type]
            _col_ratio = _col_break_idx / max(_total_blocks - 1, 1)
            # Skip only when HEADER blocks carry the blip image — those get rendered
            # as standalone body paragraphs before the table, causing blank middle
            # pages in LibreOffice (samples 18, 23).  Non-header blip images stay
            # inside a table cell and do not cause this problem (sample 16).
            _has_blip = _header_paras_have_blip_bg(doc, doc.layout_blocks)  # type: ignore[arg-type]
            if _col_ratio <= 0.80 and not _has_blip:
                _render_layout_two_col_table(
                    doc, body, sectPr,
                    _col_break_idx,
                    _main_pgSz_w, _main_pgSz_h,
                    _main_is_multicolumn,
                )
                # Solid-colour backgrounds only — blip images are composite and
                # must not be cloned (they contain foreground photo/icon content).
                _find_and_move_bg_to_start(body, sectPr)
                return
            if _has_blip:
                _log.debug(
                    "LAYOUT_TWO_COL_TABLE_SKIPPED: header block has blip bg — using native columns"
                )
            else:
                _log.debug(
                    "LAYOUT_TWO_COL_TABLE_SKIPPED: col_break_idx=%d total=%d ratio=%.2f > 0.80",
                    _col_break_idx, _total_blocks, _col_ratio,
                )

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

    # Inline summary injection: the updater sets _inline_summary_pid / _text when no
    # trailing empty header slots exist (e.g. sample 11 table-based template).
    _inline_pid: str | None = getattr(doc, "_inline_summary_pid", None)
    _inline_text: str | None = getattr(doc, "_inline_summary_text", None)

    # Post-summary spacer compression: find the summary body anchor para_id so
    # the renderer can zero-out spacing on subsequent empty paras (samples 13/14).
    # This prevents a chain of empty spacer paras from pushing the table to page 2.
    _summary_body_pid: str | None = next(
        (s.body_paras[-1].para_id
         for s in doc.sections
         if getattr(s, "section_id", "") == "sec_summary_inserted"
         and s.body_paras and s.body_paras[-1].para_id),
        None,
    )
    _compress_remaining: int = 0  # count of subsequent empty paras still to compress

    for block in doc.layout_blocks:  # type: ignore[union-attr]
        if isinstance(block, LayoutTableBlock):
            tbl_elem = etree.fromstring(block.xml_proto_xml)
            all_p = tbl_elem.findall(f".//{{{_W}}}p")
            patched = 0
            # Build a map from para_id to XML element for inline injection
            pid_to_pelem: dict[str, Any] = {}
            for para_id, p_elem in zip(block.para_ids, all_p):
                if para_id:
                    pid_to_pelem[para_id] = p_elem
                pm = para_lookup.get(para_id)
                if pm is not None:
                    _strip_text_wrapping_breaks(p_elem)
                    _set_para_text(p_elem, pm.text)
                    _clear_sdt_placeholder(p_elem)
                    patched += 1
                else:
                    _log.debug("LAYOUT_BLOCK_MISSING_PARA_ID: table para_id=%r", para_id)
                    # keep original text — surplus / unmatched template cells
            _log.debug(
                "TABLE_BLOCK_XML_PATCHED: table_id=%r  patched=%d/%d",
                block.table_id, patched, len(block.para_ids),
            )
            # Inline summary injection: insert new paragraph after the target
            # para in the table XML (for templates where the title is the last
            # header_para and there are no trailing empty slots, e.g. sample 11).
            if _inline_pid and _inline_text and _inline_pid in pid_to_pelem:
                _ref_p = pid_to_pelem[_inline_pid]
                _new_p = _make_inline_summary_para(_ref_p, _inline_text)
                _ref_p.addnext(_new_p)
                _inline_pid = None  # consume once
                _log.debug(
                    "INLINE_SUMMARY_INJECTED: new para inserted after para_id=%r",
                    getattr(doc, "_inline_summary_pid", "?"),
                )
            # Once we hit a table block, stop compressing spacers (table started).
            _compress_remaining = 0
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
                    _strip_text_wrapping_breaks(elem)
                    _set_para_text(elem, pm.text)
                    _clear_sdt_placeholder(elem)
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
                _strip_non_column_section_break(
                    elem, _main_pgSz_w, _main_pgSz_h, _main_is_multicolumn
                )
                pm = para_lookup.get(block.para_id) if block.para_id else None
                if pm is not None:
                    _strip_text_wrapping_breaks(elem)
                    _set_para_text(elem, pm.text)
                    _clear_sdt_placeholder(elem)
                    _log.debug("PARAGRAPH_BLOCK_XML_PATCHED: para_id=%r", block.para_id)
                else:
                    if block.para_id:
                        _log.debug("LAYOUT_BLOCK_MISSING_PARA_ID: para_id=%r", block.para_id)
                    # Structural/orphan paragraph — insert verbatim (original text kept)

            # Post-summary spacer compression: once the summary body anchor para
            # has been rendered, compress the spacing of subsequent empty paras.
            # This prevents a cluster of empty spacers from pushing the resume table
            # to page 2 (samples 13/14).  Compression stops at the first non-empty
            # para or when the counter exhausts.
            if block.para_id == _summary_body_pid:
                _compress_remaining = 8  # compress up to 8 following empty paras
            elif _compress_remaining > 0:
                _para_text = (pm.text.strip() if pm else "").strip()
                if not _para_text:
                    _zero_para_spacing(elem)
                    _compress_remaining -= 1
                    _log.debug(
                        "SUMMARY_SPACER_COMPRESSED: para_id=%r spacing zeroed",
                        block.para_id,
                    )
                else:
                    _compress_remaining = 0  # non-empty para: stop compressing

        if sectPr is not None:
            sectPr.addprevious(elem)
        else:
            body.append(elem)

    # NOTE: unbound paragraphs (para_id="") are intentionally NOT appended here.
    # Extra LLM content beyond template capacity is placed via the _extra_injections
    # mechanism in apply_tailored, which inserts LayoutParagraphBlock entries (or
    # modifies LayoutTableBlock XML) at the correct position so content stays inside
    # its section.  Appending unbound paras at document end caused experience bullets
    # to appear after Education/Technical Skills sections (samples 1, 6, 13, 14, 18).

    # Task 1 — page background: ensure background para anchors page 1 and clone
    # for any overflow continuation page.
    _find_and_move_bg_to_start(body, sectPr)


# ---------------------------------------------------------------------------
# Background color inheritance from blip image
# ---------------------------------------------------------------------------

def _maybe_insert_bg_rect(docx_path: str, body, sectPr) -> None:
    """Insert a solid-fill background rectangle for the overflow continuation page.

    Problem: LibreOffice does not render w:background on every page — only on
    page 1.  Templates that use a full-page behindDoc blip as background (e.g.
    sample 16) lose the background on page 2.  The blip cannot be cloned because
    it is a composite image containing the candidate photo, contact icons, and
    other foreground content.

    Solution: extract the DOMINANT BACKGROUND COLOR from the blip image (sampling
    five edge pixels away from photo/icon regions), then create a brand-new
    solid-fill DrawingML rectangle shape with NO image, NO text, NO stroke and
    insert it as the LAST body paragraph (just before sectPr).

    The rectangle uses:
      - behindDoc="1"   — behind all content, z-order lowest
      - layoutInCell="0" — page-relative positioning inside table cells
      - posH/posV relativeFrom="page" offset=0 — covers the full page from (0,0)
      - Solid fill = sampled hex color (e.g. #F8F8F6)
      - No stroke (a:noFill on border)
      - No text body (empty wps:bodyPr)

    When content overflows to page 2, this paragraph is the last element before
    sectPr and lands on page 2.  The page-relative rectangle covers page 2 with
    the same background color as page 1.  If content does NOT overflow (all on
    page 1), this paragraph also appears on page 1 with the same #F8F8F6 color
    as the blip image — visually indistinguishable from the existing background.

    Silently skips when: no qualifying behindDoc blip found, PIL not installed,
    dominant color is pure white, or any I/O/decoding error.
    """
    _WP_NS = _WP
    _DML_NS = _DML
    _RELS_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
    _R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    # Find first behindDoc blip anchor in the body (including inside table cells)
    rel_id: "str | None" = None
    for anchor in body.findall(f".//{{{_WP_NS}}}anchor"):
        if anchor.get("behindDoc") != "1":
            continue
        ext = anchor.find(f"{{{_WP_NS}}}extent")
        if ext is None:
            continue
        try:
            cx_src = int(ext.get("cx", "0"))
            cy_src = int(ext.get("cy", "0"))
        except ValueError:
            continue
        if cx_src < 7_000_000 or cy_src < 10_000_000:
            continue
        blip = anchor.find(f".//{{{_DML_NS}}}blip")
        if blip is None:
            continue
        rel_id = blip.get(f"{{{_R_NS}}}embed")
        break

    if not rel_id:
        return

    try:
        import zipfile as _zf
        from PIL import Image as _PILImage
        import io as _io
        from lxml import etree as _et

        # --- Extract dominant background color from the blip image ---
        with _zf.ZipFile(docx_path, "r") as zf:
            rels_xml = _et.fromstring(zf.read("word/_rels/document.xml.rels"))
            img_path: "str | None" = None
            for rel in rels_xml.findall(f".//{{{_RELS_NS}}}Relationship"):
                if rel.get("Id") == rel_id:
                    target = rel.get("Target", "")
                    img_path = (
                        f"word/{target}" if not target.startswith("/") else target[1:]
                    )
                    break
            if not img_path:
                return
            img_bytes = zf.read(img_path)

        img = _PILImage.open(_io.BytesIO(img_bytes)).convert("RGB")
        iw, ih = img.size
        # Five edge-pixel samples far from photo/icon content
        sample_pts = [
            (max(iw // 20, 1), ih // 2),
            (max(iw // 20, 1), ih * 3 // 4),
            (iw * 19 // 20, ih // 2),
            (iw * 19 // 20, ih * 3 // 4),
            (iw // 2, ih * 9 // 10),
        ]
        pixels = [img.getpixel(pt) for pt in sample_pts]
        r = sorted(p[0] for p in pixels)[len(pixels) // 2]
        g = sorted(p[1] for p in pixels)[len(pixels) // 2]
        b = sorted(p[2] for p in pixels)[len(pixels) // 2]
        hex_color = f"{r:02X}{g:02X}{b:02X}"
        if hex_color.upper() == "FFFFFF":
            return

        # --- Determine rectangle size from page dimensions ---
        _pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
        if _pgSz is not None:
            try:
                cx_emu = int(_pgSz.get(f"{{{_W}}}w", "11920")) * _EMU_PER_TWIP
                cy_emu = int(_pgSz.get(f"{{{_W}}}h", "16840")) * _EMU_PER_TWIP
            except ValueError:
                cx_emu, cy_emu = 7568800, 10693400
        else:
            cx_emu, cy_emu = 7568800, 10693400  # A4 portrait fallback

        # --- Build the solid-fill rectangle as a DrawingML anchor ---
        _A = _DML_NS
        _WPS_NS = _WPS

        bg_p = _et.Element(f"{{{_W}}}p")
        pPr = _et.SubElement(bg_p, f"{{{_W}}}pPr")
        spacing = _et.SubElement(pPr, f"{{{_W}}}spacing")
        spacing.set(f"{{{_W}}}after", "0")
        spacing.set(f"{{{_W}}}line", "20")
        spacing.set(f"{{{_W}}}lineRule", "exact")

        run = _et.SubElement(bg_p, f"{{{_W}}}r")
        drawing = _et.SubElement(run, f"{{{_W}}}drawing")

        anc = _et.SubElement(drawing, f"{{{_WP_NS}}}anchor")
        anc.set("distT", "0")
        anc.set("distB", "0")
        anc.set("distL", "0")
        anc.set("distR", "0")
        anc.set("simplePos", "0")
        anc.set("relativeHeight", "2251658")
        anc.set("behindDoc", "1")
        anc.set("locked", "0")
        anc.set("layoutInCell", "0")
        anc.set("allowOverlap", "1")

        sp = _et.SubElement(anc, f"{{{_WP_NS}}}simplePos")
        sp.set("x", "0")
        sp.set("y", "0")

        pH = _et.SubElement(anc, f"{{{_WP_NS}}}positionH")
        pH.set("relativeFrom", "page")
        _et.SubElement(pH, f"{{{_WP_NS}}}posOffset").text = "0"

        pV = _et.SubElement(anc, f"{{{_WP_NS}}}positionV")
        pV.set("relativeFrom", "page")
        _et.SubElement(pV, f"{{{_WP_NS}}}posOffset").text = "0"

        ext_el = _et.SubElement(anc, f"{{{_WP_NS}}}extent")
        ext_el.set("cx", str(cx_emu))
        ext_el.set("cy", str(cy_emu))

        eff = _et.SubElement(anc, f"{{{_WP_NS}}}effectExtent")
        eff.set("l", "0")
        eff.set("t", "0")
        eff.set("r", "0")
        eff.set("b", "0")

        _et.SubElement(anc, f"{{{_WP_NS}}}wrapNone")

        dpr = _et.SubElement(anc, f"{{{_WP_NS}}}docPr")
        dpr.set("id", "99999")
        dpr.set("name", "OverflowBgRect")

        _et.SubElement(anc, f"{{{_WP_NS}}}cNvGraphicFramePr")

        graphic = _et.SubElement(anc, f"{{{_A}}}graphic")
        graphicData = _et.SubElement(graphic, f"{{{_A}}}graphicData")
        graphicData.set(
            "uri", "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
        )

        wsp = _et.SubElement(graphicData, f"{{{_WPS_NS}}}wsp")

        cNvSpPr = _et.SubElement(wsp, f"{{{_WPS_NS}}}cNvSpPr")
        spLocks = _et.SubElement(cNvSpPr, f"{{{_A}}}spLocks")
        spLocks.set("noChangeArrowheads", "1")

        spPr = _et.SubElement(wsp, f"{{{_WPS_NS}}}spPr")

        xfrm = _et.SubElement(spPr, f"{{{_A}}}xfrm")
        off = _et.SubElement(xfrm, f"{{{_A}}}off")
        off.set("x", "0")
        off.set("y", "0")
        sz = _et.SubElement(xfrm, f"{{{_A}}}ext")
        sz.set("cx", str(cx_emu))
        sz.set("cy", str(cy_emu))

        prstGeom = _et.SubElement(spPr, f"{{{_A}}}prstGeom")
        prstGeom.set("prst", "rect")
        _et.SubElement(prstGeom, f"{{{_A}}}avLst")

        solidFill = _et.SubElement(spPr, f"{{{_A}}}solidFill")
        srgbClr = _et.SubElement(solidFill, f"{{{_A}}}srgbClr")
        srgbClr.set("val", hex_color)

        ln = _et.SubElement(spPr, f"{{{_A}}}ln")
        _et.SubElement(ln, f"{{{_A}}}noFill")

        _et.SubElement(wsp, f"{{{_WPS_NS}}}bodyPr")

        # Insert as the last body element before sectPr
        if sectPr is not None:
            sectPr.addprevious(bg_p)
        else:
            body.append(bg_p)

        _log.debug(
            "OVERFLOW_BG_RECT: inserted solid-fill rect #%s cx=%d cy=%d (rId=%s)",
            hex_color, cx_emu, cy_emu, rel_id,
        )

    except Exception as exc:
        _log.debug("OVERFLOW_BG_RECT_SKIP: %s", exc)


# ---------------------------------------------------------------------------
# Glossary cleanup
# ---------------------------------------------------------------------------

def _clear_docx_glossary(docx_path: str) -> None:
    """Remove SDT placeholder content and thumbnail from generated DOCX.

    Two problems are fixed here:

    1. Glossary placeholder paragraphs (word/glossary/document.xml):
       Word stores SDT placeholder text as building blocks in the glossary.
       LibreOffice may render these paragraphs as a supplementary section
       appended to the output PDF even after we flatten all SDTs.
       Fix: replace the glossary with an empty <w:docParts/> element.

    2. Document thumbnail (docProps/thumbnail.emf):
       The thumbnail captures the template's original appearance including
       SDT placeholder text (e.g. "Summarize your key responsibilities...").
       LibreOffice reads this EMF and overlays its vector-text layer onto the
       rendered PDF, causing the old placeholder text to appear alongside the
       updated LLM content.
       Fix: remove the thumbnail from the ZIP entirely.  LibreOffice renders
       the document correctly without it; Word regenerates the thumbnail on
       the next save.
    """
    import io
    import zipfile as _zf

    _GLOSSARY_PATH = "word/glossary/document.xml"
    _THUMBNAIL_PATH = "docProps/thumbnail.emf"
    _ROOT_RELS_PATH = "_rels/.rels"
    _THUMBNAIL_TYPE = "http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"
    _EMPTY_GLOSSARY = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:glossaryDocument xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"'
        ' xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        ' xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
        ' mc:Ignorable="w14"'
        ' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006">'
        '<w:docParts/>'
        '</w:glossaryDocument>'
    )

    try:
        with open(docx_path, "rb") as f:
            data = f.read()

        buf = io.BytesIO(data)
        out_buf = io.BytesIO()

        with _zf.ZipFile(buf, "r") as zin, _zf.ZipFile(out_buf, "w", _zf.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == _THUMBNAIL_PATH:
                    _log.debug("THUMBNAIL_REMOVED: %s stripped from %s", _THUMBNAIL_PATH, docx_path)
                    continue  # drop the thumbnail file
                if item.filename == _GLOSSARY_PATH:
                    zout.writestr(item, _EMPTY_GLOSSARY.encode("utf-8"))
                    _log.debug("GLOSSARY_CLEARED: %s emptied in %s", _GLOSSARY_PATH, docx_path)
                elif item.filename == _ROOT_RELS_PATH:
                    # Remove the thumbnail relationship so python-docx (and Word)
                    # do not try to load the now-absent thumbnail file.
                    try:
                        from lxml import etree as _et
                        rels_xml = zin.read(item.filename)
                        root = _et.fromstring(rels_xml)
                        removed = 0
                        for rel in list(root):
                            if rel.get("Type") == _THUMBNAIL_TYPE:
                                root.remove(rel)
                                removed += 1
                        if removed:
                            cleaned = _et.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                            zout.writestr(item, cleaned)
                            _log.debug("THUMBNAIL_REL_REMOVED: stripped %d rel(s) from %s", removed, _ROOT_RELS_PATH)
                        else:
                            zout.writestr(item, rels_xml)
                    except Exception:
                        zout.writestr(item, zin.read(item.filename))
                else:
                    zout.writestr(item, zin.read(item.filename))

        with open(docx_path, "wb") as f:
            f.write(out_buf.getvalue())

    except Exception as exc:
        _log.debug("GLOSSARY_CLEAR_FAILED: %s — %s", docx_path, exc)


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
    # PDF two-column documents: use a fresh minimal DOCX rather than copying
    # the style template.  Style templates carry DOCX compat settings (e.g.
    # w:sz in docDefaults, compatibilityMode) that cause LibreOffice to suppress
    # indented paragraphs in table cells.  A fresh Document() has clean defaults.
    if doc.source_kind == "pdf" and doc.layout.column_split_x is not None:
        d = Document()
        _patch_bullet_numbering(d)
        body = d.element.body
        sectPr = body.find(f"{{{_W}}}sectPr")
        _apply_pdf_page_geometry(sectPr, doc.layout)
        if doc.layout.section_row_table:
            _render_pdf_section_row_table(doc, body, sectPr, doc_part=d.part)
        else:
            _render_pdf_two_col(doc, body, sectPr, doc_part=d.part)
        d.save(output_path)
        return

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
            # Inherit page background color for overflow pages.  Extracts the
            # dominant edge-pixel color from any behindDoc blip background and
            # inserts a solid-fill rectangle (no image, no text, no foreground
            # content) at the body end.  That paragraph lands on page 2 when
            # content overflows, covering it with the same background fill.
            _maybe_insert_bg_rect(output_path, body, sectPr)
            d.save(output_path)
            # After saving, remove the glossary document from the ZIP so that
            # LibreOffice does not render the SDT placeholder paragraphs stored
            # there as a supplementary section.  The glossary's content controls
            # (w:placeholder / w:docPart entries) are no longer needed — we have
            # already flattened all SDTs in the main document XML.
            _clear_docx_glossary(output_path)
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
        # Strip the "overrideTableStyleFontSizeAndJustification" compat setting
        # from the style template.  When this is set to 1, LibreOffice suppresses
        # paragraphs inside table cells whose explicit font size differs from the
        # table style's default — making section headings and role entries invisible.
        _strip_override_table_style_font_compat(d)
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
