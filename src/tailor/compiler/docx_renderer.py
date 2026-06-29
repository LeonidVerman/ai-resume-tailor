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

_W   = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
_WP  = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_A   = "http://schemas.openxmlformats.org/drawingml/2006/main"
_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
_R   = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_log = logging.getLogger(__name__)

_PT_TO_EMU = 12700  # 1 point = 12700 English Metric Units


# ---------------------------------------------------------------------------
# Floating image helpers (PDF raster asset preservation)
# ---------------------------------------------------------------------------

def _make_floating_image_para(
    png_bytes: bytes,
    x_pt: float,
    y_pt: float,
    w_pt: float,
    h_pt: float,
    doc_part,
    img_id: int,
    behind_doc: bool = False,
    relative_height: int = 2,
) -> "Any | None":
    """Return a ``w:p`` containing a floating (page-anchored) image, or None on error.

    The image is positioned at (x_pt, y_pt) from the page top-left corner,
    matching the original PDF coordinates.  When *behind_doc* is True the image
    sits behind body text (suitable for decorative backgrounds); otherwise it
    floats above text with no wrapping (suitable for profile photos).
    """
    if doc_part is None or not png_bytes:
        return None

    import io
    from lxml import etree

    try:
        rId, _ = doc_part.get_or_add_image(io.BytesIO(png_bytes))
    except Exception:
        return None

    x_emu = int(x_pt * _PT_TO_EMU)
    y_emu = int(y_pt * _PT_TO_EMU)
    cx_emu = int(w_pt * _PT_TO_EMU)
    cy_emu = int(h_pt * _PT_TO_EMU)

    # Declare DrawingML namespaces with canonical prefixes ('a:', 'pic:') on the
    # paragraph element so lxml uses those prefixes for all descendant elements.
    # Without this lxml auto-generates 'ns0:', 'ns2:' etc. which confuse LibreOffice.
    p = etree.Element(f"{{{_W}}}p", nsmap={"a": _A, "pic": _PIC})
    r = etree.SubElement(p, f"{{{_W}}}r")
    drawing = etree.SubElement(r, f"{{{_W}}}drawing")

    anchor = etree.SubElement(
        drawing, f"{{{_WP}}}anchor",
        distT="0", distB="0", distL="0", distR="0",
        simplePos="0", relativeHeight=str(relative_height),
        behindDoc="1" if behind_doc else "0",
        locked="0", layoutInCell="1", allowOverlap="0",
    )

    etree.SubElement(anchor, f"{{{_WP}}}simplePos", x="0", y="0")

    posH = etree.SubElement(anchor, f"{{{_WP}}}positionH", relativeFrom="page")
    etree.SubElement(posH, f"{{{_WP}}}posOffset").text = str(x_emu)

    posV = etree.SubElement(anchor, f"{{{_WP}}}positionV", relativeFrom="page")
    etree.SubElement(posV, f"{{{_WP}}}posOffset").text = str(y_emu)

    etree.SubElement(anchor, f"{{{_WP}}}extent", cx=str(cx_emu), cy=str(cy_emu))
    etree.SubElement(anchor, f"{{{_WP}}}effectExtent", l="0", t="0", r="0", b="0")
    etree.SubElement(anchor, f"{{{_WP}}}wrapNone")

    docPr = etree.SubElement(anchor, f"{{{_WP}}}docPr")
    docPr.set("id", str(img_id))
    docPr.set("name", f"Picture{img_id}")

    # cNvGraphicFramePr must contain graphicFrameLocks so renderers treat this
    # as a proper picture frame (required by LibreOffice; Word tolerates omission).
    cNvGFPr = etree.SubElement(anchor, f"{{{_WP}}}cNvGraphicFramePr")
    etree.SubElement(cNvGFPr, f"{{{_A}}}graphicFrameLocks", noChangeAspect="1")

    graphic = etree.SubElement(anchor, f"{{{_A}}}graphic")
    graphicData = etree.SubElement(
        graphic, f"{{{_A}}}graphicData",
        uri="http://schemas.openxmlformats.org/drawingml/2006/picture",
    )

    pic = etree.SubElement(graphicData, f"{{{_PIC}}}pic")

    nvPicPr = etree.SubElement(pic, f"{{{_PIC}}}nvPicPr")
    etree.SubElement(nvPicPr, f"{{{_PIC}}}cNvPr", id=str(img_id), name=f"Picture{img_id}")
    etree.SubElement(nvPicPr, f"{{{_PIC}}}cNvPicPr")

    blipFill = etree.SubElement(pic, f"{{{_PIC}}}blipFill")
    blip = etree.SubElement(blipFill, f"{{{_A}}}blip")
    blip.set(f"{{{_R}}}embed", rId)
    stretch = etree.SubElement(blipFill, f"{{{_A}}}stretch")
    etree.SubElement(stretch, f"{{{_A}}}fillRect")

    spPr = etree.SubElement(pic, f"{{{_PIC}}}spPr")
    xfrm = etree.SubElement(spPr, f"{{{_A}}}xfrm")
    etree.SubElement(xfrm, f"{{{_A}}}off", x="0", y="0")
    etree.SubElement(xfrm, f"{{{_A}}}ext", cx=str(cx_emu), cy=str(cy_emu))
    prstGeom = etree.SubElement(spPr, f"{{{_A}}}prstGeom", prst="rect")
    etree.SubElement(prstGeom, f"{{{_A}}}avLst")

    return p


def _apply_docx_page_background(d, hex_color: str) -> None:
    """Set the DOCX page background color via <w:background>.

    Adds <w:background w:color="RRGGBB"/> before <w:body> and enables
    displayBackgroundShape in settings.xml.  LibreOffice renders this as a
    solid page fill — more reliable than a behind-text floating image for
    single-column templates whose entire page is a dark color.
    """
    from lxml import etree as _etree
    hex_upper = hex_color.upper()
    doc_elem = d.element
    existing = doc_elem.find(f"{{{_W}}}background")
    if existing is not None:
        doc_elem.remove(existing)
    bg = _etree.Element(f"{{{_W}}}background")
    bg.set(f"{{{_W}}}color", hex_upper)
    body_elem = doc_elem.find(f"{{{_W}}}body")
    if body_elem is not None:
        body_idx = list(doc_elem).index(body_elem)
        doc_elem.insert(body_idx, bg)
    else:
        doc_elem.insert(0, bg)
    try:
        settings_elem = d.settings.element
        disp_tag = f"{{{_W}}}displayBackgroundShape"
        if settings_elem.find(disp_tag) is None:
            _etree.SubElement(settings_elem, disp_tag)
    except Exception:
        pass


def _insert_page_images(doc: "ResumeDocument", body, sectPr, doc_part) -> None:
    """Append floating image paragraphs for every extracted ``PageImageBlock``.

    Called once after each render path writes its content paragraphs.
    Images are appended to the body (or inserted before sectPr when present)
    so they appear as page-anchored floating objects independent of text flow.

    Profile photos float above text (behindDoc=0); decorative elements sit
    behind text (behindDoc=1).
    """
    if not getattr(doc, "page_images", None) or doc_part is None:
        return

    def _add(elem) -> None:
        # Insert at position 0 so the anchor paragraph is always on page 1.
        # LibreOffice places page-anchored images on the page of their anchor
        # paragraph — appending at the end puts images on the last page when
        # the document spans multiple pages.
        body.insert(0, elem)

    # Fix C: Deterministic z-ordering by category so background layers remain stable
    # regardless of document insertion order.  Lower relativeHeight = further back.
    _CATEGORY_Z: dict[str, int] = {
        "full_page_bg": 2,       # page-level decorative background — furthest back
        "header_band": 3,        # header/footer color bands
        "footer_band": 3,
        "sidebar_bg": 4,         # column sidebar background
        "body_decor": 5,         # mid-page decorative elements
        "header_footer_decor": 5,
        "profile_photo": 10,     # foreground — on top of everything
    }

    # h_rule/v_rule suppression: header-divider rules (rules positioned before the
    # first body section heading) overlap with dynamically-reflowed header content in
    # the DOCX.  The original PDF has tight gaps (e.g. 6 pt below the name) that
    # disappear when the template reflows as single-column with standard line spacing.
    # Only h/v rules whose y is >= the first section heading's y are safe to render.
    _first_sec_y: "float | None" = None
    if getattr(doc, "sections", None):
        _sec_ys = [
            sec.heading.paragraph_profile.y_top_pt
            for sec in doc.sections
            if sec.heading.paragraph_profile
            and sec.heading.paragraph_profile.y_top_pt > 0
        ]
        if _sec_ys:
            _first_sec_y = min(_sec_ys)

    for i, img in enumerate(doc.page_images):
        if img.category in ("h_rule", "v_rule") and _first_sec_y is not None:
            if img.y_pt < _first_sec_y:
                _log.debug(
                    "HRULE_HEADER_ZONE_SKIP: category=%s y=%.0f first_sec_y=%.0f",
                    img.category, img.y_pt, _first_sec_y,
                )
                continue
        # Anchored h_rules are rendered as w:pBdr/w:top on the section heading
        # paragraph.  Skip them here so they don't also appear as floating images.
        if img.category == "h_rule" and img.anchor_next_section_id is not None:
            continue
        behind = img.category != "profile_photo"
        z = _CATEGORY_Z.get(img.category, 5 if behind else 10)
        para = _make_floating_image_para(
            img.image_bytes,
            img.x_pt, img.y_pt,
            img.width_pt, img.height_pt,
            doc_part,
            img_id=1000 + i,
            behind_doc=behind,
            relative_height=z,
        )
        if para is not None:
            _add(para)


# ---------------------------------------------------------------------------
# Section-anchored h_rule border helpers
# ---------------------------------------------------------------------------

def _sample_png_color_hex(image_bytes: bytes) -> "str | None":
    """Return 'RRGGBB' hex for the center pixel of *image_bytes* (PNG), or None."""
    try:
        import fitz as _fitz
        import io as _io
        pix = _fitz.Pixmap(_io.BytesIO(image_bytes))
        sample = pix.pixel(pix.width // 2, pix.height // 2)
        return f"{sample[0]:02x}{sample[1]:02x}{sample[2]:02x}"
    except Exception:
        return None


def _build_h_rule_border_map(doc: "ResumeDocument") -> "dict[str, Any]":
    """Return {heading_para_id: PageImageBlock} for sections with anchored h_rules.

    Used by render paths to apply w:pBdr/w:top borders to section headings that
    have an associated h_rule extracted from the source PDF.
    """
    sec_id_to_para_id = {
        sec.section_id: sec.heading.para_id
        for sec in doc.sections
        if sec.section_id and sec.heading.para_id
    }
    result: dict = {}
    for img in getattr(doc, "page_images", None) or []:
        if img.category != "h_rule":
            continue
        anc = img.anchor_next_section_id
        if anc is None:
            continue
        para_id = sec_id_to_para_id.get(anc)
        if para_id:
            result[para_id] = img
    return result


def _apply_h_rule_top_border(p_elem, rule_img: "Any") -> None:
    """Add w:pBdr/w:top to *p_elem* (a w:p lxml element) for *rule_img*.

    Uses the rule's color (sampled from its PNG center pixel) and derives
    border thickness from the rule height.  Updates w:spacing/w:before so
    the visual gap above the heading matches the original PDF gap_to_anchor_pt.
    """
    from lxml import etree

    hex_color = _sample_png_color_hex(rule_img.image_bytes)
    if hex_color is None:
        hex_color = "808080"

    gap_pt = rule_img.gap_to_anchor_pt or 20.0
    # Derive border thickness from rule height (8ths of a point, clamped 4–24).
    thickness_eighths = max(4, min(24, int(rule_img.height_pt * 8)))
    # Space between the border line and the paragraph text (in points, OOXML units).
    space_pt = 4
    # Total spacing before = gap_to_anchor_pt so the border lands at the right
    # vertical position relative to the previous section's content.
    before_twips = int(gap_pt * 20)

    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        return

    pBdr = etree.Element(f"{{{_W}}}pBdr")
    top = etree.SubElement(pBdr, f"{{{_W}}}top")
    top.set(f"{{{_W}}}val", "single")
    top.set(f"{{{_W}}}sz", str(thickness_eighths))
    top.set(f"{{{_W}}}space", str(space_pt))
    top.set(f"{{{_W}}}color", hex_color)

    # Insert pBdr before w:spacing to satisfy OOXML schema ordering.
    spc_elem = pPr.find(f"{{{_W}}}spacing")
    if spc_elem is not None:
        spc_elem.addprevious(pBdr)
        spc_elem.set(f"{{{_W}}}before", str(before_twips))
    else:
        pPr.append(pBdr)
        spc_new = etree.SubElement(pPr, f"{{{_W}}}spacing")
        spc_new.set(f"{{{_W}}}before", str(before_twips))
        spc_new.set(f"{{{_W}}}after", "0")

    _log.debug(
        "HRULE_BORDER_APPLIED: para=%s color=#%s thickness=%d before=%d",
        p_elem.get(f"{{{_W}}}rsidR", "?"), hex_color, thickness_eighths, before_twips,
    )


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


def _strip_text_wrapping_breaks(p_elem, new_text: str = "") -> None:
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

    Guard: when *new_text* contains '\\n', the line breaks are intentional
    (e.g. multi-subsection skills paragraphs like sample 34 CORE COMPETENCIES).
    In that case the textWrapping br elements are preserved so _set_para_text
    can distribute text segments to the correct run groups.
    """
    if "\n" in new_text:
        return  # preserve intentional line breaks
    _found_br = False
    for r_elem in list(p_elem.findall(f"{{{_W}}}r")):
        if _found_br:
            p_elem.remove(r_elem)
            continue
        for br in list(r_elem.findall(f"{{{_W}}}br")):
            if br.get(f"{{{_W}}}type") == "textWrapping":
                r_elem.remove(br)
                _found_br = True
                break


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
    # Preserve original text for multi-line distribution check (before stripping \n).
    _text_has_newlines = "\n" in text
    # w:br elements provide the line break; avoid doubling by stripping \n from text.
    text = text.replace("\n", "")
    # Early-exit for multi-SDT paragraphs: when a paragraph has two or more
    # direct w:sdt children, those SDTs form a structured multi-column layout
    # (e.g. sample 7 "email TAB phone TAB LinkedIn" contact row).  Distributing
    # new text across the SDT runs collapses all columns into a single run and
    # destroys the tab-stop-based horizontal spread.  Skip text update entirely;
    # _clear_sdt_placeholder will flatten each SDT to its original runs,
    # preserving the original text and tab structure.
    _sdt_count = sum(1 for child in p_elem if child.tag == f"{{{_W}}}sdt")
    if _sdt_count >= 2:
        return  # multi-column SDT row — preserve structure unchanged
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
        elif r.find(f".//{{{_WP}}}anchor") is not None:
            # Modern WPS text box in mc:AlternateContent/mc:Choice/w:drawing/wp:anchor.
            # Both the Choice (modern drawing) and Fallback (VML) branches carry the
            # same text; findall returns both copies, matching what the parser
            # concatenated into the paragraph IR text.
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

    # Pre-compute pure-tab separator run indices BEFORE clearing so we can save
    # the original text of post-tab content runs.  Pure-tab runs have <w:tab/>
    # but NO <w:t> children; content runs have <w:t> but typically no <w:tab/>.
    _pure_tab_idx_pre: set[int] = {
        i for i, r in enumerate(all_runs)
        if not r.findall(f"{{{_W}}}t") and r.findall(f"{{{_W}}}tab")
    }
    # When pure-tab separators exist and new text has no tab, we need to preserve
    # post-tab content runs unchanged (they contain right-column data such as
    # "Really Great University (2012-2014)").  Save their original text now.
    #
    # Guard: only save when the new text is a label-only update (new text is
    # significantly shorter than the original total).  When the new text is as
    # long as the original, it already incorporates both sides and restoring
    # post-tab runs would duplicate content (e.g. letter-spaced skills headings).
    _post_tab_preserve: dict[int, str] = {}
    if _pure_tab_idx_pre and "\t" not in text and total_orig > 0:
        _first_pre_tab = min(_pure_tab_idx_pre)
        # Only restore if new text is less than half the total original length.
        # Label-only updates (e.g. "EDUCATION" replacing "EDUCATION\tUniversity…")
        # have len(text) << total_orig.  Full replacements have len(text) ≈ total_orig.
        if len(text) < total_orig * 0.55:
            for i, r in enumerate(all_runs):
                if i > _first_pre_tab and i not in ws_text and i not in vml_indices:
                    _t_elems = r.findall(f"{{{_W}}}t")
                    if _t_elems:
                        _post_tab_preserve[i] = "".join(t.text or "" for t in _t_elems)

    # PDF-export micro-kerning strip and w:w propagation.
    #
    # PDF-to-DOCX converters produce two artefacts that break proportional text
    # redistribution:
    #
    # 1. Inter-word spacer runs: 1–3 char whitespace-only runs with w:spacing
    #    tuned for a space glyph.  After redistribution word characters land
    #    there and the space-optimised spacing mis-aligns them ("Bakend").
    #    Fix: strip w:spacing from these runs.
    #
    # 2. Character width scaling mismatch: spacer runs lack w:w (character width
    #    scaling, e.g. w:val="90" = condensed 90%) that content runs carry.
    #    After redistribution the word char in the spacer slot renders at 100%
    #    width while surrounding chars are condensed → visible width mismatch.
    #    Fix: copy w:w from the immediately preceding content run.
    #
    # Values ≥ 80 twips are intentional letter-spacing (decorative headings)
    # and must be preserved.
    _MICRO_KERN_LIMIT = 80  # twips; larger = intentional letter-spacing
    # w:w (character width scaling) cleanup thresholds.
    # PDF-to-DOCX converters produce two classes of artifact w:w values:
    #   > 150%: FontAwesome icon/bullet placeholder runs (e.g. w=270) that end
    #           up carrying the first 1-2 chars of redistributed content, making
    #           those chars render 2.7x wide ("De signed", "Mento red").
    #   < 95%:  Single mega-runs covering the whole original line (e.g. w=90)
    #           where only the first proportional slice inherits the value; the
    #           remaining slices get w=None (100%), creating a mismatch at the
    #           first run boundary ("Prot ocols").
    # Values in [95, 150] are treated as intentional typography and preserved.
    _W_SCALE_MIN = 95
    _W_SCALE_MAX = 150
    _prev_content_rpr = None  # rPr of last non-spacer run, for w:w propagation
    for _ki, _kr in enumerate(all_runs):
        if _ki in ws_text or _ki in vml_indices:
            continue
        _kt_elems = _kr.findall(f"{{{_W}}}t")
        _kr_text = "".join(e.text or "" for e in _kt_elems)
        if not _kr_text:
            continue
        _krpr = _kr.find(f"{{{_W}}}rPr")
        _is_spacer = _kr_text.isspace() and 1 <= len(_kr_text) <= 3

        if not _is_spacer:
            # Content run: record its rPr for neighbouring spacer propagation.
            _prev_content_rpr = _krpr
            # Strip micro-tracking from all content runs.  The _MICRO_KERN_LIMIT
            # guard already protects intentional letter-spacing (≥80 twips).
            if _krpr is not None:
                _ksp = _krpr.find(f"{{{_W}}}spacing")
                if _ksp is not None:
                    try:
                        if abs(int(_ksp.get(f"{{{_W}}}val") or 0)) < _MICRO_KERN_LIMIT:
                            _krpr.remove(_ksp)
                    except (ValueError, TypeError):
                        pass
                # Strip out-of-range character width scaling from content runs.
                _kww = _krpr.find(f"{{{_W}}}w")
                if _kww is not None:
                    try:
                        _wval = int(_kww.get(f"{{{_W}}}val") or 100)
                        if _wval < _W_SCALE_MIN or _wval > _W_SCALE_MAX:
                            _krpr.remove(_kww)
                    except (ValueError, TypeError):
                        pass
            continue

        # Spacer run: strip w:spacing.
        if _krpr is not None:
            _ksp = _krpr.find(f"{{{_W}}}spacing")
            if _ksp is not None:
                try:
                    if abs(int(_ksp.get(f"{{{_W}}}val") or 0)) < _MICRO_KERN_LIMIT:
                        _krpr.remove(_ksp)
                except (ValueError, TypeError):
                    pass
            # Strip out-of-range w:w from spacer runs too.  Template spacer runs
            # that immediately follow icon/placeholder runs may carry the same
            # artifact w=270; stripping prevents it from surviving into output.
            _kww_s = _krpr.find(f"{{{_W}}}w")
            if _kww_s is not None:
                try:
                    _wval_s = int(_kww_s.get(f"{{{_W}}}val") or 100)
                    if _wval_s < _W_SCALE_MIN or _wval_s > _W_SCALE_MAX:
                        _krpr.remove(_kww_s)
                except (ValueError, TypeError):
                    pass

        # Propagate w:w (character width scaling) from the preceding content run
        # so the redistributed word character renders at the same condensed width.
        # Only in-range values are propagated; artifact values were stripped above.
        if _prev_content_rpr is not None and _krpr is not None:
            _src_ww = _prev_content_rpr.find(f"{{{_W}}}w")
            if _src_ww is not None and _krpr.find(f"{{{_W}}}w") is None:
                _krpr.append(deepcopy(_src_ww))

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

    # Tab-split label fix: when the paragraph has pure-tab separator runs (column
    # dividers) and the new text contains no tab characters, place ALL text in the
    # first content run before the tab.  Post-tab content runs (which hold right-
    # column data like "Really Great University (2012-2014)") are RESTORED to their
    # original text so they are not lost.  This prevents label-column headings like
    # "EDUCATION\t<right-col-text>" from distributing "EDUCATION" proportionally as
    # "ED" (pre-tab) + "UCATION" (post-tab), which splits the word across columns.
    if _pure_tab_idx and "\t" not in text:
        _first_tab_run = min(_pure_tab_idx)
        _pre_tab_ci = [ci for ci in content_indices if ci < _first_tab_run]
        if _pre_tab_ci:
            _set_run_text(all_runs[_pre_tab_ci[0]], text)
            for ci in content_indices:
                if ci == _pre_tab_ci[0]:
                    continue
                elif ci in _post_tab_preserve:
                    # Restore original right-column content (e.g. institution name)
                    _set_run_text(all_runs[ci], _post_tab_preserve[ci])
                else:
                    _set_run_text(all_runs[ci], "")
            return

    # Multi-line paragraph distribution: when the original text had \n (line breaks
    # correspond to <w:br type="textWrapping"/> elements) and the paragraph still
    # has those br elements (i.e. _strip_text_wrapping_breaks was skipped), distribute
    # each line segment to its corresponding run group instead of proportionally
    # across all runs.  This preserves paragraph-internal line breaks for templates
    # like multi-category skills sections (e.g. sample 34 CORE COMPETENCIES).
    if _text_has_newlines and not _pure_tab_idx and not vml_indices:
        _tw_br_run_indices: set[int] = {
            i for i, r in enumerate(all_runs)
            if r.findall(f"{{{_W}}}br")
            and not any((t.text or "") for t in r.findall(f"{{{_W}}}t"))
        }
        if _tw_br_run_indices:
            # Group content run indices by br-separator runs.
            # Each group receives one \n-split text segment.
            _segs = (text or "").split("\n") if "\n" in (text or "") else [text]
            # Rebuild from original text (before \n was stripped) using known segments
            # Actually text already has \n stripped → use original segments from
            # _text_has_newlines. We must re-derive segments from the pre-strip text.
            # Re-examine: text was already stripped; we need the segment list.
            # Use the run-group structure: groups separated by br-only runs.
            _run_groups: list[list[int]] = [[]]
            for _ri, _r in enumerate(all_runs):
                if _ri in ws_text:
                    continue
                _is_sep = (
                    _r.findall(f"{{{_W}}}br")
                    and not any((t.text or "") for t in _r.findall(f"{{{_W}}}t"))
                )
                if _is_sep:
                    _run_groups.append([])
                else:
                    _run_groups[-1].append(_ri)
            # Remove empty trailing group
            while _run_groups and not _run_groups[-1]:
                _run_groups.pop()
            # The stripped text is a single string; we can't reconstruct segments.
            # Fall through to proportional distribution if segments mismatch.
            if len(_run_groups) > 1:
                # Distribute text across groups by their original length proportion.
                _group_orig_lens = [
                    sum(orig_lens[ci] for ci in grp if ci < len(orig_lens))
                    for grp in _run_groups
                ]
                _total_grp_len = sum(_group_orig_lens) or 1
                _assigned = 0
                for _gi, (_grp, _glen) in enumerate(zip(_run_groups, _group_orig_lens)):
                    if not _grp:
                        continue
                    if _gi == len(_run_groups) - 1:
                        _seg = text[_assigned:]
                    else:
                        _seg_end = round(len(text) * sum(_group_orig_lens[:_gi+1]) / _total_grp_len)
                        _seg = text[_assigned:_seg_end]
                        _assigned = _seg_end
                    # Write segment to first run of group; clear the rest.
                    _set_run_text(all_runs[_grp[0]], _seg)
                    for _ci in _grp[1:]:
                        _set_run_text(all_runs[_ci], "")
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

        # Apply cell-level background shading when the section heading carries a
        # background color (e.g. sample 14's peach section bands).  Cell-level
        # shading spans the full cell width whereas paragraph-level shading only
        # covers the text area, giving partial/striped blocks.
        _hd_bg = (section.heading.paragraph_profile.background_color
                  if section.heading.paragraph_profile else None)
        if _hd_bg:
            _hd_shd = etree.SubElement(left_tcPr, f"{{{_W}}}shd")
            _hd_shd.set(f"{{{_W}}}val", "clear")
            _hd_shd.set(f"{{{_W}}}color", "auto")
            _hd_shd.set(f"{{{_W}}}fill", _hd_bg)

        heading_elem = build_para_element(section.heading, doc_part=doc_part, skip_bg_shd=bool(_hd_bg))
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

        # Apply cell-level background shading to the right cell as well when the
        # heading carries a background colour (so the row looks like a full band).
        if _hd_bg:
            _rt_shd = etree.SubElement(right_tcPr, f"{{{_W}}}shd")
            _rt_shd.set(f"{{{_W}}}val", "clear")
            _rt_shd.set(f"{{{_W}}}color", "auto")
            _rt_shd.set(f"{{{_W}}}fill", _hd_bg)

        def _append(pm, combined_text=None):
            """Build and append a para element with normalised indent."""
            src = pm.with_text(combined_text) if combined_text else pm
            # Skip paragraph-level bg shading when the cell carries cell shading.
            p_elem = build_para_element(src, doc_part=doc_part, skip_bg_shd=bool(_hd_bg))
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


def _make_full_width_dark_band(
    bg_hex: str, page_w_twips: int, left_margin_twips: int,
    paras: list, doc_part=None, *, center_text: bool = False,
) -> "Any":
    """Return a full-page-width w:tbl element with *bg_hex* cell shading.

    Used to render full-width dark header/footer bands (e.g. sample 25).
    The table is pushed to the physical page left edge via tblInd=-left_margin.
    Each ParaModel in *paras* is appended to the single cell with skip_bg_shd=False.
    When *center_text* is True, each paragraph's indent is offset by left_margin_twips
    so the content aligns correctly within the full-width cell.
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    tbl = etree.Element(f"{{{_W}}}tbl")
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW.set(f"{{{_W}}}w", str(page_w_twips))
    tblW.set(f"{{{_W}}}type", "dxa")
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
    tr = etree.SubElement(tbl, f"{{{_W}}}tr")
    tc = etree.SubElement(tr, f"{{{_W}}}tc")
    tcPr = etree.SubElement(tc, f"{{{_W}}}tcPr")
    tcW = etree.SubElement(tcPr, f"{{{_W}}}tcW")
    tcW.set(f"{{{_W}}}w", str(page_w_twips))
    tcW.set(f"{{{_W}}}type", "dxa")
    shd = etree.SubElement(tcPr, f"{{{_W}}}shd")
    shd.set(f"{{{_W}}}val", "clear")
    shd.set(f"{{{_W}}}color", "auto")
    shd.set(f"{{{_W}}}fill", bg_hex)
    for pm in paras:
        p_elem = build_para_element(pm, doc_part=doc_part, skip_bg_shd=False)
        if center_text:
            pPr = p_elem.find(f"{{{_W}}}pPr")
            if pPr is not None:
                ind = pPr.find(f"{{{_W}}}ind")
                if ind is not None:
                    cur = int(ind.get(f"{{{_W}}}left", "0"))
                    ind.set(f"{{{_W}}}left", str(cur + left_margin_twips))
                else:
                    new_ind = etree.SubElement(pPr, f"{{{_W}}}ind")
                    new_ind.set(f"{{{_W}}}left", str(left_margin_twips))
        tc.append(p_elem)
    if not paras:
        etree.SubElement(tc, f"{{{_W}}}p")
    return tbl


def _detect_body_two_col(body_paras) -> "list[tuple] | None":
    """Return (left_pm, right_pm) row pairs if body_paras form a 2-column grid, else None.

    Detection criteria:
    - Exactly 2 distinct indent_left_pt values across all body_paras.
    - Every y_top_pt bucket has at most one para from each indent group.
    This catches contact-info sub-tables (address on left, phone/email on right)
    that appear side-by-side in the source PDF but land in the same section body.
    """
    if len(body_paras) < 2:
        return None
    indents = []
    for pm in body_paras:
        pp = pm.paragraph_profile
        if pp is None or pp.indent_left_pt is None:
            return None
        indents.append(pp.indent_left_pt)
    unique_indents = sorted({round(x) for x in indents})
    if len(unique_indents) != 2:
        return None
    left_ind, right_ind = unique_indents
    by_y: "dict" = {}
    for pm in body_paras:
        pp = pm.paragraph_profile
        if pp.y_top_pt is None:
            return None
        y = round(pp.y_top_pt, 1)
        ind = round(pp.indent_left_pt)
        slot = by_y.setdefault(y, {})
        if ind in slot:
            return None  # duplicate: not a clean grid
        slot[ind] = pm
    rows = []
    for y in sorted(by_y):
        row = by_y[y]
        rows.append((row.get(left_ind), row.get(right_ind)))
    return rows if rows else None


def _detect_same_level_section_groups(sections) -> "list[list[int]]":
    """Return groups (lists of section indices) where headings share the same y_top (±5 pt).

    Sections at the same vertical position in the source PDF were laid out as
    side-by-side columns (e.g. Education | Skills | Interests across the bottom
    row of a template).  Grouping them lets the renderer create an N-column
    table so they appear horizontally aligned in the output.
    """
    from collections import defaultdict
    if not sections:
        return []
    y_groups: "dict[int, list[int]]" = defaultdict(list)
    for i, sec in enumerate(sections):
        pp = sec.heading.paragraph_profile
        # Only group original template sections (non-empty para_id).
        # LLM-added extra sections inherit y_top from their archetype, which
        # would cause false same-level grouping with unrelated template sections.
        if pp and pp.y_top_pt > 0 and sec.heading.para_id:
            bucket = round(pp.y_top_pt / 5) * 5  # snap to 5 pt grid
            y_groups[bucket].append(i)
    return [sorted(idxs) for idxs in y_groups.values() if len(idxs) >= 2]


def _render_pdf_single_col_with_groups(
    doc: "ResumeDocument",
    body,
    sectPr,
    doc_part,
    groups: "list[list[int]]",
    text_area_w_twips: int,
) -> None:
    """Render a single-column PDF that has same-level (multi-column) section groups.

    Sections NOT in any group are rendered as regular paragraphs.
    Sections in the same group are rendered as a single-row N-column table,
    matching the original side-by-side layout from the source PDF.
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    grouped_idxs: "set[int]" = {idx for grp in groups for idx in grp}
    rendered_group_keys: "set[tuple[int, ...]]" = set()
    _h_rule_borders = _build_h_rule_border_map(doc)

    def _add(elem) -> None:
        if sectPr is not None:
            sectPr.addprevious(elem)
        else:
            body.append(elem)

    def _section_min_indent_twips(sec) -> int:
        """Return the minimum indent_left_pt (in twips) across all paras in a section."""
        vals = []
        for pm in [sec.heading] + list(sec.body_paras):
            pp = pm.paragraph_profile
            if pp and pp.indent_left_pt > 0:
                vals.append(int(pp.indent_left_pt * 20))
        for role in sec.roles:
            for pm in [role.header] + list(role.meta_lines) + list(role.bullets):
                pp = pm.paragraph_profile
                if pp and pp.indent_left_pt > 0:
                    vals.append(int(pp.indent_left_pt * 20))
        return min(vals) if vals else 0

    def _shift_indent(p_elem, delta_twips: int) -> None:
        """Subtract delta_twips from w:ind w:left (floor at 0) inside a w:p element."""
        if delta_twips <= 0:
            return
        pPr = p_elem.find(f"{{{_W}}}pPr")
        if pPr is None:
            return
        ind = pPr.find(f"{{{_W}}}ind")
        if ind is not None:
            cur = int(ind.get(f"{{{_W}}}left", "0"))
            ind.set(f"{{{_W}}}left", str(max(0, cur - delta_twips)))

    def _role_date_first(role) -> bool:
        """True when the first meta_line is physically above the role header (date-first layout)."""
        if not role.meta_lines:
            return False
        m0_pp = role.meta_lines[0].paragraph_profile
        h_pp = role.header.paragraph_profile
        if m0_pp is None or h_pp is None:
            return False
        m0_y = m0_pp.y_top_pt
        h_y = h_pp.y_top_pt
        if m0_y is None or h_y is None:
            return False
        return m0_y < h_y

    def _role_header_pm(role):
        """Return a ParaModel for the role header, combining header_extra when present."""
        if role.header_extra and "|" not in role.header.text:
            combined = role.header.text.strip() + " | " + " | ".join(
                he.text.strip() for he in role.header_extra if he.text.strip()
            )
            return role.header.with_text(combined)
        return role.header

    def _role_two_col_date(role) -> bool:
        """True when date meta is in the left column and role title is in the right column.

        Detects the 'parallel columns' experience layout where the date appears at
        x≈0 and the role title is indented far to the right (e.g. sample 6).
        Both date and title share the same y_top (they are side-by-side).
        """
        if not role.meta_lines:
            return False
        m0_pp = role.meta_lines[0].paragraph_profile
        h_pp = role.header.paragraph_profile
        if m0_pp is None or h_pp is None:
            return False
        date_x = m0_pp.indent_left_pt
        role_x = h_pp.indent_left_pt
        if date_x is None or role_x is None or role_x - date_x < 100:
            return False
        date_y = m0_pp.y_top_pt
        role_y = h_pp.y_top_pt
        if date_y is None or role_y is None:
            return False
        return abs(date_y - role_y) < 5

    def _build_role_two_col_table(role, hdr_pm, min_indent: int = 0):
        """Build a 2-column role sub-table: left=date, right=title+bullets.

        Used when the date and role title are side-by-side in the source PDF
        (two-column experience layout).  min_indent is subtracted from all indents
        when the role is inside an outer table cell.
        """
        h_pp = role.header.paragraph_profile
        left_col_w = max(0, int(h_pp.indent_left_pt * 20) - min_indent) if h_pp and h_pp.indent_left_pt else 0
        right_col_w = max(100, text_area_w_twips - min_indent - left_col_w)

        tbl = etree.Element(f"{{{_W}}}tbl")
        tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
        tblW_el = etree.SubElement(tblPr, f"{{{_W}}}tblW")
        tblW_el.set(f"{{{_W}}}w", str(left_col_w + right_col_w))
        tblW_el.set(f"{{{_W}}}type", "dxa")
        tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
        tblLayout.set(f"{{{_W}}}type", "fixed")
        tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
        for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
            brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
            brd.set(f"{{{_W}}}val", "none")
        tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
        for side in ("top", "left", "bottom", "right"):
            m_el = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
            m_el.set(f"{{{_W}}}w", "0")
            m_el.set(f"{{{_W}}}type", "dxa")

        tr = etree.SubElement(tbl, f"{{{_W}}}tr")

        # Left cell: date
        tc_l = etree.SubElement(tr, f"{{{_W}}}tc")
        tcPr_l = etree.SubElement(tc_l, f"{{{_W}}}tcPr")
        tcW_l = etree.SubElement(tcPr_l, f"{{{_W}}}tcW")
        tcW_l.set(f"{{{_W}}}w", str(left_col_w))
        tcW_l.set(f"{{{_W}}}type", "dxa")
        etree.SubElement(tcPr_l, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
        date_elem = build_para_element(role.meta_lines[0], doc_part)
        _shift_indent(date_elem, min_indent)
        tc_l.append(date_elem)

        # Right cell: role header + remaining meta + bullets
        tc_r = etree.SubElement(tr, f"{{{_W}}}tc")
        tcPr_r = etree.SubElement(tc_r, f"{{{_W}}}tcPr")
        tcW_r = etree.SubElement(tcPr_r, f"{{{_W}}}tcW")
        tcW_r.set(f"{{{_W}}}w", str(right_col_w))
        tcW_r.set(f"{{{_W}}}type", "dxa")
        etree.SubElement(tcPr_r, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
        shift = min_indent + left_col_w
        hdr_elem = build_para_element(hdr_pm, doc_part)
        _shift_indent(hdr_elem, shift)
        tc_r.append(hdr_elem)
        for m in role.meta_lines[1:]:
            m_elem = build_para_element(m, doc_part)
            _shift_indent(m_elem, shift)
            tc_r.append(m_elem)
        for b in role.bullets:
            b_elem = build_para_element(b, doc_part)
            _shift_indent(b_elem, shift)
            tc_r.append(b_elem)
        if not tc_r.findall(f"{{{_W}}}p"):
            etree.SubElement(tc_r, f"{{{_W}}}p")

        return tbl

    def _emit_role_elems_shifted(role, min_indent: int) -> "list":
        """Build shifted paragraph elements for a role (used inside table cells)."""
        result = []
        hdr_pm = _role_header_pm(role)
        date_first = _role_date_first(role)
        two_col = _role_two_col_date(role)
        if two_col:
            result.append(_build_role_two_col_table(role, hdr_pm, min_indent))
        elif date_first:
            elem = build_para_element(role.meta_lines[0], doc_part)
            _shift_indent(elem, min_indent)
            result.append(elem)
            elem = build_para_element(hdr_pm, doc_part)
            _shift_indent(elem, min_indent)
            result.append(elem)
            for m in role.meta_lines[1:]:
                elem = build_para_element(m, doc_part)
                _shift_indent(elem, min_indent)
                result.append(elem)
        else:
            elem = build_para_element(hdr_pm, doc_part)
            _shift_indent(elem, min_indent)
            result.append(elem)
            for m in role.meta_lines:
                elem = build_para_element(m, doc_part)
                _shift_indent(elem, min_indent)
                result.append(elem)
        if not two_col:
            for b in role.bullets:
                elem = build_para_element(b, doc_part)
                _shift_indent(elem, min_indent)
                result.append(elem)
        return result

    def _emit_role_elems(role) -> "list":
        """Build paragraph elements for a role (used outside table cells)."""
        result = []
        hdr_pm = _role_header_pm(role)
        date_first = _role_date_first(role)
        two_col = _role_two_col_date(role)
        if two_col:
            result.append(_build_role_two_col_table(role, hdr_pm))
        elif date_first:
            result.append(build_para_element(role.meta_lines[0], doc_part))
            result.append(build_para_element(hdr_pm, doc_part))
            for m in role.meta_lines[1:]:
                result.append(build_para_element(m, doc_part))
            for b in role.bullets:
                result.append(build_para_element(b, doc_part))
        else:
            result.append(build_para_element(hdr_pm, doc_part))
            for m in role.meta_lines:
                result.append(build_para_element(m, doc_part))
            for b in role.bullets:
                result.append(build_para_element(b, doc_part))
        return result

    def _build_section_paras_for_cell(sec, min_indent: int) -> "list":
        """Build paragraph elements for a table cell, normalizing indent to cell origin."""
        elems = []
        heading_elem = build_para_element(sec.heading, doc_part)
        _shift_indent(heading_elem, min_indent)
        if sec.heading.para_id in _h_rule_borders:
            _apply_h_rule_top_border(heading_elem, _h_rule_borders[sec.heading.para_id])
        elems.append(heading_elem)
        if sec.semantic_type == "experience" and sec.roles:
            _has_orphan = any(bp.semantic == "role_header" for bp in sec.body_paras)
            if _has_orphan:
                _claimed = {id(pm) for role in sec.roles for pm in role.meta_lines}
                for bp in sec.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip() and id(bp) not in _claimed:
                        elem = build_para_element(bp, doc_part)
                        _shift_indent(elem, min_indent)
                        elems.append(elem)
            for role in sec.roles:
                elems.extend(_emit_role_elems_shifted(role, min_indent))
        else:
            for pm in sec.body_paras:
                elem = build_para_element(pm, doc_part)
                _shift_indent(elem, min_indent)
                elems.append(elem)
        return elems

    def _build_section_paras(sec) -> "list":
        heading_elem = build_para_element(sec.heading, doc_part)
        if sec.heading.para_id in _h_rule_borders:
            _apply_h_rule_top_border(heading_elem, _h_rule_borders[sec.heading.para_id])
        elems: list = [heading_elem]
        if sec.semantic_type == "experience" and sec.roles:
            _has_orphan = any(bp.semantic == "role_header" for bp in sec.body_paras)
            if _has_orphan:
                # Skip body_paras that are already claimed by role meta_lines
                # (Pattern B: date appears in both body_paras and role.meta_lines)
                _claimed = {id(pm) for role in sec.roles for pm in role.meta_lines}
                for bp in sec.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip() and id(bp) not in _claimed:
                        elems.append(build_para_element(bp, doc_part))
            for role in sec.roles:
                elems.extend(_emit_role_elems(role))
        else:
            two_col_rows = _detect_body_two_col(sec.body_paras)
            if two_col_rows:
                col_w = text_area_w_twips // 2
                tbl = etree.Element(f"{{{_W}}}tbl")
                tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
                tblW_el = etree.SubElement(tblPr, f"{{{_W}}}tblW")
                tblW_el.set(f"{{{_W}}}w", str(col_w * 2))
                tblW_el.set(f"{{{_W}}}type", "dxa")
                tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
                tblLayout.set(f"{{{_W}}}type", "fixed")
                tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
                for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
                    brd.set(f"{{{_W}}}val", "none")
                tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
                for side in ("top", "left", "bottom", "right"):
                    m_el = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
                    m_el.set(f"{{{_W}}}w", "0")
                    m_el.set(f"{{{_W}}}type", "dxa")
                for left_pm, right_pm in two_col_rows:
                    tr = etree.SubElement(tbl, f"{{{_W}}}tr")
                    for pm in (left_pm, right_pm):
                        tc = etree.SubElement(tr, f"{{{_W}}}tc")
                        tcPr = etree.SubElement(tc, f"{{{_W}}}tcPr")
                        tcW = etree.SubElement(tcPr, f"{{{_W}}}tcW")
                        tcW.set(f"{{{_W}}}w", str(col_w))
                        tcW.set(f"{{{_W}}}type", "dxa")
                        etree.SubElement(tcPr, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
                        if pm is not None:
                            tc.append(build_para_element(pm, doc_part))
                        else:
                            etree.SubElement(tc, f"{{{_W}}}p")
                elems.append(tbl)
            else:
                for pm in sec.body_paras:
                    elems.append(build_para_element(pm, doc_part))
        return elems

    # Header paragraphs first
    for pm in doc.header_paras:
        _add(build_para_element(pm, doc_part))

    for i, sec in enumerate(doc.sections):
        if i not in grouped_idxs:
            # Render normally
            for elem in _build_section_paras(sec):
                _add(elem)
        else:
            # Find which group this section belongs to
            grp = next((g for g in groups if i in g), None)
            if grp is None:
                for elem in _build_section_paras(sec):
                    _add(elem)
                continue
            key = tuple(grp)
            if key in rendered_group_keys:
                continue  # already rendered as a table
            rendered_group_keys.add(key)

            n = len(grp)
            col_w = text_area_w_twips // n

            # Build N-column borderless table
            tbl = etree.Element(f"{{{_W}}}tbl")
            tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
            tblW_el = etree.SubElement(tblPr, f"{{{_W}}}tblW")
            tblW_el.set(f"{{{_W}}}w", str(col_w * n))
            tblW_el.set(f"{{{_W}}}type", "dxa")
            tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
            tblLayout.set(f"{{{_W}}}type", "fixed")
            tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                brd = etree.SubElement(tblBorders, f"{{{_W}}}{side}")
                brd.set(f"{{{_W}}}val", "none")
            tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
            for side in ("top", "left", "bottom", "right"):
                m_el = etree.SubElement(tblCellMar, f"{{{_W}}}{side}")
                m_el.set(f"{{{_W}}}w", "0")
                m_el.set(f"{{{_W}}}type", "dxa")

            tr = etree.SubElement(tbl, f"{{{_W}}}tr")
            for sec_idx in grp:
                grp_sec = doc.sections[sec_idx]
                min_ind = _section_min_indent_twips(grp_sec)
                tc = etree.SubElement(tr, f"{{{_W}}}tc")
                tcPr = etree.SubElement(tc, f"{{{_W}}}tcPr")
                tcW = etree.SubElement(tcPr, f"{{{_W}}}tcW")
                tcW.set(f"{{{_W}}}w", str(col_w))
                tcW.set(f"{{{_W}}}type", "dxa")
                etree.SubElement(tcPr, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
                for elem in _build_section_paras_for_cell(grp_sec, min_ind):
                    tc.append(elem)
                if not tc.findall(f"{{{_W}}}p"):
                    etree.SubElement(tc, f"{{{_W}}}p")

            _add(tbl)


def _render_pdf_single_col_dark_header(doc: "ResumeDocument", body, sectPr, doc_part=None) -> None:
    """Render a single-column PDF with full-width dark header (and optional footer) bands.

    Used when layout.column_split_x is None but layout.header_bg_color is set
    (dark header detected via pixel sampling rather than vector rectangle).
    Creates a full-page-width header table with dark shading at the very top of the
    page (top margin = 0), renders all body paragraphs in single-column below it,
    then optionally appends a dark footer table for contact strips.
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    layout = doc.layout
    pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    page_w_twips = int(pgSz.get(f"{{{_W}}}w", "12240")) if pgSz is not None else 12240
    left_margin_twips = int(pgMar.get(f"{{{_W}}}left", "1440")) if pgMar is not None else 1440

    # Zero top margin so the dark header band starts at the physical page top
    if pgMar is not None:
        pgMar.set(f"{{{_W}}}top", "0")
        pgMar.set(f"{{{_W}}}header", "0")

    # Build the full-width header band table
    hdr_tbl = _make_full_width_dark_band(
        layout.header_bg_color, page_w_twips, left_margin_twips,
        doc.header_paras, doc_part=doc_part, center_text=True,
    )

    # Collect body paragraphs (all_paras minus header_paras)
    _header_ids = frozenset(id(pm) for pm in doc.header_paras)
    main_paras = [pm for pm in doc.all_paras if id(pm) not in _header_ids]

    # Footer paragraphs are stored separately in doc.footer_paras (preserved by
    # compile_resume_from_pdf before apply_tailored replaces section content).
    # Multiple footer paras (phone / email / address on separate fitz blocks) are
    # merged into a SINGLE paragraph joined by "    " (spaces) so the footer band
    # occupies one line height instead of three, preventing page overflow.
    _raw_footer = list(doc.footer_paras) if doc.footer_paras else []

    ftr_tbl = None
    if _raw_footer and layout.footer_bg_color:
        if len(_raw_footer) > 1:
            from tailor.compiler.models import ParaModel as _PM
            _merged_text = "     ".join(pm.text.strip() for pm in _raw_footer if pm.text.strip())
            _arch = _raw_footer[0]
            _merged_pm = _arch.with_text(_merged_text)
            _mpp = _merged_pm.paragraph_profile
            if _mpp:
                _mpp.alignment = "center"
                _mpp.space_before_pt = 4.0
                _mpp.space_after_pt = 4.0
            footer_paras_render = [_merged_pm]
        else:
            footer_paras_render = _raw_footer
        ftr_tbl = _make_full_width_dark_band(
            layout.footer_bg_color, page_w_twips, left_margin_twips,
            footer_paras_render, doc_part=doc_part, center_text=True,
        )

    # Insert everything before sectPr
    def _add(elem):
        if sectPr is not None:
            sectPr.addprevious(elem)
        else:
            body.append(elem)

    _h_rule_borders = _build_h_rule_border_map(doc)
    _add(hdr_tbl)
    for pm in main_paras:
        elem = build_para_element(pm, doc_part=doc_part)
        if _h_rule_borders and pm.para_id in _h_rule_borders:
            _apply_h_rule_top_border(elem, _h_rule_borders[pm.para_id])
        _add(elem)
    if ftr_tbl is not None:
        _add(ftr_tbl)


def _sec_has_parallel_body(sec) -> bool:
    """True if a section has both left-column and right-column body paragraphs.

    Used to detect the parallel-body layout (e.g. Sample 25) where section
    headings are shared and the body content runs in two independent columns.
    """
    has_left = any(
        pm.paragraph_profile and pm.paragraph_profile.column_id == "left"
        for pm in sec.body_paras
    )
    has_right = any(
        pm.paragraph_profile and pm.paragraph_profile.column_id == "right"
        for pm in sec.body_paras
    )
    return has_left and has_right


def _render_pdf_two_col(doc: "ResumeDocument", body, sectPr, doc_part=None) -> None:
    """Render a two-column PDF-sourced document as a borderless DOCX table.

    Creates a full-page-width w:tbl pushed to the page left edge via a negative
    w:tblInd (bypassing the left margin).  Left cell width =
    layout.left_col_width_twips (from the visual sidebar boundary); right cell
    fills the remainder.  The left cell receives cell shading from
    layout.left_col_bg_color when available.  All paragraphs tagged
    column_id='left' go into the left cell; everything else goes right.

    When a dark header band is detected the header paragraphs are placed in a
    merged first row (gridSpan across all columns) inside the SAME table.
    This avoids the two-separate-tables-with-negative-tblInd structure that
    causes LibreOffice to strip cell shading on the standalone header band.
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
    # right_w_max uses full page width (no right-margin deduction): the table
    # already occupies to the physical page right edge via tblInd offset, so
    # subtracting the right margin would incorrectly shrink the right column.
    left_w = layout.left_col_width_twips or (page_w_twips // 3)
    right_w_max = page_w_twips - left_w
    right_w = min(
        layout.right_col_width_twips or right_w_max,
        right_w_max,
    )
    right_w = max(right_w, 2000)  # floor: prevent degenerate right cell
    total_w = left_w + right_w

    # Classify header_paras early so we know whether a dark header row is needed
    # before building the table element.
    _header_ids: frozenset[int] = frozenset(id(pm) for pm in doc.header_paras)

    def _is_dark_bg(pm) -> bool:
        pp = pm.paragraph_profile
        if pp is None:
            return False
        bg = pp.background_color
        if not bg:
            return False
        try:
            r = int(bg[0:2], 16)
            g = int(bg[2:4], 16)
            b = int(bg[4:6], 16)
            return (r + g + b) / 3 < 128
        except (ValueError, IndexError):
            return False

    above_paras = [
        pm for pm in doc.header_paras
        if _is_dark_bg(pm)
        or not (pm.paragraph_profile and pm.paragraph_profile.column_id in ("left", "right"))
    ]
    _above_ids = frozenset(id(pm) for pm in above_paras)
    _hdr_left = [
        pm for pm in doc.header_paras
        if id(pm) not in _above_ids
        and pm.paragraph_profile and pm.paragraph_profile.column_id == "left"
    ]
    _hdr_right = [
        pm for pm in doc.header_paras
        if id(pm) not in _above_ids
        and pm.paragraph_profile and pm.paragraph_profile.column_id == "right"
    ]
    body_paras = [pm for pm in doc.all_paras if id(pm) not in _header_ids]
    _body_left = [
        pm for pm in body_paras
        if pm.paragraph_profile and pm.paragraph_profile.column_id == "left"
    ]
    # column_id=None body paras (cross-column or unassigned) fall back to the
    # right column so LLM-injected sections whose archetype was a full-width
    # header para are not silently dropped from the rendered output.
    _body_right = [
        pm for pm in body_paras
        if not pm.paragraph_profile
        or pm.paragraph_profile.column_id in ("right", None)
    ]

    def _y_of(pm):
        pp = pm.paragraph_profile
        if pp is None:
            return float("inf")
        # Use absolute Y so multi-page PDFs order correctly.  y_top_pt is
        # page-relative (resets to 0 at each page top), so we add page_num *
        # page_height to get a monotonically increasing coordinate.
        # LLM-injected body paragraphs (para_id=None, y=0) sort last.
        page_h = (layout.page_height_pt or 792.0)
        abs_y = pp.page_num * page_h + (pp.y_top_pt or 0.0)
        if abs_y == 0.0 and not pm.para_id:
            return float("inf")
        return abs_y

    # Sort each column's *body* portion by absolute Y so paragraphs render in
    # visual top-to-bottom order.  The IR can store paras out of y-order when a
    # section boundary is detected (e.g. "Education" at y=263) before earlier
    # content (e.g. contact icons at y=86) classified in the same column later.
    # _hdr_* lists are NOT sorted: the pipeline (e.g. _inject_llm_summary_into_header)
    # already places header items in the correct visual sequence; re-sorting them
    # would displace LLM-injected summary paragraphs (y=0, no para_id) that were
    # appended after the name header.
    above_paras.sort(key=_y_of)
    _body_left.sort(key=_y_of)
    _body_right.sort(key=_y_of)
    left_paras  = _hdr_left  + _body_left
    right_paras = _hdr_right + _body_right

    # Geometry diagnostics: log column assignments and y-positions for
    # layout drift analysis.
    import logging as _rlog
    _glog = _rlog.getLogger("tailor.layout_geometry")
    _glog.debug(
        "GEOMETRY_REPORT sample=%s page=%.0fx%.0fpt "
        "split_x=%.1fpt left_w=%.1fpt right_w=%.1fpt above=%d left=%d right=%d",
        getattr(doc, "source_filename", "?"),
        layout.page_width_pt or 0,
        layout.page_height_pt or 0,
        layout.column_split_x or 0,
        left_w / 20.0,
        right_w / 20.0,
        len(above_paras),
        len(left_paras),
        len(right_paras),
    )
    for _pm in left_paras[:3]:
        _glog.debug("  LEFT  y=%.1f %r", _y_of(_pm), (_pm.text or "")[:30])
    for _pm in right_paras[:3]:
        _glog.debug("  RIGHT y=%.1f %r", _y_of(_pm), (_pm.text or "")[:30])

    # Background promotion: if all paragraphs in the left column share the same
    # background_color and the layout has no explicit left_col_bg_color, use
    # that color as cell shading so the background is continuous across
    # space_before/space_after gaps rather than showing white between sections.
    _eff_left_bg = layout.left_col_bg_color
    if not _eff_left_bg and left_paras:
        _left_bgs = {
            pm.paragraph_profile.background_color
            for pm in left_paras
            if pm.paragraph_profile and pm.paragraph_profile.background_color
        }
        if len(_left_bgs) == 1:
            _candidate = _left_bgs.pop()
            _h = _candidate.lstrip("#").lower()
            if len(_h) == 6:
                _r, _g, _b = int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16)
                if (_r + _g + _b) / 3 >= 128:  # light backgrounds only
                    _eff_left_bg = _candidate
                    _glog.debug("LAYOUT_BG_PROMOTED left_cell_bg=#%s (from uniform para bg)", _eff_left_bg)

    _eff_right_bg = layout.right_col_bg_color
    if not _eff_right_bg and right_paras:
        _right_bgs = {
            pm.paragraph_profile.background_color
            for pm in right_paras
            if pm.paragraph_profile and pm.paragraph_profile.background_color
        }
        if len(_right_bgs) == 1:
            _candidate = _right_bgs.pop()
            _h = _candidate.lstrip("#").lower()
            if len(_h) == 6:
                _r, _g, _b = int(_h[0:2], 16), int(_h[2:4], 16), int(_h[4:6], 16)
                if (_r + _g + _b) / 3 >= 128:  # light backgrounds only
                    _eff_right_bg = _candidate
                    _glog.debug("LAYOUT_BG_PROMOTED right_cell_bg=#%s (from uniform para bg)", _eff_right_bg)

    # Detect header band color (first above_para with a background color).
    header_bg = next(
        (pm.paragraph_profile.background_color
         for pm in above_paras
         if pm.paragraph_profile and pm.paragraph_profile.background_color),
        None,
    )
    # has_dark_hdr: True only when the header band is visually dark (lum < 128).
    # Controls full-page-width extension, top-margin zeroing, and whether
    # above_paras go inside the table header row (dark) or before the table as
    # regular paragraphs (light/unassigned).  Light-background templates with
    # col=None paragraphs at the top (e.g. the candidate name) should NOT
    # trigger dark-header behavior — they render before the table via above_elems.
    has_dark_hdr = any(_is_dark_bg(pm) for pm in above_paras)

    # When a dark header band is present the table is extended to the full
    # physical page width (page_w_twips) so the merged header row covers both
    # page margins.  A right-margin padding column (pad_w) fills the extra grid
    # slot in the body row without widening the content cells.
    pad_w = max(0, page_w_twips - total_w) if has_dark_hdr else 0
    _use_pad = has_dark_hdr and pad_w > 0
    n_grid_cols = 3 if _use_pad else 2
    actual_tbl_w = page_w_twips if has_dark_hdr else total_w

    # Table element
    tbl = etree.Element(f"{{{_W}}}tbl")

    # Table properties: width, negative indent to reach physical left page edge,
    # fixed layout, no borders, no cell margins.
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW.set(f"{{{_W}}}w", str(actual_tbl_w))
    tblW.set(f"{{{_W}}}type", "dxa")

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

    # Grid column definitions — required for gridSpan in the merged header row.
    tblGrid = etree.SubElement(tbl, f"{{{_W}}}tblGrid")
    for cw in ([left_w, right_w, pad_w] if _use_pad else [left_w, right_w]):
        gc = etree.SubElement(tblGrid, f"{{{_W}}}gridCol")
        gc.set(f"{{{_W}}}w", str(cw))

    # Header row — merged cell spanning all grid columns with dark shading.
    # Placed inside this table (not a separate table) so LibreOffice reliably
    # renders the cell shading when converting DOCX → PDF.
    if has_dark_hdr:
        if sectPr is not None:
            _pgMar = sectPr.find(f"{{{_W}}}pgMar")
            if _pgMar is None:
                _pgMar = etree.SubElement(sectPr, f"{{{_W}}}pgMar")
            _pgMar.set(f"{{{_W}}}top", "0")
            _pgMar.set(f"{{{_W}}}header", "0")

        hdr_tr = etree.SubElement(tbl, f"{{{_W}}}tr")
        hdr_tc = etree.SubElement(hdr_tr, f"{{{_W}}}tc")
        hdr_tcPr = etree.SubElement(hdr_tc, f"{{{_W}}}tcPr")
        hdr_tcW = etree.SubElement(hdr_tcPr, f"{{{_W}}}tcW")
        hdr_tcW.set(f"{{{_W}}}w", str(actual_tbl_w))
        hdr_tcW.set(f"{{{_W}}}type", "dxa")
        hdr_gs = etree.SubElement(hdr_tcPr, f"{{{_W}}}gridSpan")
        hdr_gs.set(f"{{{_W}}}val", str(n_grid_cols))
        hdr_shd = etree.SubElement(hdr_tcPr, f"{{{_W}}}shd")
        hdr_shd.set(f"{{{_W}}}val", "clear")
        hdr_shd.set(f"{{{_W}}}color", "auto")
        hdr_shd.set(f"{{{_W}}}fill", header_bg)
        for pm in above_paras:
            hdr_tc.append(build_para_element(pm, doc_part=doc_part, skip_bg_shd=False))
        if not above_paras:
            etree.SubElement(hdr_tc, f"{{{_W}}}p")

    # Detect parallel-body layout: any section has PARALLEL_BODY render_mode (from
    # template_ir geometry classification) or has both left and right body_paras detected
    # at runtime.  FULL_WIDTH alone does NOT trigger parallel mode — that would incorrectly
    # force per-section-row rendering on sidebar templates where only some sections happen
    # to have body text crossing the column split.
    # When active, each section renders as a merged full-width heading row followed by
    # a body row whose format depends on the section's render_mode:
    #   PARALLEL_BODY → left|right two-column body row
    #   FULL_WIDTH    → single merged full-width body row (only when parallel mode active)
    #   SINGLE_COLUMN (or None) → left|right row with right cell empty
    _parallel_mode = (
        any(sec.render_mode == "PARALLEL_BODY" for sec in doc.sections)
        or any(_sec_has_parallel_body(sec) for sec in doc.sections)
    )

    def _append_left_indent(p_elem, pm) -> None:
        """Add left_margin_twips to a paragraph element's left indent."""
        pPr = p_elem.find(f"{{{_W}}}pPr")
        if pPr is not None:
            ind = pPr.find(f"{{{_W}}}ind")
            if ind is not None:
                cur = int(ind.get(f"{{{_W}}}left", "0"))
                ind.set(f"{{{_W}}}left", str(cur + left_margin_twips))
            else:
                new_ind = etree.SubElement(pPr, f"{{{_W}}}ind")
                new_ind.set(f"{{{_W}}}left", str(left_margin_twips))

    if _parallel_mode:
        # ------------------------------------------------------------------ #
        # Parallel-body multi-row rendering                                   #
        # Each section: merged heading row + left|right body row.            #
        # Section headings span full width; body columns are independent.    #
        # ------------------------------------------------------------------ #
        _par_count = sum(1 for _s in doc.sections if _sec_has_parallel_body(_s))
        _glog.debug(
            "PARALLEL_SECTION_DETECTED doc=%s parallel_sections=%d total_sections=%d",
            getattr(doc, "source_filename", "?"), _par_count, len(doc.sections),
        )

        def _zero_cell_top_spacing(p_elem) -> None:
            """Zero space_before on the first paragraph of a table cell.

            Both cells in a parallel body row start at the same vertical position.
            Space_before on the first para of each cell pushes that cell's content
            down, breaking visual alignment between left and right columns.
            Standard DOCX practice: cells own their top padding; paragraphs inside
            cells should have space_before=0 for the first item.
            """
            pPr = p_elem.find(f"{{{_W}}}pPr")
            if pPr is not None:
                spB = pPr.find(f"{{{_W}}}spacing")
                if spB is not None:
                    spB.set(f"{{{_W}}}before", "0")
                else:
                    new_sp = etree.SubElement(pPr, f"{{{_W}}}spacing")
                    new_sp.set(f"{{{_W}}}before", "0")

        def _make_two_col_row(l_paras, r_paras) -> None:
            """Append a left|right body row to the table."""
            _tr = etree.SubElement(tbl, f"{{{_W}}}tr")
            # Left cell
            _ltc = etree.SubElement(_tr, f"{{{_W}}}tc")
            _ltcPr = etree.SubElement(_ltc, f"{{{_W}}}tcPr")
            _ltcW = etree.SubElement(_ltcPr, f"{{{_W}}}tcW")
            _ltcW.set(f"{{{_W}}}w", str(left_w))
            _ltcW.set(f"{{{_W}}}type", "dxa")
            if _eff_left_bg:
                _shd = etree.SubElement(_ltcPr, f"{{{_W}}}shd")
                _shd.set(f"{{{_W}}}val", "clear")
                _shd.set(f"{{{_W}}}color", "auto")
                _shd.set(f"{{{_W}}}fill", _eff_left_bg)
            for _i, _pm in enumerate(l_paras):
                _pe = build_para_element(_pm, doc_part=doc_part)
                _append_left_indent(_pe, _pm)
                if _i == 0:
                    _zero_cell_top_spacing(_pe)
                _ltc.append(_pe)
            if not l_paras:
                etree.SubElement(_ltc, f"{{{_W}}}p")
            # Right cell
            _rtc = etree.SubElement(_tr, f"{{{_W}}}tc")
            _rtcPr = etree.SubElement(_rtc, f"{{{_W}}}tcPr")
            _rtcW = etree.SubElement(_rtcPr, f"{{{_W}}}tcW")
            _rtcW.set(f"{{{_W}}}w", str(right_w))
            _rtcW.set(f"{{{_W}}}type", "dxa")
            if _eff_right_bg:
                _shd = etree.SubElement(_rtcPr, f"{{{_W}}}shd")
                _shd.set(f"{{{_W}}}val", "clear")
                _shd.set(f"{{{_W}}}color", "auto")
                _shd.set(f"{{{_W}}}fill", _eff_right_bg)
            for _i, _pm in enumerate(r_paras):
                _pe = build_para_element(_pm, doc_part=doc_part)
                if _i == 0:
                    _zero_cell_top_spacing(_pe)
                _rtc.append(_pe)
            if not r_paras:
                etree.SubElement(_rtc, f"{{{_W}}}p")
            if _use_pad:
                _ptc = etree.SubElement(_tr, f"{{{_W}}}tc")
                _ptcPr = etree.SubElement(_ptc, f"{{{_W}}}tcPr")
                _ptcW = etree.SubElement(_ptcPr, f"{{{_W}}}tcW")
                _ptcW.set(f"{{{_W}}}w", str(pad_w))
                _ptcW.set(f"{{{_W}}}type", "dxa")
                etree.SubElement(_ptc, f"{{{_W}}}p")

        def _make_merged_hdr_row(pm) -> None:
            """Append a full-width merged row for a section heading."""
            _tr = etree.SubElement(tbl, f"{{{_W}}}tr")
            _htc = etree.SubElement(_tr, f"{{{_W}}}tc")
            _htcPr = etree.SubElement(_htc, f"{{{_W}}}tcPr")
            _htcW = etree.SubElement(_htcPr, f"{{{_W}}}tcW")
            _htcW.set(f"{{{_W}}}w", str(actual_tbl_w))
            _htcW.set(f"{{{_W}}}type", "dxa")
            _gs = etree.SubElement(_htcPr, f"{{{_W}}}gridSpan")
            _gs.set(f"{{{_W}}}val", str(n_grid_cols))
            _pe = build_para_element(pm, doc_part=doc_part)
            # Apply the same left_margin_twips correction as left-cell paras so
            # the heading sits at its original PDF x position inside the merged cell.
            _append_left_indent(_pe, pm)
            _htc.append(_pe)

        def _make_full_width_body_row(paras) -> None:
            """Append a full-width merged body row (for FULL_WIDTH sections)."""
            _tr = etree.SubElement(tbl, f"{{{_W}}}tr")
            _tc = etree.SubElement(_tr, f"{{{_W}}}tc")
            _tcPr = etree.SubElement(_tc, f"{{{_W}}}tcPr")
            _tcW = etree.SubElement(_tcPr, f"{{{_W}}}tcW")
            _tcW.set(f"{{{_W}}}w", str(actual_tbl_w))
            _tcW.set(f"{{{_W}}}type", "dxa")
            _fgs = etree.SubElement(_tcPr, f"{{{_W}}}gridSpan")
            _fgs.set(f"{{{_W}}}val", str(n_grid_cols))
            for _pm in paras:
                _pe = build_para_element(_pm, doc_part=doc_part)
                _append_left_indent(_pe, _pm)
                _tc.append(_pe)
            if not paras:
                etree.SubElement(_tc, f"{{{_W}}}p")

        # Sort sections by heading y, then render each as: heading row + body row.
        _sorted_secs = sorted(
            doc.sections,
            key=lambda _s: (
                (_s.heading.paragraph_profile.y_top_pt or 0.0)
                if _s.heading.paragraph_profile else 0.0
            ),
        )
        for _sec in _sorted_secs:
            _sec_mode = _sec.render_mode  # 'FULL_WIDTH' | 'PARALLEL_BODY' | 'SINGLE_COLUMN' | None
            _lbody: list = sorted(
                [_pm for _pm in _sec.body_paras
                 if _pm.paragraph_profile
                 and _pm.paragraph_profile.column_id == "left"],
                key=_y_of,
            )
            _rbody: list = sorted(
                [_pm for _pm in _sec.body_paras
                 if _pm.paragraph_profile
                 and _pm.paragraph_profile.column_id == "right"],
                key=_y_of,
            )
            # Experience sections store body content in roles, not body_paras.
            for _role in _sec.roles:
                _lbody.extend([_role.header, *_role.meta_lines, *_role.bullets])
            _lbody.sort(key=_y_of)
            _is_par = bool(_lbody and _rbody)
            _glog.debug(
                "SECTION_RENDER_MODE sec=%r mode=%s left=%d right=%d parallel=%s",
                _sec.title, _sec_mode, len(_lbody), len(_rbody), _is_par,
            )
            if _is_par:
                _left_y0 = _y_of(_lbody[0]) if _lbody else 0.0
                _right_y0 = _y_of(_rbody[0]) if _rbody else 0.0
                _glog.debug(
                    "PARALLEL_ALIGNMENT section=%r left_y=%.1f right_y=%.1f delta=%.1f",
                    _sec.title, _left_y0, _right_y0, abs(_left_y0 - _right_y0),
                )
                _glog.debug(
                    "PARALLEL_SECTION_RENDERED sec=%r left_items=%d right_items=%d",
                    _sec.title, len(_lbody), len(_rbody),
                )
            _make_merged_hdr_row(_sec.heading)
            if _sec_mode == "FULL_WIDTH":
                # Body spans full width — collect all body_paras regardless of column_id.
                _fw_body: list = list(_sec.body_paras)
                for _role in _sec.roles:
                    _fw_body.extend([_role.header, *_role.meta_lines, *_role.bullets])
                _fw_body.sort(key=_y_of)
                _make_full_width_body_row(_fw_body)
            else:
                _make_two_col_row(_lbody, _rbody)

    else:
        # ------------------------------------------------------------------ #
        # Standard single-body-row rendering.                                 #
        # ------------------------------------------------------------------ #
        # Body row
        tr = etree.SubElement(tbl, f"{{{_W}}}tr")

        # Left cell
        left_tc = etree.SubElement(tr, f"{{{_W}}}tc")
        left_tcPr = etree.SubElement(left_tc, f"{{{_W}}}tcPr")
        left_tcW = etree.SubElement(left_tcPr, f"{{{_W}}}tcW")
        left_tcW.set(f"{{{_W}}}w", str(left_w))
        left_tcW.set(f"{{{_W}}}type", "dxa")
        if _eff_left_bg:
            shd = etree.SubElement(left_tcPr, f"{{{_W}}}shd")
            shd.set(f"{{{_W}}}val", "clear")
            shd.set(f"{{{_W}}}color", "auto")
            shd.set(f"{{{_W}}}fill", _eff_left_bg)

        # Right cell
        right_tc = etree.SubElement(tr, f"{{{_W}}}tc")
        right_tcPr = etree.SubElement(right_tc, f"{{{_W}}}tcPr")
        right_tcW = etree.SubElement(right_tcPr, f"{{{_W}}}tcW")
        right_tcW.set(f"{{{_W}}}w", str(right_w))
        right_tcW.set(f"{{{_W}}}type", "dxa")
        if _eff_right_bg:
            shd = etree.SubElement(right_tcPr, f"{{{_W}}}shd")
            shd.set(f"{{{_W}}}val", "clear")
            shd.set(f"{{{_W}}}color", "auto")
            shd.set(f"{{{_W}}}fill", _eff_right_bg)

        # Right-margin padding cell — empty spacer filling the third grid column.
        if _use_pad:
            pad_tc = etree.SubElement(tr, f"{{{_W}}}tc")
            pad_tcPr = etree.SubElement(pad_tc, f"{{{_W}}}tcPr")
            pad_tcW = etree.SubElement(pad_tcPr, f"{{{_W}}}tcW")
            pad_tcW.set(f"{{{_W}}}w", str(pad_w))
            pad_tcW.set(f"{{{_W}}}type", "dxa")
            etree.SubElement(pad_tc, f"{{{_W}}}p")

        # Pre-compute each left para's "section heading indent" so body paras that
        # are more indented than their section heading can be capped to the heading
        # level.  This normalises over-indented body items without affecting headings
        # or items flush with their section heading.
        _sec_heading_ind_twips: list[int] = []
        _cur_heading_ind = 0
        for pm in left_paras:
            pp = pm.paragraph_profile
            if pm.semantic == "section_heading" and pp is not None:
                _cur_heading_ind = int(pp.indent_left_pt * 20)
            _sec_heading_ind_twips.append(_cur_heading_ind)

        for i, pm in enumerate(left_paras):
            p_elem = build_para_element(pm, doc_part=doc_part)
            # Shift all left-cell content right by left_margin_twips so it sits at
            # the same x position as in the source PDF.
            pPr = p_elem.find(f"{{{_W}}}pPr")
            if pPr is not None:
                ind = pPr.find(f"{{{_W}}}ind")
                heading_ind = _sec_heading_ind_twips[i]
                if ind is not None:
                    cur = int(ind.get(f"{{{_W}}}left", "0"))
                    if pm.semantic != "section_heading":
                        cur = min(cur, heading_ind)
                    ind.set(f"{{{_W}}}left", str(cur + left_margin_twips))
                else:
                    new_ind = etree.SubElement(pPr, f"{{{_W}}}ind")
                    new_ind.set(f"{{{_W}}}left", str(left_margin_twips))
            left_tc.append(p_elem)
        if not left_paras:
            etree.SubElement(left_tc, f"{{{_W}}}p")

        for pm in right_paras:
            right_tc.append(build_para_element(pm, doc_part=doc_part))
        if not right_paras:
            etree.SubElement(right_tc, f"{{{_W}}}p")

    # When there is no dark header band, above_paras are plain full-width
    # paragraphs (e.g. contact info with no background) inserted before the table.
    above_elems = (
        [] if has_dark_hdr
        else [build_para_element(pm, doc_part=doc_part) for pm in above_paras]
    )

    # Footer band: render doc.footer_paras in a full-width dark band after the main
    # table.  footer_bg_color may be inferred from the header color when pixel-sampling
    # missed the footer (e.g. sample 25 where footer items were redistributed into
    # section body_paras rather than captured as footer_paras directly).
    _ftr_tbl = None
    _raw_footer = list(doc.footer_paras) if doc.footer_paras else []

    # Capture the original footer raster y-position before we filter page_images,
    # so we can bottom-anchor our generated footer band at the same location.
    _footer_raster_y: "float | None" = None
    _ph = layout.page_height_pt or 841.9
    _pw = layout.page_width_pt or 595.3
    if _raw_footer and layout.footer_bg_color and getattr(doc, "page_images", None):
        for _img in doc.page_images:
            if (
                _img.category in ("header_footer_decor", "footer_band")
                and _img.width_pt >= _pw * 0.70
                and (_img.y_pt + _img.height_pt) > _ph * 0.75
            ):
                _footer_raster_y = _img.y_pt
                break

    if _raw_footer and layout.footer_bg_color:
        if len(_raw_footer) > 1:
            _merged_text = "     ".join(pm.text.strip() for pm in _raw_footer if pm.text.strip())
            _arch = _raw_footer[0]
            _merged_pm = _arch.with_text(_merged_text)
            _mpp = _merged_pm.paragraph_profile
            if _mpp:
                _mpp.alignment = "center"
                _mpp.text_color = "ffffff"
                _mpp.space_before_pt = 6.0
                _mpp.space_after_pt = 6.0
                _mpp.numbering = None
            else:
                from tailor.compiler.models import ParagraphProfile as _PP
                _merged_pm.paragraph_profile = _PP(
                    alignment="center", text_color="ffffff",
                    space_before_pt=6.0, space_after_pt=6.0,
                )
            _merged_pm.semantic = "paragraph"  # strip bullet prefix (Target 5)
            _footer_render = [_merged_pm]
        else:
            # Single item: clone to avoid mutating the original para.
            _single = _raw_footer[0].with_text(_raw_footer[0].text)
            _single.semantic = "paragraph"
            if _single.paragraph_profile:
                _single.paragraph_profile.numbering = None
            _footer_render = [_single]
        _ftr_tbl = _make_full_width_dark_band(
            layout.footer_bg_color, page_w_twips, left_margin_twips,
            _footer_render, doc_part=doc_part, center_text=True,
        )

        # Target 4: bottom-anchor footer band at its original page position.
        # tblpPr makes the table a floating element positioned absolutely from the
        # page top, so it lands at the original PDF footer location regardless of
        # how much body content the LLM produced.
        if _footer_raster_y is None and _raw_footer:
            _fpm = _raw_footer[0].paragraph_profile
            if _fpm and _fpm.y_pt:
                # Para y minus one row height (approximately space_before + line) gives band top.
                _footer_raster_y = max(0.0, _fpm.y_pt - 10.0)
        if _footer_raster_y is not None:
            _ftr_tblPr = _ftr_tbl.find(f"{{{_W}}}tblPr")
            if _ftr_tblPr is not None:
                _tblpPr = etree.SubElement(_ftr_tblPr, f"{{{_W}}}tblpPr")
                _tblpPr.set(f"{{{_W}}}leftFromText", "0")
                _tblpPr.set(f"{{{_W}}}rightFromText", "0")
                _tblpPr.set(f"{{{_W}}}vertAnchor", "page")
                _tblpPr.set(f"{{{_W}}}horzAnchor", "page")
                _tblpPr.set(f"{{{_W}}}tblpX", "0")
                _tblpPr.set(f"{{{_W}}}tblpY", str(int(_footer_raster_y * 20)))
            _glog.debug(
                "FOOTER_RENDERED items=%d bg=%s anchor_y=%.1f",
                len(_raw_footer), layout.footer_bg_color, _footer_raster_y,
            )
        else:
            _glog.debug("FOOTER_RENDERED items=%d bg=%s", len(_raw_footer), layout.footer_bg_color)

    if sectPr is not None:
        for elem in above_elems:
            sectPr.addprevious(elem)
        sectPr.addprevious(tbl)
        if _ftr_tbl is not None:
            sectPr.addprevious(_ftr_tbl)
    else:
        for elem in above_elems:
            body.append(elem)
        body.append(tbl)
        if _ftr_tbl is not None:
            body.append(_ftr_tbl)

    # Targets 1 & 3: Suppress raster header/footer band images that are replaced by
    # DOCX-generated equivalents (merged header row + footer band table).
    # These wide raster images were captured from the original PDF as page_images with
    # category="header_footer_decor". Leaving them in creates duplicate dark areas:
    # the floating raster at the original position AND our DOCX table cell elsewhere.
    if getattr(doc, "page_images", None):
        _suppress_hdr = has_dark_hdr
        _suppress_ftr = _ftr_tbl is not None

        def _is_wide_band_raster(img, zone: str) -> bool:
            if img.category not in ("header_footer_decor", "header_band", "footer_band"):
                return False
            if img.width_pt < _pw * 0.70:
                return False
            if zone == "header":
                return img.y_pt < _ph * 0.30
            return (img.y_pt + img.height_pt) > _ph * 0.75  # footer zone

        doc.page_images = [
            img for img in doc.page_images
            if not (
                (_suppress_hdr and _is_wide_band_raster(img, "header"))
                or (_suppress_ftr and _is_wide_band_raster(img, "footer"))
            )
        ]


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
    the main table content (samples 13/14).  Zeroing all spacing (before, after,
    and line-height) compresses the whitespace gap between the header summary
    and the table, allowing the table to start on page 1 rather than being
    pushed to page 2.  Uses exact line-height=1 (near-zero) so that LibreOffice
    renders the paragraph as a hairline rather than a full line-height gap.
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

    behindDoc anchors whose height covers the full page (cy ≥ 10 M EMU,
    roughly 11 inches) get layoutInCell='0' so they use page-level
    coordinates and do NOT force the table row to match their height.

    This covers both full-page-width backgrounds (cx ≥ 7 M EMU) and
    narrow-column sidebar backgrounds (e.g. sample 11 blue left sidebar at
    cx ≈ 2.85 M EMU but cy = 11 in).  If a background is tall enough to
    span the page it must always be page-relative; letting it be
    cell-relative forces the containing table row to be 11 inches tall,
    pushing all body rows to page 2.

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
            cy = int(ext.get("cy", "0"))
        except ValueError:
            continue
        if cy >= 10_000_000:
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


def _promote_cell_bg_drawings_to_body(body, sectPr) -> None:
    """Promote page-relative behind-doc drawings from table cells to body level.

    When a two-column table is built, behindDoc anchors with
    relativeFrom="page" (both H and V) inside table cells are rendered by
    LibreOffice relative to the cell origin instead of the page origin.  This
    causes full-page background drawings (e.g. the dark header in sample 3)
    to disappear or render at the wrong position.

    Fix: extract just the <w:drawing> element (NOT the whole paragraph) from
    each cell paragraph that contains such an anchor.  The drawing is placed
    in a zero-height body paragraph BEFORE the table; the original paragraph
    stays in the cell with its text content intact.  This works for both
    drawing-only paragraphs and mixed drawing+text paragraphs (e.g. the
    contact-email paragraph in sample 3 that doubles as the anchor host).
    """
    from copy import deepcopy as _dc
    from lxml import etree as _et

    _W_NS = f"{{{_W}}}"
    _WP_NS = f"{{{_WP}}}"

    # Find the first table in body (the two-col table just built)
    tbl = next((c for c in body if c.tag == f"{_W_NS}tbl"), None)
    if tbl is None:
        return

    extracted_drawings: list = []
    for tc in tbl.findall(f".//{_W_NS}tc"):
        for p in tc.findall(f"{_W_NS}p"):
            for anchor in list(p.findall(f".//{_WP_NS}anchor")):
                if anchor.get("behindDoc") != "1":
                    continue
                posH = anchor.find(f"{_WP_NS}positionH")
                posV = anchor.find(f"{_WP_NS}positionV")
                h_page = posH is not None and posH.get("relativeFrom") == "page"
                v_page = posV is not None and posV.get("relativeFrom") == "page"
                if not (h_page and v_page):
                    continue
                # Find the <w:drawing> parent of this anchor
                drawing = anchor.getparent()
                if drawing is None or drawing.tag != f"{_W_NS}drawing":
                    continue
                dc_drawing = _dc(drawing)
                # Set layoutInCell='0' on any anchor in the extracted drawing
                # so LibreOffice treats it as page-relative even at body level.
                for _extr_anchor in dc_drawing.findall(f".//{_WP_NS}anchor"):
                    _extr_anchor.set("layoutInCell", "0")
                extracted_drawings.append(dc_drawing)
                drawing_parent = drawing.getparent()
                if drawing_parent is not None:
                    drawing_parent.remove(drawing)

    if not extracted_drawings:
        return

    # Create zero-height body paragraphs containing the drawings and insert
    # them BEFORE the table so they render at page coordinates.
    tbl_idx = list(body).index(tbl)
    for offset, drawing in enumerate(extracted_drawings):
        bg_p = _et.Element(f"{_W_NS}p")
        bg_pPr = _et.SubElement(bg_p, f"{_W_NS}pPr")
        bg_sp = _et.SubElement(bg_pPr, f"{_W_NS}spacing")
        bg_sp.set(f"{_W_NS}before", "0")
        bg_sp.set(f"{_W_NS}after", "0")
        bg_sp.set(f"{_W_NS}line", "1")
        bg_sp.set(f"{_W_NS}lineRule", "exact")
        bg_r = _et.SubElement(bg_p, f"{_W_NS}r")
        bg_r.append(drawing)
        body.insert(tbl_idx + offset, bg_p)

    _log.debug(
        "CELL_BG_PROMOTED_TO_BODY: extracted %d behind-doc drawing(s) before table",
        len(extracted_drawings),
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


_WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
_BULLET_MARKER_MAX_EMU = 100_000   # cx/cy ≤ this → small bullet dot, not a line


def _strip_column_bullet_drawings(tc_elem) -> None:
    """Remove small absolutely-positioned bullet-marker drawings from a table cell.

    Templates like sample 20 use absolutely-positioned tiny shapes (cx≈38100 EMU,
    relativeFrom='column') as bullet markers.  Inside a table cell these cannot use
    the native 'column' reference (which points to the original narrow left column,
    not the wide right cell), so LibreOffice renders them in the wrong column.

    This function:
    1. Scans every <w:p> in the cell for small anchor drawings (cx ≤ 100k EMU).
    2. For each such paragraph (always an empty spacer after the bullet text),
       identifies the immediately preceding non-empty text paragraph as the
       intended bullet target.
    3. Removes the drawing paragraph.
    4. Injects a '• ' prefix run into the identified bullet text paragraph so the
       bullet marker appears inline at the correct position.
    """
    paras = list(tc_elem.findall(f"{{{_W}}}p"))
    to_remove: list = []
    to_bullet: list = []
    prev_text_p = None

    for p_elem in paras:
        drawings = p_elem.findall(f".//{{{_W}}}drawing")
        is_bullet_marker = False
        for d in drawings:
            for anc in d.findall(f".//{{{_WP_NS}}}anchor"):
                ext = anc.find(f"{{{_WP_NS}}}extent")
                if ext is None:
                    continue
                try:
                    cx = int(ext.get("cx", "0"))
                    cy = int(ext.get("cy", "0"))
                except ValueError:
                    continue
                if cx <= _BULLET_MARKER_MAX_EMU and cy <= _BULLET_MARKER_MAX_EMU:
                    is_bullet_marker = True
                    break
            if is_bullet_marker:
                break

        if is_bullet_marker:
            to_remove.append(p_elem)
            if prev_text_p is not None:
                to_bullet.append(prev_text_p)
            prev_text_p = None  # consumed — reset so we don't double-bullet
        else:
            ts = p_elem.findall(f".//{{{_W}}}t")
            if any(t.text and t.text.strip() for t in ts):
                prev_text_p = p_elem
            # empty paras don't reset prev_text_p

    for p_elem in to_remove:
        parent = p_elem.getparent()
        if parent is not None:
            parent.remove(p_elem)

    for p_elem in to_bullet:
        runs = p_elem.findall(f"{{{_W}}}r")
        if runs:
            # Prepend bullet character to first run's text
            first_t = runs[0].find(f"{{{_W}}}t")
            if first_t is not None:
                existing = first_t.text or ""
                first_t.text = "• " + existing
                first_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
        else:
            # No runs — add a minimal run with bullet char before any existing runs
            from lxml import etree as _et
            new_r = _et.Element(f"{{{_W}}}r")
            new_t = _et.SubElement(new_r, f"{{{_W}}}t")
            new_t.text = "• "
            new_t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            pPr = p_elem.find(f"{{{_W}}}pPr")
            if pPr is not None:
                pPr.addnext(new_r)
            else:
                p_elem.insert(0, new_r)


def _is_docx_section_header_left_layout(doc, left_blocks) -> bool:
    """Return True when the left column contains ONLY section-heading + spacer blocks.

    This pattern appears in templates like sample 20 where the native 2-column
    layout has a narrow left column that holds only section labels (Summary, Work
    Experience, …) separated by blank spacers, while all body content lives in the
    wide right column.  Unlike the independent-column layout these sections are
    logically paired: each label belongs with the body content beside it.

    Detection: every layout block in the left column that carries `<w:t>` text
    content must be the heading of one of doc.sections (excluding the first
    'other' section, which is the header-table content).  The presence of a
    TableBlock (the name/contact header table) is expected and allowed.
    """
    # Collect heading para_ids for sections[1:] (skip sections[0] = header table)
    heading_pids: set[str] = set()
    for sec in doc.sections[1:]:
        if sec.heading and sec.heading.para_id:
            heading_pids.add(sec.heading.para_id)

    if len(heading_pids) < 2:
        return False  # need at least 2 sections

    for blk in left_blocks:
        if isinstance(blk, LayoutTableBlock):
            continue  # header table is fine
        if not isinstance(blk, LayoutParagraphBlock):
            continue
        # A block with visible text that is NOT a section heading → body content
        if blk.para_id and "<w:t>" in (blk.xml_proto_xml or ""):
            if blk.para_id not in heading_pids:
                return False

    return True


def _render_docx_section_row_table(
    doc,
    body,
    sectPr,
    col_break_idx: int,
    main_pgSz_w,
    main_pgSz_h,
    main_is_multicolumn: bool,
) -> None:
    """Render a section-header-left DOCX template as a multi-row section table.

    Used for templates like sample 20 where the native 2-column layout places
    section labels (Summary, Work Experience, …) in the narrow left column and
    all body content in the wide right column.  Unlike the single-row 2-cell
    table produced by _render_layout_two_col_table, this function creates ONE
    TABLE ROW PER SECTION so that each label stays vertically aligned with its
    corresponding body content regardless of how much the content grows.

    Column widths are read from the template's w:cols spec.  A thin top border
    on every cell replicates the horizontal divider line that the original
    template renders via absolutely-positioned image shapes.
    """
    from copy import deepcopy
    from lxml import etree

    para_lookup = _build_para_lookup(doc)

    # ── Column geometry ──────────────────────────────────────────────────────
    _pgSz = sectPr.find(f"{{{_W}}}pgSz") if sectPr is not None else None
    _pgMar = sectPr.find(f"{{{_W}}}pgMar") if sectPr is not None else None
    _page_w = int(_pgSz.get(f"{{{_W}}}w", "12240")) if _pgSz is not None else 12240
    _mar_left = int(_pgMar.get(f"{{{_W}}}left", "0")) if _pgMar is not None else 0
    _mar_right = int(_pgMar.get(f"{{{_W}}}right", "0")) if _pgMar is not None else 0
    _text_area = max(_page_w - _mar_left - _mar_right, 1)

    left_w = right_w = 0
    if sectPr is not None:
        cols_elem = sectPr.find(f"{{{_W}}}cols")
        if cols_elem is not None:
            col_elems = cols_elem.findall(f"{{{_W}}}col")
            if len(col_elems) >= 2:
                try:
                    left_w = int(col_elems[0].get(f"{{{_W}}}w", "0"))
                    right_w = int(col_elems[1].get(f"{{{_W}}}w", "0"))
                except ValueError:
                    pass
    if left_w == 0 or right_w == 0:
        left_w = _text_area // 3
        right_w = _text_area - left_w

    # Remove w:cols so LibreOffice uses table layout, not native column flow.
    if sectPr is not None:
        cols_to_remove = sectPr.find(f"{{{_W}}}cols")
        if cols_to_remove is not None:
            sectPr.remove(cols_to_remove)

    left_blocks = list(doc.layout_blocks[:col_break_idx])   # type: ignore[index]
    right_blocks = list(doc.layout_blocks[col_break_idx + 1:])  # type: ignore[index]

    # ── Section heading para_id mappings ────────────────────────────────────
    # sections[0] is the "other" section whose content lives in the header table
    # (name, title, license).  We start section rows from sections[1].
    heading_pid_to_si: dict[str, int] = {}
    for si, sec in enumerate(doc.sections):
        if si == 0:
            continue
        if sec.heading and sec.heading.para_id:
            heading_pid_to_si[sec.heading.para_id] = si

    # ── para_id → section_index for right-column content ────────────────────
    para_to_si: dict[str, int] = {}
    for si, sec in enumerate(doc.sections):
        if si == 0:
            continue
        for pm in sec.body_paras:
            if pm.para_id:
                para_to_si[pm.para_id] = si
        for role in sec.roles:
            for pm in ([role.header]
                       + list(role.header_extra)
                       + list(role.meta_lines)
                       + list(role.bullets)):
                if pm and pm.para_id:
                    para_to_si[pm.para_id] = si

    # ── Split right_blocks into per-section buckets ──────────────────────────
    n_sections = len(doc.sections)
    section_right_blocks: list[list] = [[] for _ in range(n_sections)]
    last_si = 1  # fallback section for unrecognised blocks
    for blk in right_blocks:
        if isinstance(blk, LayoutParagraphBlock):
            if blk.para_id:
                si = para_to_si.get(blk.para_id)
                if si is not None:
                    last_si = si
                    section_right_blocks[si].append(blk)
                # else: spacer/orphan — skip
            # else: unbound block (para_id="") — skip; handled via all_paras below

    # ── Find heading blocks in left_blocks ───────────────────────────────────
    heading_block_for_si: dict[int, LayoutParagraphBlock] = {}
    for blk in left_blocks:
        if isinstance(blk, LayoutParagraphBlock) and blk.para_id in heading_pid_to_si:
            si = heading_pid_to_si[blk.para_id]
            heading_block_for_si[si] = blk

    # ── Render the header TableBlock at body level first ─────────────────────
    for blk in left_blocks:
        if isinstance(blk, LayoutTableBlock) and blk.xml_proto_xml:
            tbl_elem = etree.fromstring(blk.xml_proto_xml)
            all_p = tbl_elem.findall(f".//{{{_W}}}p")
            for para_id, p_elem in zip(blk.para_ids, all_p):
                pm = para_lookup.get(para_id)
                if pm is not None:
                    _strip_text_wrapping_breaks(p_elem, pm.text)
                    _set_para_text(p_elem, pm.text)
                    _clear_sdt_placeholder(p_elem)
            if sectPr is not None:
                sectPr.addprevious(tbl_elem)
            else:
                body.append(tbl_elem)
            break  # only one header table expected

    # ── Build the section-row table ──────────────────────────────────────────
    tbl = etree.Element(f"{{{_W}}}tbl")
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW_el = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW_el.set(f"{{{_W}}}w", str(left_w + right_w))
    tblW_el.set(f"{{{_W}}}type", "dxa")
    tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
    tblLayout.set(f"{{{_W}}}type", "fixed")
    _add_tbl_no_borders(tblPr)
    _add_tbl_zero_cell_margins(tblPr)

    def _add_divider_border(tcPr_elem) -> None:
        """Add a thin black top border to replicate the section divider line."""
        tc_brd = etree.SubElement(tcPr_elem, f"{{{_W}}}tcBorders")
        top = etree.SubElement(tc_brd, f"{{{_W}}}top")
        top.set(f"{{{_W}}}val", "single")
        top.set(f"{{{_W}}}sz", "6")
        top.set(f"{{{_W}}}color", "000000")

    def _make_tc(tr_elem, w_twips: int, add_border: bool) -> "Any":
        """Create a table cell with given width and optional top divider border."""
        tc = etree.SubElement(tr_elem, f"{{{_W}}}tc")
        tcPr = etree.SubElement(tc, f"{{{_W}}}tcPr")
        tcW = etree.SubElement(tcPr, f"{{{_W}}}tcW")
        tcW.set(f"{{{_W}}}w", str(w_twips))
        tcW.set(f"{{{_W}}}type", "dxa")
        etree.SubElement(tcPr, f"{{{_W}}}vAlign").set(f"{{{_W}}}val", "top")
        if add_border:
            _add_divider_border(tcPr)
        return tc

    last_right_tc = None
    for si, sec in enumerate(doc.sections):
        if si == 0:
            continue  # header-table section — already rendered above

        tr = etree.SubElement(tbl, f"{{{_W}}}tr")

        # Left cell: section heading
        left_tc = _make_tc(tr, left_w, add_border=True)
        h_blk = heading_block_for_si.get(si)
        if h_blk is not None:
            h_elem = _render_block_into_elem(
                h_blk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn
            )
            if h_elem is not None:
                # Strip absolutely-positioned drawings from the heading paragraph
                # (the original template embeds them here as divider-line anchors;
                # we use the table top-border instead so positioning stays correct).
                for drawing in list(h_elem.findall(f".//{{{_W}}}drawing")):
                    parent = drawing.getparent()
                    if parent is not None:
                        parent.remove(drawing)
                left_tc.append(h_elem)
        if not left_tc.findall(f"{{{_W}}}p"):
            etree.SubElement(left_tc, f"{{{_W}}}p")

        # Right cell: section body content
        right_tc = _make_tc(tr, right_w, add_border=True)
        last_right_tc = right_tc

        for blk in section_right_blocks[si]:
            r_elem = _render_block_into_elem(
                blk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn
            )
            if r_elem is not None:
                right_tc.append(r_elem)

        # Strip absolutely-positioned bullet-marker drawings and inject '• ' text so
        # bullets appear inline in the right cell instead of bleeding into the left.
        _strip_column_bullet_drawings(right_tc)

        if not right_tc.findall(f"{{{_W}}}p"):
            etree.SubElement(right_tc, f"{{{_W}}}p")

    # Append unbound extra paras (LLM overflow with no para_id) to last right cell.
    if last_right_tc is not None:
        for pm in (doc.all_paras or []):
            if not pm.para_id and pm.text.strip():
                if pm.style.xml_proto is not None:
                    from copy import deepcopy as _dc
                    p_elem = _dc(pm.style.xml_proto)
                    _set_para_text(p_elem, pm.text)
                    last_right_tc.append(p_elem)
                elif pm.paragraph_profile is not None:
                    from tailor.compiler.para_builder import build_para_element
                    last_right_tc.append(build_para_element(pm))

    # Insert the section-row table into the body.
    if sectPr is not None:
        sectPr.addprevious(tbl)
    else:
        body.append(tbl)


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


_SPACER_LINE_THRESHOLD = 500  # twips; spacer paras above this push content to column bottom


def _collapse_oversized_spacer(elem) -> bool:
    """Reduce extreme line-spacing on empty template spacer paragraphs.

    Some templates use a paragraph with w:spacing w:line set to a very large
    value (e.g. 2541 twips ≈ 127 pt) to push the next section (e.g. AFFILIATIONS)
    to the bottom of the right column.  When the LLM-rendered content is shorter
    than the original, this spacer creates a large blank gap.  Reduce it to a
    minimal line height so AFFILIATIONS follows directly after CONTACT INFORMATION.

    Returns True if the spacer was collapsed, False otherwise.
    """
    pPr = elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        return False
    spacing = pPr.find(f"{{{_W}}}spacing")
    if spacing is None:
        return False
    line_val = spacing.get(f"{{{_W}}}line")
    if line_val is None:
        return False
    try:
        line_int = int(line_val)
    except ValueError:
        return False
    if line_int > _SPACER_LINE_THRESHOLD:
        # Only collapse when the paragraph carries no text content
        has_text = any(t.text for t in elem.iter(f"{{{_W}}}t"))
        if not has_text:
            spacing.set(f"{{{_W}}}before", "0")
            spacing.set(f"{{{_W}}}after", "0")
            spacing.set(f"{{{_W}}}line", "1")
            spacing.set(f"{{{_W}}}lineRule", "exact")
            return True
    return False


def _minimize_empty_para(elem) -> None:
    """Reduce an empty spacer paragraph to near-zero height (before=0, after=0, line=1)."""
    from lxml import etree as _etree
    has_text = any(t.text for t in elem.iter(f"{{{_W}}}t"))
    if has_text:
        return
    pPr = elem.find(f"{{{_W}}}pPr")
    if pPr is None:
        pPr = _etree.SubElement(elem, f"{{{_W}}}pPr")
        elem.insert(0, pPr)
    spacing = pPr.find(f"{{{_W}}}spacing")
    if spacing is None:
        spacing = _etree.SubElement(pPr, f"{{{_W}}}spacing")
    spacing.set(f"{{{_W}}}before", "0")
    spacing.set(f"{{{_W}}}after", "0")
    spacing.set(f"{{{_W}}}line", "1")
    spacing.set(f"{{{_W}}}lineRule", "exact")


def _cap_para_font_size(elem, max_halfpts: int = 36) -> None:
    """Cap run-level sz/szCs values that exceed max_halfpts (half-points = pt*2).

    Called on _ext_ blocks that clone their XML proto from a large-font heading
    paragraph (e.g. name heading with sz=64 = 32pt).  Without this cap, body
    bullets set into such paragraphs render in oversized font (sample 27).
    Default max 18pt (sz=36) preserves all normal body/title fonts while
    preventing name-heading sizes from bleeding into injected body content.
    """
    for rPr in elem.iter(f"{{{_W}}}rPr"):
        for tag in (f"{{{_W}}}sz", f"{{{_W}}}szCs"):
            sz_el = rPr.find(tag)
            if sz_el is not None:
                val_str = sz_el.get(f"{{{_W}}}val")
                if val_str is not None:
                    try:
                        if int(val_str) > max_halfpts:
                            sz_el.set(f"{{{_W}}}val", str(max_halfpts))
                    except ValueError:
                        pass


def _render_block_into_elem(
    block, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn,
    exempt_font_cap_ids: "frozenset[str] | None" = None,
):
    """Render one LayoutParagraphBlock → lxml element (None if empty).

    *exempt_font_cap_ids*: para_ids whose large run-level fonts must NOT be
    capped even when semantic is non-heading (e.g. header name paragraphs).
    """
    from lxml import etree
    if not block.xml_proto_xml:
        return None
    elem = etree.fromstring(block.xml_proto_xml)
    _strip_last_rendered_page_breaks(elem)
    _strip_non_column_section_break(elem, main_pgSz_w, main_pgSz_h, main_is_multicolumn)
    _strip_column_break(elem)
    pm = para_lookup.get(block.para_id) if block.para_id else None
    if pm is not None:
        # _set_para_text has a guard that early-exits for paragraphs with ≥2 direct
        # w:sdt children (multi-column contact rows like "email TAB phone TAB LinkedIn").
        # The guard is correct for preserved template rows but incorrectly also fires
        # for skills/summary rows that share the same SDT structure when the LLM
        # has replaced their content.  Strip all non-pPr children when:
        #   (a) ext slots: always (cloned proto carries the multi-SDT structure but
        #       needs new LLM text); or
        #   (b) regular slots: the model text differs from what the SDT content
        #       currently holds (LLM replaced this paragraph).
        _sdt_cnt = sum(1 for _c in elem if _c.tag == f"{{{_W}}}sdt")
        if _sdt_cnt >= 2:
            _is_ext = "_ext_" in (block.para_id or "")
            _should_strip = _is_ext
            if not _is_ext and pm.text.strip():
                _xml_parts: list[str] = []
                for _child in elem:
                    if _child.tag == f"{{{_W}}}sdt":
                        _sc = _child.find(f"{{{_W}}}sdtContent")
                        if _sc is not None:
                            for _r in _sc.findall(f".//{{{_W}}}r"):
                                for _t in _r.findall(f"{{{_W}}}t"):
                                    if _t.text:
                                        _xml_parts.append(_t.text)
                _xml_text = " ".join(_xml_parts)
                _norm = lambda s: " ".join(s.lower().split())
                _should_strip = _norm(pm.text) != _norm(_xml_text)
            if _should_strip:
                for _sdt_c in list(elem):
                    if _sdt_c.tag != f"{{{_W}}}pPr":
                        elem.remove(_sdt_c)
        _render_text = pm.text
        if pm.semantic == "role_meta" and "\n" in pm.text:
            _render_text = pm.text.split("\n")[0]
        # Capture proto text and bold state before modification; used below to
        # detect rewritten bold-proto slots where original template text differs
        # from LLM output.  style.bold is not serialised so we read the XML.
        _proto_text_raw = "".join(_t.text or "" for _t in elem.iter(f"{{{_W}}}t"))
        _pPr_proto = elem.find(f"{{{_W}}}pPr")
        _proto_para_bold = (
            _pPr_proto is not None
            and _pPr_proto.find(f"{{{_W}}}rPr/{{{_W}}}b") is not None
        )
        _strip_text_wrapping_breaks(elem, _render_text)
        _set_para_text(elem, _render_text)
        _clear_sdt_placeholder(elem)
        # Cap oversized font when:
        #   (a) _ext_ blocks cloned from large-font heading protos (original guard), OR
        #   (b) any non-heading block whose XML proto carries run-level sz > 36 (18pt)
        #       but whose semantic indicates body content — handles the case where a
        #       name-heading slot (sz=64) is reused for a body paragraph by the updater
        #       (e.g. sample 27 right-column: MATTHEW TURNER proto used for bullets).
        _HEADING_SEMANTICS = frozenset({
            "section_heading", "role_header",
            # Classification-propagated adjunct types — preserve their template
            # formatting (font size, bold label) rather than treating them as
            # generic body content that should be capped or stripped.
            "role_intro", "role_key_technologies", "role_tech_stack",
            "role_project_label",
        })
        _is_exempt_font = (
            exempt_font_cap_ids is not None
            and block.para_id is not None
            and block.para_id in exempt_font_cap_ids
        )
        _needs_font_cap = (
            bool(block.para_id and "_ext_" in block.para_id)
            and pm.semantic not in _HEADING_SEMANTICS
            and not _is_exempt_font
        )
        _proto_run_font_cap: int = 36  # default cap (18pt)
        if not _needs_font_cap and pm.semantic not in _HEADING_SEMANTICS and not _is_exempt_font:
            # Detect run-level rPr with sz > 36 (18pt).  Run rPr is a direct child
            # of w:r; paragraph-default rPr (pPr/rPr) is a direct child of w:pPr.
            for _rPr in elem.iter(f"{{{_W}}}rPr"):
                _parent = _rPr.getparent()
                if _parent is not None and _parent.tag == f"{{{_W}}}r":
                    _sz_el = _rPr.find(f"{{{_W}}}sz")
                    if _sz_el is not None:
                        try:
                            if int(_sz_el.get(f"{{{_W}}}val", "0")) > 36:
                                _needs_font_cap = True
                                break
                        except ValueError:
                            pass
            if _needs_font_cap:
                # Use the paragraph-default rPr sz (pPr/rPr/sz) as the cap
                # when it is smaller than the global default — this restores the
                # body font size rather than capping at 18pt (e.g. sample 27
                # MATTHEW TURNER slot has pPr/rPr/sz=20=10pt which is correct).
                _pPr_cap = elem.find(f"{{{_W}}}pPr")
                if _pPr_cap is not None:
                    _rPr_cap = _pPr_cap.find(f"{{{_W}}}rPr")
                    if _rPr_cap is not None:
                        _sz_cap = _rPr_cap.find(f"{{{_W}}}sz")
                        if _sz_cap is not None:
                            try:
                                _ppr_sz = int(_sz_cap.get(f"{{{_W}}}val", "0"))
                                if 0 < _ppr_sz < _proto_run_font_cap:
                                    _proto_run_font_cap = _ppr_sz
                            except ValueError:
                                pass
        if _needs_font_cap:
            _cap_para_font_size(elem, max_halfpts=_proto_run_font_cap)
        # Injected bullet blocks cloned from a Heading-N role-header inherit the
        # heading paragraph style (e.g. blue bold).  Normalise to "Normal" so the
        # injected content renders as regular body text.
        if _needs_font_cap and pm.semantic in ("bullet", "paragraph"):
            _pPr_norm2 = elem.find(f"{{{_W}}}pPr")
            if _pPr_norm2 is not None:
                _pStyle_norm2 = _pPr_norm2.find(f"{{{_W}}}pStyle")
                if _pStyle_norm2 is not None:
                    _sval2 = _pStyle_norm2.get(f"{{{_W}}}val", "").lower()
                    if _sval2.startswith("heading"):
                        _pStyle_norm2.set(f"{{{_W}}}val", "Normal")
            # Strip explicit run-level bold/italic/color inherited from anchor proto.
            for _rPr_ext2 in elem.iter(f"{{{_W}}}rPr"):
                _r_ext2 = _rPr_ext2.getparent()
                if _r_ext2 is not None and _r_ext2.tag == f"{{{_W}}}r":
                    for _ftag_ext2 in (
                        f"{{{_W}}}b", f"{{{_W}}}bCs",
                        f"{{{_W}}}i", f"{{{_W}}}iCs",
                        f"{{{_W}}}color",
                    ):
                        _fel_ext2 = _rPr_ext2.find(_ftag_ext2)
                        if _fel_ext2 is not None:
                            _rPr_ext2.remove(_fel_ext2)
        # Strip bold from rewritten non-heading paras whose XML proto is ALL BOLD.
        # Template project headers (e.g. "OTA project") are styled all-bold; when
        # the LLM assigns achievement text to such a slot the bold must not carry
        # over.  Detected by comparing proto text (captured before _set_para_text)
        # against the updated pm.text — a mismatch means the slot was rewritten.
        # Bold may be set at paragraph-level (pPr/rPr/b) or run-level (r/rPr/b);
        # both are stripped so the inherited formatting does not persist.
        if (
            pm.text.strip()
            and pm.semantic not in _HEADING_SEMANTICS
            and _proto_para_bold
            and _proto_text_raw.strip() != pm.text.strip()
        ):
            for _rPr_ab in elem.iter(f"{{{_W}}}rPr"):
                _par_ab = _rPr_ab.getparent()
                if _par_ab is not None and _par_ab.tag in (
                    f"{{{_W}}}r", f"{{{_W}}}pPr"
                ):
                    for _btag_ab in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                        _bel_ab = _rPr_ab.find(_btag_ab)
                        if _bel_ab is not None:
                            _rPr_ab.remove(_bel_ab)
        # Strip bullet/list formatting (w:numPr) for semantic types that represent
        # prose or technology-block text, not list items.  Some template paragraphs
        # inherit ListParagraph style with w:numPr when their document position
        # happens to fall inside a numbered list; for role_intro, role_key_technologies,
        # role_tech_stack, and role_project_label content this bullet marker is wrong.
        _NO_BULLET_SEMANTICS = frozenset({
            "role_intro", "role_key_technologies", "role_tech_stack", "role_project_label",
        })
        if pm.semantic in _NO_BULLET_SEMANTICS and pm.text.strip():
            _pPr_nb = elem.find(f"{{{_W}}}pPr")
            if _pPr_nb is not None:
                _numPr_nb = _pPr_nb.find(f"{{{_W}}}numPr")
                if _numPr_nb is not None:
                    _pPr_nb.remove(_numPr_nb)
        # Semantic visual styling: apply italic for narrative intro types and bold
        # for technology/label types so each semantic kind renders distinctly.
        # Applied after bold-strip and bullet-strip so these take final precedence.
        _SEM_ITALIC = frozenset({"role_intro", "project_intro"})
        _SEM_BOLD = frozenset({
            "role_key_technologies", "role_tech_stack",
            "role_project_label", "highlight_header",
        })
        if pm.semantic in _SEM_ITALIC and pm.text.strip():
            _pPr_si = elem.find(f"{{{_W}}}pPr")
            if _pPr_si is not None:
                _rPr_ppr_si = _pPr_si.find(f"{{{_W}}}rPr")
                if _rPr_ppr_si is None:
                    _rPr_ppr_si = etree.SubElement(_pPr_si, f"{{{_W}}}rPr")
                for _t_si in (f"{{{_W}}}i", f"{{{_W}}}iCs"):
                    if _rPr_ppr_si.find(_t_si) is None:
                        etree.SubElement(_rPr_ppr_si, _t_si)
            for _r_si in elem.findall(f".//{{{_W}}}r"):
                _rPr_si = _r_si.find(f"{{{_W}}}rPr")
                if _rPr_si is None:
                    _rPr_si = etree.SubElement(_r_si, f"{{{_W}}}rPr")
                    _r_si.insert(0, _rPr_si)
                for _t_si in (f"{{{_W}}}i", f"{{{_W}}}iCs"):
                    if _rPr_si.find(_t_si) is None:
                        etree.SubElement(_rPr_si, _t_si)
        elif pm.semantic in _SEM_BOLD and pm.text.strip():
            _pPr_sb = elem.find(f"{{{_W}}}pPr")
            if _pPr_sb is not None:
                _rPr_ppr_sb = _pPr_sb.find(f"{{{_W}}}rPr")
                if _rPr_ppr_sb is None:
                    _rPr_ppr_sb = etree.SubElement(_pPr_sb, f"{{{_W}}}rPr")
                for _t_sb in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                    if _rPr_ppr_sb.find(_t_sb) is None:
                        etree.SubElement(_rPr_ppr_sb, _t_sb)
            for _r_sb in elem.findall(f".//{{{_W}}}r"):
                _rPr_sb = _r_sb.find(f"{{{_W}}}rPr")
                if _rPr_sb is None:
                    _rPr_sb = etree.SubElement(_r_sb, f"{{{_W}}}rPr")
                    _r_sb.insert(0, _rPr_sb)
                for _t_sb in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                    if _rPr_sb.find(_t_sb) is None:
                        etree.SubElement(_rPr_sb, _t_sb)
        # Strip display-only (Symbol/Wingdings/SymbolMT) fonts from run rPr so
        # that injected text renders with normal characters instead of garbled
        # symbol glyphs.  These fonts map codepoints to dingbats/symbols rather
        # than letters; any new text placed in such runs shows as Greek or random
        # graphics.  Removing the rFonts element lets LibreOffice use the
        # paragraph's default font for the run.
        _DISPLAY_FONTS = frozenset({
            "symbol", "wingdings", "wingdings2", "wingdings3",
            "symbolmt", "webdings", "marlett",
        })
        if pm.text.strip():
            for _rPr_sym in elem.iter(f"{{{_W}}}rPr"):
                _rFonts_sym = _rPr_sym.find(f"{{{_W}}}rFonts")
                if _rFonts_sym is not None:
                    _font_names = {
                        _rFonts_sym.get(attr, "").lower()
                        for attr in (
                            f"{{{_W}}}ascii", f"{{{_W}}}hAnsi",
                            f"{{{_W}}}cs", f"{{{_W}}}eastAsia",
                        )
                        if _rFonts_sym.get(attr)
                    }
                    if _font_names and _font_names.issubset(_DISPLAY_FONTS):
                        _rPr_sym.remove(_rFonts_sym)
        # Sync cleared indent: the updater may have called _clear_left_indent(pm),
        # setting pm.style.indent_left=None and removing w:left from pm.style.xml_proto.
        # But the renderer uses block.xml_proto_xml (the original template XML) which
        # still carries the old w:left attribute.  When the pm has no explicit
        # indent_left but the elem has an explicit non-zero w:left, the updater
        # cleared it — remove it from elem to match.
        if pm.style.indent_left is None:
            _pPr = elem.find(f"{{{_W}}}pPr")
            if _pPr is not None:
                _ind = _pPr.find(f"{{{_W}}}ind")
                if _ind is not None:
                    _w_left = _ind.get(f"{{{_W}}}left")
                    if _w_left is not None and _w_left != "0":
                        del _ind.attrib[f"{{{_W}}}left"]
                        if not _ind.attrib:
                            _pPr.remove(_ind)
    _collapse_oversized_spacer(elem)
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
    # Include the col-break paragraph itself in the right column.  For templates
    # where the column-break is embedded inside the first right-column heading
    # (e.g. sample 3 "Software Engineer" Heading1), dropping it caused the heading
    # to be lost.  _render_block_into_elem → _strip_column_break removes the
    # break character so the paragraph is rendered normally in the table cell.
    # For templates with a standalone empty col-break paragraph (e.g. sample 27),
    # the leading-empty-trim below discards it harmlessly.
    right_blocks = list(doc.layout_blocks[col_break_idx:])  # type: ignore[index]

    # Skip leading empty (spacer) blocks at the top of the right column.
    # In native 2-column templates these empty paragraphs were column-break
    # follow-up spacers that aligned content relative to the left column.
    # Inside a table cell they create a blank gap above the first real section
    # heading.  A block is "empty" when: (a) it is a LayoutParagraphBlock,
    # (b) its XML proto exists, and (c) neither the XML nor the pm text carries
    # any visible text content.
    #
    # Exception: when the total leading space is small (≤ 700 twips = 35pt),
    # the spacers are intentional vertical positioning — e.g. sample 27 has
    # 596 twips before MATTHEW TURNER to align it with the original header
    # geometry.  Trimming them would move the name too high.  Templates with
    # large cumulative leading space (e.g. 1000+ twips) have column-alignment
    # spacers that do not belong in a table cell and should be removed.
    _para_lookup_for_trim = _build_para_lookup(doc)
    _rb_leading_twips = 0
    for _scan_blk in right_blocks:
        if not isinstance(_scan_blk, LayoutParagraphBlock) or not _scan_blk.xml_proto_xml:
            break
        _pm_s = _para_lookup_for_trim.get(_scan_blk.para_id) if _scan_blk.para_id else None
        if (_pm_s and _pm_s.text.strip()) or "<w:t>" in _scan_blk.xml_proto_xml:
            break
        try:
            _sp_el = etree.fromstring(_scan_blk.xml_proto_xml).find(f".//{{{_W}}}spacing")
            _rb_leading_twips += int(_sp_el.get(f"{{{_W}}}line", "0")) if _sp_el is not None else 0
        except Exception:
            pass
    _rb_start = 0
    if _rb_leading_twips > 700:
        for _i, _blk in enumerate(right_blocks):
            if not isinstance(_blk, LayoutParagraphBlock) or not _blk.xml_proto_xml:
                break  # hit a table block or block without XML — stop trimming
            _pm_trim = _para_lookup_for_trim.get(_blk.para_id) if _blk.para_id else None
            _pm_text_trim = (_pm_trim.text.strip() if _pm_trim else "")
            _has_xml_text_trim = "<w:t>" in _blk.xml_proto_xml
            if _pm_text_trim or _has_xml_text_trim:
                break  # found real content — stop trimming
            _rb_start = _i + 1
    if _rb_start:
        right_blocks = right_blocks[_rb_start:]

    # Strip trailing empty blocks from both columns.
    # In native w:cols templates, empty paragraphs at the END of a column are
    # column-break alignment artifacts — they fill the column to align the
    # column-break point.  Inside a table cell they add dead vertical space
    # below the last real section (e.g. sample 22 has 12 trailing empties after
    # Skills).  Only TRAILING empties are removed; all intra-section empty
    # paragraphs (section heading → spacing pairs → first content) are preserved,
    # so the original template's vertical geometry is intact.
    def _strip_trailing_empty_blocks(blocks, _para_lookup):
        end = len(blocks)
        while end > 0:
            blk = blocks[end - 1]
            if isinstance(blk, LayoutParagraphBlock) and blk.xml_proto_xml:
                _pm = _para_lookup.get(blk.para_id) if blk.para_id else None
                if (
                    "<w:t>" not in blk.xml_proto_xml
                    and "<w:drawing>" not in blk.xml_proto_xml
                    and (_pm is None or not _pm.text.strip())
                ):
                    end -= 1
                    continue
            break
        return blocks[:end]

    left_blocks = _strip_trailing_empty_blocks(left_blocks, para_lookup)
    right_blocks = _strip_trailing_empty_blocks(right_blocks, para_lookup)

    # Collect para_ids of right-column name/title paragraphs whose run-level
    # font size is large (> 36 half-pts = 18pt) so the font-cap logic in
    # _render_block_into_elem skips them.  These slots are identity paragraphs
    # (name, title) that must render at their original large size even when the
    # updater assigned body-semantic text to the slot.  Only inspect the first 3
    # right-column blocks that have visible text content to avoid flagging actual
    # body content.  Skip leading empty spacer paragraphs (which the 700t-threshold
    # heuristic now preserves before the name paragraph, e.g. sample 27).
    _rne_candidates = [
        _b for _b in right_blocks[:8]
        if isinstance(_b, LayoutParagraphBlock)
        and _b.xml_proto_xml
        and "<w:t>" in _b.xml_proto_xml
    ][:3]
    _right_name_exempt: set[str] = set()
    for _rne_blk in _rne_candidates:
        if not isinstance(_rne_blk, LayoutParagraphBlock) or not _rne_blk.para_id or not _rne_blk.xml_proto_xml:
            continue
        try:
            _rne_proto = etree.fromstring(_rne_blk.xml_proto_xml)
            if any(
                int(_sz.get(f"{{{_W}}}val", "0")) > 36
                for _rPr in _rne_proto.iter(f"{{{_W}}}rPr")
                if _rPr.getparent() is not None and _rPr.getparent().tag == f"{{{_W}}}r"
                for _sz in _rPr.findall(f"{{{_W}}}sz")
            ):
                _right_name_exempt.add(_rne_blk.para_id)
        except Exception:
            pass

    # Extract full-page blip background DRAWINGS from left_blocks and insert them
    # as a separate body-level paragraph BEFORE the table.  A behindDoc blip
    # inside a table cell forces LibreOffice to set the row height to the image
    # extent (e.g. A4: 11.70 in), making the entire page blank.
    #
    # Crucially, only the <w:drawing> element is extracted — the surrounding
    # <w:p> paragraph stays in the left cell with its text content (e.g. the
    # candidate name "ABIGAIL NAOMI") and formatting intact.  Extracting the
    # whole paragraph would move the name text out of the table cell, causing it
    # to render in a body-level context where its font size + column width
    # constraints produce a letter-per-line vertical display.
    from copy import deepcopy as _deepcopy
    _blip_drawings: list = []   # extracted <w:drawing> elements
    _left_blocks_filtered: list = []
    for _blk in left_blocks:
        _modified = False
        if (
            isinstance(_blk, LayoutParagraphBlock)
            and _blk.xml_proto_xml
            and "behindDoc" in _blk.xml_proto_xml
            and "blip" in _blk.xml_proto_xml
        ):
            try:
                _pel = etree.fromstring(_blk.xml_proto_xml)
                # Use descendant search — <w:drawing> is typically nested inside
                # a <w:r> run, not a direct child of the paragraph.
                for _drawing in list(_pel.findall(f".//{{{_W}}}drawing")):
                    _anchor = _drawing.find(f".//{{{_WP}}}anchor")
                    if _anchor is not None and _is_bg_anchor_any(_anchor):
                        _blip_drawings.append(_deepcopy(_drawing))
                        # Remove from its actual parent (could be a <w:r> run)
                        _parent = _drawing.getparent()
                        if _parent is not None:
                            _parent.remove(_drawing)
                        _modified = True
                if _modified:
                    # Keep the paragraph (with text, without background drawing)
                    # in its block list using its updated XML.
                    # NOTE: do NOT cap w:right indent here.  These blocks are
                    # rendered as body-level paragraphs (full page width) where
                    # the original right indent (e.g. right=5840) is intentional:
                    # it positions the name text in the left 43% of the page,
                    # leaving the right 57% for the circular photo, and forces
                    # "ABIGAIL" / "NAOMI" to wrap onto separate lines.
                    # Capping the indent (done in a previous approach) made the
                    # text use the full page width → single-line display → header
                    # too short → table starting before the photo's bottom edge.
                    _left_blocks_filtered.append(LayoutParagraphBlock(
                        para_id=_blk.para_id,
                        xml_proto_xml=etree.tostring(_pel).decode(),
                    ))
            except Exception:
                pass
        if not _modified:
            _left_blocks_filtered.append(_blk)
    left_blocks = _left_blocks_filtered

    # Insert a single body-level paragraph containing the background drawings.
    # Wrap the drawing in a <w:r> run (Word XML spec) and add a zero-height
    # pPr so the paragraph itself consumes no vertical space.
    if _blip_drawings:
        _bg_p = etree.Element(f"{{{_W}}}p")
        _bg_pPr = etree.SubElement(_bg_p, f"{{{_W}}}pPr")
        _bg_sp = etree.SubElement(_bg_pPr, f"{{{_W}}}spacing")
        _bg_sp.set(f"{{{_W}}}before", "0")
        _bg_sp.set(f"{{{_W}}}after", "0")
        _bg_sp.set(f"{{{_W}}}line", "1")
        _bg_sp.set(f"{{{_W}}}lineRule", "exact")
        _bg_r = etree.SubElement(_bg_p, f"{{{_W}}}r")
        for _draw in _blip_drawings:
            _bg_r.append(_draw)
        if sectPr is not None:
            sectPr.addprevious(_bg_p)
        else:
            body.append(_bg_p)

    # Separate left_blocks into header blocks (to be rendered as full-width
    # body-level paragraphs BEFORE the table) and section blocks (inside the
    # left table cell).  This restores the merged header area seen in the
    # original template: candidate name + title span the full page width while
    # the 2-column table starts below with section content.
    #
    # NOTE: in the original code ALL left blocks went into the left cell to
    # avoid pushing the table to page 2 (the header consumed too much vertical
    # space on page 1).  For templates with compact headers (< 2 in of height)
    # this trade-off reversal is safe.  The _needs_table_for_contact guard that
    # activates this path ensures we only reach here for such templates.
    _header_para_ids_set: set[str] = {
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    }
    # Also include para_ids from any injected summary section (sec_summary_inserted).
    # _find_summary_anchors consumes empty header_para slots to create this section,
    # removing them from doc.header_paras.  Without this guard those paras stay in
    # the left table cell and appear alongside (not above) the right column content.
    for _sec in (doc.sections or []):
        if getattr(_sec, "section_id", None) == "sec_summary_inserted":
            if _sec.heading and _sec.heading.para_id:
                _header_para_ids_set.add(_sec.heading.para_id)
            for _bp in _sec.body_paras:
                if _bp.para_id:
                    _header_para_ids_set.add(_bp.para_id)
    _header_para_ids: frozenset[str] = frozenset(_header_para_ids_set)
    _header_left_blocks = [
        blk for blk in left_blocks
        if isinstance(blk, LayoutParagraphBlock) and blk.para_id in _header_para_ids
    ]
    _section_left_blocks = [
        blk for blk in left_blocks
        if not (isinstance(blk, LayoutParagraphBlock) and blk.para_id in _header_para_ids)
    ]
    # Safety: if all left blocks are header blocks (nothing left for the left cell),
    # try to split at the first dark-text non-empty paragraph to separate the
    # truly full-width header (name/title with white text on dark background) from
    # the left-column content (contact/education/skills with dark text).
    # Example: sample 3 has para_1='CHARLES MCTURLAND' (white, sz=66) and
    # para_2='SOFTWARE ENGINEER' (white, sz=31) as the header, while para_7+
    # (contact info, dark color=202529) belongs in the left cell.
    # If no white-to-dark transition is found, fall back: all into left cell.
    if not _section_left_blocks and _header_left_blocks:
        _split_at: int | None = None
        for _hi, _hblk in enumerate(_header_left_blocks):
            if not isinstance(_hblk, LayoutParagraphBlock) or not _hblk.xml_proto_xml:
                continue
            _hblk_xml = etree.fromstring(_hblk.xml_proto_xml)
            _hblk_text = "".join(
                t.text or "" for t in _hblk_xml.findall(f".//{{{_W}}}t")
            ).strip()
            if not _hblk_text:
                continue  # empty spacer — skip for split detection
            _has_white = any(
                c.get(f"{{{_W}}}val", "").upper() == "FFFFFF"
                for c in _hblk_xml.findall(f".//{{{_W}}}color")
            )
            if not _has_white:
                # First non-empty paragraph WITHOUT white text = start of left-col content
                _split_at = _hi
                break
        if _split_at is not None and _split_at > 0:
            # White-text blocks (name/title) → full-width before table
            # Dark-text blocks (contact/edu/skills) → left cell
            _section_left_blocks = _header_left_blocks[_split_at:]
            _header_left_blocks = _header_left_blocks[:_split_at]
            _white_text_split_applied = True
        else:
            # No clear split: all into left cell (original safe fallback)
            _section_left_blocks = _header_left_blocks
            _header_left_blocks = []
            _white_text_split_applied = False
    else:
        _white_text_split_applied = False

    # Right-column-name guard: when the right column starts with candidate name/title
    # content (non-section-heading paragraph), the template uses a split-header layout
    # where the left contact block and right name sit at the same vertical level.
    # Extracting left header blocks to the body would push them ABOVE the right-column
    # name, breaking visual alignment.  Keep all left blocks in the left cell instead.
    # Example: sample 16 where left=contact-info and right=HARPER RUSSO / DEVOPS.
    # Skip when _white_text_split_applied: the white-text heuristic already correctly
    # determined which blocks are the full-width header vs. left-column content.
    if _header_left_blocks and not _white_text_split_applied:
        _first_right_pm = None
        for _rblk in right_blocks:
            if isinstance(_rblk, LayoutParagraphBlock) and _rblk.para_id:
                _rblk_pm = para_lookup.get(_rblk.para_id)
                if _rblk_pm and _rblk_pm.text.strip():
                    _first_right_pm = _rblk_pm
                    break
        if _first_right_pm is not None and _first_right_pm.semantic not in {
            "section_heading", "empty"
        }:
            # Right column starts with name/title — keep contact/header in left cell.
            _section_left_blocks = _header_left_blocks + _section_left_blocks
            _header_left_blocks = []

    # Render header blocks as body-level paragraphs (full page width).
    # Exempt header paragraphs from font capping so name/title keep their
    # large template font sizes (e.g. sample 22 "Lydia Mary" at 35pt).
    for _hblk in _header_left_blocks:
        _hel = _render_block_into_elem(
            _hblk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn,
            exempt_font_cap_ids=_header_para_ids,
        )
        if _hel is not None:
            if sectPr is not None:
                sectPr.addprevious(_hel)
            else:
                body.append(_hel)

    # Intro-zone extraction (sample 19 pattern): when the left column starts with
    # an intro zone (title section + contact table + summary) before the first
    # "real" content section (which has roles), extract the intro zone as body-level
    # paragraphs so the 2-col table starts at WORK EXPERIENCE level — aligning with
    # RELEVANT SKILLS in the right column.
    # Guard: only applies when there is a LayoutTableBlock in the intro zone AND
    # a content section (with roles) exists in the left column blocks.
    _sec_with_roles_pids: frozenset[str] = frozenset(
        sec.heading.para_id
        for sec in (doc.sections or [])
        if sec.heading and sec.heading.para_id and sec.roles
    )
    _intro_scan_table_seen: bool = False
    _intro_first_content_idx: "int | None" = None
    for _ii, _iblk in enumerate(_section_left_blocks):
        if isinstance(_iblk, LayoutTableBlock):
            _intro_scan_table_seen = True
        elif (
            isinstance(_iblk, LayoutParagraphBlock)
            and _iblk.para_id
            and _iblk.para_id in _sec_with_roles_pids
        ):
            _intro_first_content_idx = _ii
            break
    if (
        _intro_scan_table_seen
        and _intro_first_content_idx is not None
        and 0 < _intro_first_content_idx <= 20
        and _intro_first_content_idx < len(_section_left_blocks)
    ):
        _intro_zone_blocks = _section_left_blocks[:_intro_first_content_idx]
        _section_left_blocks = _section_left_blocks[_intro_first_content_idx:]
        for _izblk in _intro_zone_blocks:
            if isinstance(_izblk, LayoutTableBlock):
                _iztbl = etree.fromstring(_izblk.xml_proto_xml)
                for _iz_pid, _iz_pel in zip(_izblk.para_ids, _iztbl.findall(f".//{{{_W}}}p")):
                    _iz_pm = para_lookup.get(_iz_pid)
                    if _iz_pm is not None:
                        _set_para_text(_iz_pel, _iz_pm.text)
                        _clear_sdt_placeholder(_iz_pel)
                if sectPr is not None:
                    sectPr.addprevious(_iztbl)
                else:
                    body.append(_iztbl)
            else:
                _izel = _render_block_into_elem(
                    _izblk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn,
                )
                if _izel is not None:
                    if sectPr is not None:
                        sectPr.addprevious(_izel)
                    else:
                        body.append(_izel)

    left_blocks = _section_left_blocks

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

    def _fill_cell(tc, blocks, col_x_emu: int, extra_paras=None, exempt_font_cap_ids=None) -> None:
        _cell_consec_empty: int = 0
        # Compression guards: only compress BETWEEN section content, never before the
        # first section heading (prevents collapsing name/title spacers in right cells,
        # e.g. sample 16 where spacers before PROFILE must be preserved) or right after
        # a section heading (preserves heading→content breathing room).
        _cell_section_seen: bool = False
        _cell_prev_was_section: bool = False
        for blk in blocks:
            if isinstance(blk, LayoutTableBlock):
                tbl_el = etree.fromstring(blk.xml_proto_xml)
                for para_id, p_el in zip(blk.para_ids, tbl_el.findall(f".//{{{_W}}}p")):
                    pm = para_lookup.get(para_id)
                    if pm is not None:
                        _set_para_text(p_el, pm.text)
                        _clear_sdt_placeholder(p_el)
                tc.append(tbl_el)
                _cell_consec_empty = 0
                _cell_prev_was_section = False
            else:
                el = _render_block_into_elem(
                    blk, para_lookup, main_pgSz_w, main_pgSz_h, main_is_multicolumn,
                    exempt_font_cap_ids=exempt_font_cap_ids,
                )
                if el is not None:
                    # Convert column-relative background anchor positions to page-relative
                    # BEFORE w:cols is used by LibreOffice: prevents background image
                    # from shifting when the column reference changes (sample 16 fix).
                    _fix_col_relative_anchors(el, col_x_emu)
                    # Consecutive-empty compression: minimize 2nd+ consecutive empty paras
                    # to remove blank gaps between roles (sample 22).
                    # Guards:
                    # - skip header_para blocks (contact info area, sample 16 left cell)
                    # - skip when no section_heading seen yet (name/title spacers in right
                    #   cells must be preserved, sample 16 DEVOPS→PROFILE spacers)
                    # - skip immediately after a section_heading (preserve heading breathing)
                    _fc_has_text = any(t.text for t in el.iter(f"{{{_W}}}t"))
                    _fc_pid = blk.para_id if isinstance(blk, LayoutParagraphBlock) else None
                    _fc_is_header = bool(_fc_pid and _fc_pid in _header_para_ids)
                    if _fc_has_text or _fc_is_header:
                        _fc_pm = para_lookup.get(_fc_pid) if _fc_pid else None
                        _fc_is_section = bool(
                            _fc_pm and _fc_pm.semantic == "section_heading"
                        )
                        if _fc_is_section:
                            _cell_section_seen = True
                            _cell_prev_was_section = True
                        else:
                            _cell_prev_was_section = False
                        _cell_consec_empty = 0
                    else:
                        # Empty para: compress only when inside content (after first
                        # section heading and not immediately following a section heading)
                        if (
                            _cell_section_seen
                            and not _cell_prev_was_section
                            and not _fc_is_header
                        ):
                            _cell_consec_empty += 1
                            if _cell_consec_empty >= 2:
                                _minimize_empty_para(el)
                        else:
                            _cell_consec_empty = 0
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

    # Restore right-column paragraphs whose large-font proto was hijacked as a
    # bullet slot by the updater (e.g. sample 27: "MATTHEW TURNER" at sz=64 gets
    # assigned bullet text because the parser included it in the WORK HISTORY role).
    # A heading/name paragraph in the right column must keep its original text.
    _BODY_SEM_RC = frozenset({"bullet", "paragraph"})
    for _rc_blk in right_blocks:
        if not isinstance(_rc_blk, LayoutParagraphBlock) or not _rc_blk.xml_proto_xml:
            continue
        if not _rc_blk.para_id:
            continue
        _rc_pm = para_lookup.get(_rc_blk.para_id)
        if _rc_pm is None or _rc_pm.semantic not in _BODY_SEM_RC or not _rc_pm.text.strip():
            continue
        try:
            _rc_proto = etree.fromstring(_rc_blk.xml_proto_xml)
            _has_large_rc = any(
                int(_sz.get(f"{{{_W}}}val", "0")) > 36
                for _sz in _rc_proto.findall(f".//{{{_W}}}sz")
            )
            if _has_large_rc:
                _orig_text_rc = "".join(
                    t.text or "" for t in _rc_proto.findall(f".//{{{_W}}}t")
                )
                if _orig_text_rc.strip():
                    para_lookup[_rc_blk.para_id] = _rc_pm.with_text(_orig_text_rc)
                    _log.debug(
                        "RIGHT_COL_HEADING_RESTORED: para_id=%r hijacked=%r → original=%r",
                        _rc_blk.para_id, _rc_pm.text[:40], _orig_text_rc[:40],
                    )
        except Exception:
            pass

    # Right-column summary injection: for newspaper-column templates where
    # all left-column header slots are occupied (e.g. contact/education/skills),
    # the summary must be prepended at the TOP of the right cell before experience.
    _right_col_summary_text = getattr(doc, "_right_col_summary_text", None)
    if _right_col_summary_text and right_blocks:
        _first_right_blk = next(
            (b for b in right_blocks
             if isinstance(b, LayoutParagraphBlock) and b.xml_proto_xml),
            None,
        )
        if _first_right_blk:
            _sum_ref = etree.fromstring(_first_right_blk.xml_proto_xml)
            _sum_p = _make_inline_summary_para(_sum_ref, _right_col_summary_text)
            right_tc.append(_sum_p)
            _log.debug(
                "RIGHT_COL_SUMMARY_INJECTED: len=%d chars", len(_right_col_summary_text)
            )

    _unbound_extra = [pm for pm in (doc.all_paras or []) if not pm.para_id and pm.text.strip()]
    _fill_cell(
        right_tc, right_blocks, _right_col_x_emu,
        extra_paras=_unbound_extra if _unbound_extra else None,
        exempt_font_cap_ids=frozenset(_right_name_exempt) if _right_name_exempt else None,
    )

    if sectPr is not None:
        sectPr.addprevious(tbl)
    else:
        body.append(tbl)

    # Register so _split_oversized_table_rows skips this table — splitting it
    # creates visual gaps because the left cell drives the row height while the
    # right cell ends short (see _RENDERER_CREATED_TABLES docstring).
    _RENDERER_CREATED_TABLES.add(tbl)

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

    def _force(pm: ParaModel) -> None:
        """Override any existing entry — used for sec_summary_inserted which
        reuses an empty header_para slot and must take priority over it."""
        if pm.para_id:
            seen[pm.para_id] = pm

    for pm in doc.header_paras:
        _add(pm)
    for sec in doc.sections:
        _is_summary_inserted = getattr(sec, "section_id", "") == "sec_summary_inserted"
        _add_fn = _force if _is_summary_inserted else _add
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
            _add_fn(pm)
    # Fallback: all_paras may contain paragraphs not yet in semantic sections
    for pm in (doc.all_paras or []):
        if pm.para_id and pm.para_id not in seen:
            seen[pm.para_id] = pm
    return seen


def _build_synthetic_para(pm, pid: str):
    """Build a w:p element for a ParaModel that has no LayoutParagraphBlock.

    Used by the v2 timeline table to render right_para_ids that exist in
    para_lookup but were never added to layout_blocks (e.g. para_103/104/115/116
    in Sample 35 where the LLM assigned content to slots absent from the proto).
    """
    from copy import deepcopy
    from lxml import etree

    elem = None
    if pm.style is not None and pm.style.xml_proto is not None:
        elem = deepcopy(pm.style.xml_proto)
        _strip_last_rendered_page_breaks(elem)
        _strip_column_break(elem)
        _strip_text_wrapping_breaks(elem, pm.text)
        _set_para_text(elem, pm.text)
        _clear_sdt_placeholder(elem)
    elif pm.paragraph_profile is not None:
        from tailor.compiler.para_builder import build_para_element
        elem = build_para_element(pm)
    if elem is None:
        return None
    if pid and not elem.get(f"{{{_W14}}}paraId"):
        elem.set(f"{{{_W14}}}paraId", pid)
    return elem


def _build_timeline_row_table(
    doc: "ResumeDocument",
    para_lookup: dict,
    layout_blocks: list,
    main_pgSz_w: "str | None",
    main_pgSz_h: "str | None",
    main_is_multicolumn: bool,
    sectPr,
) -> "tuple[Any, frozenset[str]]":
    """Build a borderless two-column w:tbl for the timeline experience section.

    Each row corresponds to one experience role whose layout_binding has
    kind == "timeline_left_role_right".  Left cell receives the original
    date sidebar paragraph XML verbatim; right cell receives the updated
    role content rendered via _render_block_into_elem.

    Returns (tbl_element, consumed_pids) where consumed_pids is the frozenset
    of para_ids that were placed inside the table.  The caller's flat loop
    must skip any LayoutParagraphBlock whose para_id is in this set.
    """
    from copy import deepcopy
    from lxml import etree

    # ── Collect timeline roles in row_index order ─────────────────────────
    timeline_roles = []
    for sec in (doc.sections or []):
        for role in (sec.roles or []):
            lb = role.layout_binding
            if lb and lb.get("kind") == "timeline_left_role_right":
                timeline_roles.append(role)
    if not timeline_roles:
        return None, frozenset()
    timeline_roles.sort(key=lambda r: r.layout_binding["row_index"])

    # ── Build para_id → LayoutParagraphBlock lookup ───────────────────────
    lb_by_pid: dict[str, Any] = {}
    for blk in layout_blocks:
        if isinstance(blk, LayoutParagraphBlock) and blk.para_id:
            lb_by_pid[blk.para_id] = blk

    # ── Build anchor → [_ext_ blocks] in layout_blocks order ─────────────
    ext_by_anchor: dict[str, list] = {}
    for blk in layout_blocks:
        if not isinstance(blk, LayoutParagraphBlock) or not blk.para_id:
            continue
        if "_ext_" not in blk.para_id:
            continue
        # para_id format: "{anchor_pid}_ext_{n}"
        anchor = blk.para_id.split("_ext_")[0]
        ext_by_anchor.setdefault(anchor, []).append(blk)

    # ── Page geometry ─────────────────────────────────────────────────────
    _content_w = 8748  # A4 default (~16 cm)
    if sectPr is not None:
        _pgSz = sectPr.find(f"{{{_W}}}pgSz")
        _pgMar = sectPr.find(f"{{{_W}}}pgMar")
        if _pgSz is not None and _pgMar is not None:
            try:
                _pw = int(_pgSz.get(f"{{{_W}}}w") or 0)
                _ml = int(_pgMar.get(f"{{{_W}}}left") or 0)
                _mr = int(_pgMar.get(f"{{{_W}}}right") or 0)
                if _pw > 0:
                    _content_w = _pw - _ml - _mr
            except (ValueError, TypeError):
                pass

    _left_w = timeline_roles[0].layout_binding.get("column_width_twips", 1321)
    _right_w = max(_content_w - _left_w, 1000)

    # ── Build w:tbl element ───────────────────────────────────────────────
    tbl = etree.Element(f"{{{_W}}}tbl")

    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    _tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    _tblW.set(f"{{{_W}}}w", str(_content_w))
    _tblW.set(f"{{{_W}}}type", "dxa")
    _tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
    for _bn in ("top", "left", "bottom", "right", "insideH", "insideV"):
        _be = etree.SubElement(_tblBorders, f"{{{_W}}}{_bn}")
        _be.set(f"{{{_W}}}val", "none")
        _be.set(f"{{{_W}}}sz", "0")
        _be.set(f"{{{_W}}}space", "0")
        _be.set(f"{{{_W}}}color", "auto")
    _tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
    _tblLayout.set(f"{{{_W}}}type", "fixed")
    _tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
    for _ms in ("top", "left", "bottom", "right"):
        _me = etree.SubElement(_tblCellMar, f"{{{_W}}}{_ms}")
        _me.set(f"{{{_W}}}w", "0")
        _me.set(f"{{{_W}}}type", "dxa")

    tblGrid = etree.SubElement(tbl, f"{{{_W}}}tblGrid")
    _gcL = etree.SubElement(tblGrid, f"{{{_W}}}gridCol")
    _gcL.set(f"{{{_W}}}w", str(_left_w))
    _gcR = etree.SubElement(tblGrid, f"{{{_W}}}gridCol")
    _gcR.set(f"{{{_W}}}w", str(_right_w))

    # ── Build rows ────────────────────────────────────────────────────────
    n_roles = len(timeline_roles)
    for _ri, _role in enumerate(timeline_roles):
        _lb = _role.layout_binding
        _is_last = _ri == n_roles - 1

        tr = etree.SubElement(tbl, f"{{{_W}}}tr")
        trPr = etree.SubElement(tr, f"{{{_W}}}trPr")
        etree.SubElement(trPr, f"{{{_W}}}cantSplit")

        # Left cell: leading_spacers + date_paras [+ trailing_spacers if last row]
        tc_left = etree.SubElement(tr, f"{{{_W}}}tc")
        tcPr_left = etree.SubElement(tc_left, f"{{{_W}}}tcPr")
        _twL = etree.SubElement(tcPr_left, f"{{{_W}}}tcW")
        _twL.set(f"{{{_W}}}w", str(_left_w))
        _twL.set(f"{{{_W}}}type", "dxa")

        _left_pids: list[str] = (
            list(_lb.get("left_leading_spacer_ids") or [])
            + list(_lb.get("left_para_ids") or [])
        )
        if _is_last:
            _left_pids += list(_lb.get("left_trailing_spacer_ids") or [])

        for _pid in _left_pids:
            _blk = lb_by_pid.get(_pid)
            if _blk and _blk.xml_proto_xml:
                _el = etree.fromstring(_blk.xml_proto_xml)
                _strip_last_rendered_page_breaks(_el)
                _strip_non_column_section_break(_el, main_pgSz_w, main_pgSz_h, False)
                if _pid and not _el.get(f"{{{_W14}}}paraId"):
                    _el.set(f"{{{_W14}}}paraId", _pid)
                tc_left.append(_el)

        if not tc_left.findall(f"{{{_W}}}p"):
            tc_left.append(etree.Element(f"{{{_W}}}p"))

        # Right cell: role header/meta/bullets + _ext_ blocks
        tc_right = etree.SubElement(tr, f"{{{_W}}}tc")
        tcPr_right = etree.SubElement(tc_right, f"{{{_W}}}tcPr")
        _twR = etree.SubElement(tcPr_right, f"{{{_W}}}tcW")
        _twR.set(f"{{{_W}}}w", str(_right_w))
        _twR.set(f"{{{_W}}}type", "dxa")

        for _pid in (_lb.get("right_para_ids") or []):
            _blk = lb_by_pid.get(_pid)
            if _blk:
                _el = _render_block_into_elem(
                    _blk, para_lookup, main_pgSz_w, main_pgSz_h,
                    main_is_multicolumn=False,
                )
                if _el is not None:
                    tc_right.append(_el)
            # Also render any _ext_ blocks anchored to this right_para_id
            for _ext_blk in ext_by_anchor.get(_pid, []):
                _el = _render_block_into_elem(
                    _ext_blk, para_lookup, main_pgSz_w, main_pgSz_h,
                    main_is_multicolumn=False,
                )
                if _el is not None:
                    tc_right.append(_el)

        if not tc_right.findall(f"{{{_W}}}p"):
            tc_right.append(etree.Element(f"{{{_W}}}p"))

    # ── Compute consumed_pids ─────────────────────────────────────────────
    _consumed: set[str] = set()
    for _role in timeline_roles:
        _lb = _role.layout_binding
        _consumed.update(_lb.get("left_leading_spacer_ids") or [])
        _consumed.update(_lb.get("left_para_ids") or [])
        _consumed.update(_lb.get("left_trailing_spacer_ids") or [])
        for _rp in (_lb.get("right_para_ids") or []):
            _consumed.add(_rp)
            for _ext_blk in ext_by_anchor.get(_rp, []):
                if _ext_blk.para_id:
                    _consumed.add(_ext_blk.para_id)

    # Also consume any timeline-left pids not claimed by any role (e.g. extra
    # left-column date paras beyond the last experience role).
    _all_left_pids: frozenset[str] = frozenset()
    for _sec in (doc.sections or []):
        for _r in (_sec.roles or []):
            _lb2 = _r.layout_binding
            if _lb2 and _lb2.get("kind") == "timeline_left_role_right":
                _all_left_pids = _all_left_pids | frozenset(
                    _lb2.get("left_para_ids") or []
                ) | frozenset(
                    _lb2.get("left_leading_spacer_ids") or []
                ) | frozenset(
                    _lb2.get("left_trailing_spacer_ids") or []
                )
    _consumed.update(_all_left_pids)

    # Build a set of all header_para pids for range-based consumption below.
    _header_pid_set: frozenset[str] = frozenset(
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    )

    # Include col-break paragraphs, intermediate sectPr paragraphs, and any
    # header_para pids that fall between the first and last consumed block in
    # layout_blocks order.  This catches date-column paras (e.g. para_55-58)
    # that belong to non-experience sections but appear inside the two-column
    # layout region.
    for _blk in layout_blocks:
        if not isinstance(_blk, LayoutParagraphBlock) or not _blk.para_id:
            continue
        if _blk.xml_proto_xml and 'type="column"' in _blk.xml_proto_xml:
            _consumed.add(_blk.para_id)
    _consumed_idx = [
        i for i, _blk in enumerate(layout_blocks)
        if isinstance(_blk, LayoutParagraphBlock) and _blk.para_id in _consumed
    ]
    if _consumed_idx:
        _min_i, _max_i = min(_consumed_idx), max(_consumed_idx)
        for i, _blk in enumerate(layout_blocks):
            if _min_i <= i <= _max_i:
                if not isinstance(_blk, LayoutParagraphBlock) or not _blk.para_id:
                    continue
                if not _blk.xml_proto_xml:
                    continue
                if "sectPr" in _blk.xml_proto_xml:
                    _consumed.add(_blk.para_id)
                elif _blk.para_id in _header_pid_set:
                    # Left-column sidebar paras in the layout region: consume
                    # even if not bound to an experience role (e.g. education
                    # dates that appear in the same two-column section region).
                    _consumed.add(_blk.para_id)

    _log.debug(
        "TIMELINE_ROW_TABLE: %d roles → %d rows, %d para_ids consumed",
        n_roles, n_roles, len(_consumed),
    )
    return tbl, frozenset(_consumed)


def _build_timeline_segments(
    doc,
    para_lookup: dict,
    layout_blocks: list,
    main_pgSz_w,
    main_pgSz_h,
    sectPr,
):
    """Build two borderless w:tbl elements for Sample-35-style two-section timelines.

    Segment 1 contains roles whose right_para_ids all appear BEFORE the sectPr
    boundary paragraph in layout_blocks order.  Segment 2 contains roles after it.

    Returns (seg1_tbl, seg2_tbl, consumed_pids, diag_dict).

    consumed_pids includes ONLY explicit binding IDs (left_leading_spacer_ids,
    left_para_ids, left_trailing_spacer_ids, right_para_ids, _ext_ para_ids).
    No range sweeping.  Structural paragraphs para_59 and para_120 are NOT consumed.
    """
    from lxml import etree

    # Collect timeline roles in row_index order
    timeline_roles = []
    for sec in (doc.sections or []):
        for role in (sec.roles or []):
            lb = role.layout_binding
            if lb and lb.get("kind") == "timeline_left_role_right":
                timeline_roles.append(role)
    if not timeline_roles:
        return None, None, frozenset(), {}
    timeline_roles.sort(key=lambda r: r.layout_binding["row_index"])

    # Build para_id → LayoutParagraphBlock and para_id → index lookups
    lb_by_pid: dict = {}
    pid_to_idx: dict = {}
    for i, blk in enumerate(layout_blocks):
        if isinstance(blk, LayoutParagraphBlock) and blk.para_id:
            lb_by_pid[blk.para_id] = blk
            pid_to_idx[blk.para_id] = i

    # Build anchor → [_ext_ blocks] in layout_blocks order
    ext_by_anchor: dict = {}
    for blk in layout_blocks:
        if not isinstance(blk, LayoutParagraphBlock) or not blk.para_id:
            continue
        if "_ext_" not in blk.para_id:
            continue
        anchor = blk.para_id.split("_ext_")[0]
        ext_by_anchor.setdefault(anchor, []).append(blk)

    # Locate sectPr boundary that cleanly splits role groups:
    # some roles have ALL right_para_ids before it, others have ALL after it.
    def _idx_of(pid):
        return pid_to_idx.get(pid, -1)

    right_max_idx: dict = {}  # row_index → max layout_blocks idx of any right_para_id
    right_min_idx: dict = {}  # row_index → min layout_blocks idx
    for r in timeline_roles:
        lb = r.layout_binding
        ri = lb["row_index"]
        idxs = [_idx_of(p) for p in (lb.get("right_para_ids") or []) if _idx_of(p) >= 0]
        if idxs:
            right_max_idx[ri] = max(idxs)
            right_min_idx[ri] = min(idxs)

    sectpr_pid = None
    sectpr_idx = -1
    for i, blk in enumerate(layout_blocks):
        if not isinstance(blk, LayoutParagraphBlock) or not blk.para_id:
            continue
        if not blk.xml_proto_xml or "sectPr" not in blk.xml_proto_xml:
            continue
        roles_before = [ri for ri, mx in right_max_idx.items() if mx < i]
        roles_after = [ri for ri, mn in right_min_idx.items() if mn > i]
        if roles_before and roles_after:
            sectpr_pid = blk.para_id
            sectpr_idx = i
            break

    if sectpr_pid is None:
        _log.debug("TIMELINE_SEGMENTS_V2: no sectPr boundary found; skipping")
        return None, None, frozenset(), {}

    # Split roles into two segments
    seg1_roles = [r for r in timeline_roles
                  if right_max_idx.get(r.layout_binding["row_index"], -1) < sectpr_idx]
    seg2_roles = [r for r in timeline_roles
                  if right_min_idx.get(r.layout_binding["row_index"], 999999) > sectpr_idx]

    # Page geometry from sectPr
    _content_w = 8748
    if sectPr is not None:
        _pgSz = sectPr.find(f"{{{_W}}}pgSz")
        _pgMar = sectPr.find(f"{{{_W}}}pgMar")
        if _pgSz is not None and _pgMar is not None:
            try:
                _pw = int(_pgSz.get(f"{{{_W}}}w") or 0)
                _ml = int(_pgMar.get(f"{{{_W}}}left") or 0)
                _mr = int(_pgMar.get(f"{{{_W}}}right") or 0)
                if _pw > 0:
                    _content_w = _pw - _ml - _mr
            except (ValueError, TypeError):
                pass
    _left_w = timeline_roles[0].layout_binding.get("column_width_twips", 1321)
    _right_w = max(_content_w - _left_w, 1000)

    consumed: set = set()
    synthetic_pids: list = []
    skipped_pids: list = []

    def _make_tbl_shell():
        tbl = etree.Element(f"{{{_W}}}tbl")
        tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
        _tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
        _tblW.set(f"{{{_W}}}w", str(_content_w))
        _tblW.set(f"{{{_W}}}type", "dxa")
        _tblBorders = etree.SubElement(tblPr, f"{{{_W}}}tblBorders")
        for _bn in ("top", "left", "bottom", "right", "insideH", "insideV"):
            _be = etree.SubElement(_tblBorders, f"{{{_W}}}{_bn}")
            _be.set(f"{{{_W}}}val", "none")
            _be.set(f"{{{_W}}}sz", "0")
            _be.set(f"{{{_W}}}space", "0")
            _be.set(f"{{{_W}}}color", "auto")
        _tblLayout = etree.SubElement(tblPr, f"{{{_W}}}tblLayout")
        _tblLayout.set(f"{{{_W}}}type", "fixed")
        _tblCellMar = etree.SubElement(tblPr, f"{{{_W}}}tblCellMar")
        for _ms in ("top", "left", "bottom", "right"):
            _me = etree.SubElement(_tblCellMar, f"{{{_W}}}{_ms}")
            _me.set(f"{{{_W}}}w", "0")
            _me.set(f"{{{_W}}}type", "dxa")
        tblGrid = etree.SubElement(tbl, f"{{{_W}}}tblGrid")
        _gcL = etree.SubElement(tblGrid, f"{{{_W}}}gridCol")
        _gcL.set(f"{{{_W}}}w", str(_left_w))
        _gcR = etree.SubElement(tblGrid, f"{{{_W}}}gridCol")
        _gcR.set(f"{{{_W}}}w", str(_right_w))
        return tbl

    def _build_seg(roles_for_seg):
        tbl = _make_tbl_shell()
        n = len(roles_for_seg)
        for _ri, _role in enumerate(roles_for_seg):
            _lb = _role.layout_binding
            _is_last = _ri == n - 1

            tr = etree.SubElement(tbl, f"{{{_W}}}tr")

            # Left cell: date_paras only.
            # Leading and trailing spacers are consumed (to prevent flat-loop emission)
            # but NOT rendered inside the cell: in the original two-column layout they
            # vertically positioned the date opposite right-column content. In a table
            # the row alignment handles positioning, so spacers only create blank gaps.
            tc_left = etree.SubElement(tr, f"{{{_W}}}tc")
            tcPr_left = etree.SubElement(tc_left, f"{{{_W}}}tcPr")
            _twL = etree.SubElement(tcPr_left, f"{{{_W}}}tcW")
            _twL.set(f"{{{_W}}}w", str(_left_w))
            _twL.set(f"{{{_W}}}type", "dxa")

            _left_pids = list(_lb.get("left_para_ids") or [])

            for _pid in _left_pids:
                _blk = lb_by_pid.get(_pid)
                if _blk and _blk.xml_proto_xml:
                    _el = etree.fromstring(_blk.xml_proto_xml)
                    _strip_last_rendered_page_breaks(_el)
                    _strip_non_column_section_break(_el, main_pgSz_w, main_pgSz_h, False)
                    if not _el.get(f"{{{_W14}}}paraId"):
                        _el.set(f"{{{_W14}}}paraId", _pid)
                    tc_left.append(_el)
                    consumed.add(_pid)

            if not tc_left.findall(f"{{{_W}}}p"):
                tc_left.append(etree.Element(f"{{{_W}}}p"))

            # Right cell: right_para_ids + _ext_ blocks
            tc_right = etree.SubElement(tr, f"{{{_W}}}tc")
            tcPr_right = etree.SubElement(tc_right, f"{{{_W}}}tcPr")
            _twR = etree.SubElement(tcPr_right, f"{{{_W}}}tcW")
            _twR.set(f"{{{_W}}}w", str(_right_w))
            _twR.set(f"{{{_W}}}type", "dxa")

            for _pid in (_lb.get("right_para_ids") or []):
                _blk = lb_by_pid.get(_pid)
                if _blk:
                    _el = _render_block_into_elem(
                        _blk, para_lookup, main_pgSz_w, main_pgSz_h,
                        main_is_multicolumn=False,
                    )
                    if _el is not None:
                        tc_right.append(_el)
                        consumed.add(_pid)
                else:
                    # Synthetic: pid in right_para_ids but absent from layout_blocks
                    pm = para_lookup.get(_pid)
                    if pm is not None:
                        _syn = _build_synthetic_para(pm, _pid)
                        if _syn is not None:
                            tc_right.append(_syn)
                            consumed.add(_pid)
                            synthetic_pids.append(_pid)
                            _log.debug(
                                "TIMELINE_SYNTHETIC_PARA: para_id=%r text=%r",
                                _pid, (pm.text or "")[:60],
                            )
                        else:
                            skipped_pids.append(_pid)
                            _log.warning("TIMELINE_SYNTHETIC_PARA_FAILED: para_id=%r", _pid)
                    else:
                        skipped_pids.append(_pid)
                        _log.warning("TIMELINE_PARA_NOT_FOUND: para_id=%r skipped", _pid)

                for _ext_blk in ext_by_anchor.get(_pid, []):
                    _el = _render_block_into_elem(
                        _ext_blk, para_lookup, main_pgSz_w, main_pgSz_h,
                        main_is_multicolumn=False,
                    )
                    if _el is not None:
                        tc_right.append(_el)
                        if _ext_blk.para_id:
                            consumed.add(_ext_blk.para_id)

            if not tc_right.findall(f"{{{_W}}}p"):
                tc_right.append(etree.Element(f"{{{_W}}}p"))

        return tbl

    seg1_tbl = _build_seg(seg1_roles) if seg1_roles else None
    seg2_tbl = _build_seg(seg2_roles) if seg2_roles else None

    # Consume ALL intermediate sectPr paragraphs that carry a w:cols definition.
    # In v2 mode the two-column newspaper layout is replaced by explicit tables, so
    # every intermediate multi-column sectPr must be suppressed.  Emitting them
    # forces the section containing the tables into a two-column context in
    # LibreOffice, which confines the table to one column's width.
    # Note: the para_8-style single-column sectPr (no w:cols) is NOT consumed —
    # only multi-column (w:cols) sectPrs are targeted here.
    _multi_col_sectpr_pids: set[str] = set()
    for _blk in layout_blocks:
        _blk_pid = getattr(_blk, "para_id", None)
        _blk_xml = getattr(_blk, "xml_proto_xml", None)
        if not _blk_pid or not _blk_xml:
            continue
        try:
            _blk_el = etree.fromstring(_blk_xml)
        except Exception:
            continue
        _blk_sp = _blk_el.find(f".//{{{_W}}}sectPr")
        if _blk_sp is not None and _blk_sp.find(f"{{{_W}}}cols") is not None:
            _multi_col_sectpr_pids.add(_blk_pid)
    consumed.update(_multi_col_sectpr_pids)
    if _multi_col_sectpr_pids:
        _log.debug(
            "TIMELINE_V2_CONSUME_SECTPRS: %d multi-col sectPr paras consumed: %s",
            len(_multi_col_sectpr_pids), sorted(_multi_col_sectpr_pids),
        )

    # Also consume ALL left spacer pids from every role to prevent stray flat-loop emission
    for r in timeline_roles:
        lb = r.layout_binding
        consumed.update(lb.get("left_leading_spacer_ids") or [])
        consumed.update(lb.get("left_trailing_spacer_ids") or [])

    # Consume header_para blocks within the consumed-index range.
    # These are sidebar date paras for non-Experience content (e.g. para_55-58 in
    # S35 which carry Education section dates in the same left column).  They are not
    # in any role binding but appear inside the two-column newspaper region and must
    # be suppressed to prevent orphan body-paragraph emission after the tables.
    _hp_ids: frozenset = frozenset(
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    )
    if _hp_ids:
        _ci = sorted(pid_to_idx[p] for p in consumed if p in pid_to_idx)
        if _ci:
            _ci_min, _ci_max = _ci[0], _ci[-1]
            for _blk in layout_blocks[_ci_min:_ci_max + 1]:
                if (isinstance(_blk, LayoutParagraphBlock)
                        and _blk.para_id
                        and _blk.para_id in _hp_ids):
                    consumed.add(_blk.para_id)

    # Find seg1 col-break heading: first non-consumed block with type="column" before
    # the first seg1 right_para_id (this is para_59, the Experience section heading).
    seg1_heading_pid = None
    if seg1_roles:
        _seg1_right_all = set()
        for r in seg1_roles:
            _seg1_right_all.update(r.layout_binding.get("right_para_ids") or [])
        _seg1_right_min = min((pid_to_idx[p] for p in _seg1_right_all if p in pid_to_idx), default=999999)
        for blk in layout_blocks[:_seg1_right_min]:
            if (isinstance(blk, LayoutParagraphBlock)
                    and blk.para_id
                    and blk.para_id not in consumed
                    and blk.xml_proto_xml
                    and 'type="column"' in blk.xml_proto_xml):
                seg1_heading_pid = blk.para_id
                break

    # Find seg2 trigger: first consumed pid of seg2 that appears after sectpr_idx
    seg2_trigger_pid = None
    for blk in layout_blocks[sectpr_idx + 1:]:
        if isinstance(blk, LayoutParagraphBlock) and blk.para_id and blk.para_id in consumed:
            seg2_trigger_pid = blk.para_id
            break

    diag = {
        "sectpr_pid": sectpr_pid,
        "seg1_heading_pid": seg1_heading_pid,
        "seg2_trigger_pid": seg2_trigger_pid,
        "seg1_roles": [r.layout_binding["row_index"] for r in seg1_roles],
        "seg2_roles": [r.layout_binding["row_index"] for r in seg2_roles],
        "consumed_count": len(consumed),
        "synthetic_pids": synthetic_pids,
        "skipped_pids": skipped_pids,
    }
    _log.debug(
        "TIMELINE_SEGMENTS_V2: seg1=%d roles, seg2=%d roles, "
        "consumed=%d pids, synthetic=%s, skipped=%s, "
        "heading=%r, sectpr=%r, seg2_trigger=%r",
        len(seg1_roles), len(seg2_roles), len(consumed),
        synthetic_pids, skipped_pids,
        seg1_heading_pid, sectpr_pid, seg2_trigger_pid,
    )
    return seg1_tbl, seg2_tbl, frozenset(consumed), diag


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

    # Some templates define the 2-column area via an intermediate sectPr (w:pPr/w:sectPr
    # with w:num≥2) rather than the main body sectPr.  Example: sample 17, where the
    # main sectPr is 1-column but the intermediate sectPr at body[96] declares
    # w:num="2".  Detect this pattern: if layout_blocks contain a single col-break AND
    # an intermediate 2-col sectPr, set _main_is_multicolumn=True and inject the 2-col
    # widths into the main sectPr so _render_layout_two_col_table reads correct values.
    if not _main_is_multicolumn and doc.layout_blocks:
        _lb_colbreak_probe = _find_single_col_break_idx(doc.layout_blocks)  # type: ignore[arg-type]
        if _lb_colbreak_probe is not None:
            from copy import deepcopy as _dc_mc
            _all_lb = list(doc.layout_blocks)  # type: ignore[union-attr]
            for _ilb_idx, _ilb in enumerate(_all_lb):
                if not (isinstance(_ilb, LayoutParagraphBlock) and _ilb.xml_proto_xml
                        and "sectPr" in _ilb.xml_proto_xml):
                    continue
                try:
                    _ilb_el = etree.fromstring(_ilb.xml_proto_xml)
                    _ilb_pPr = _ilb_el.find(f"{{{_W}}}pPr")
                    _ilb_sp = _ilb_pPr.find(f"{{{_W}}}sectPr") if _ilb_pPr is not None else None
                    _ilb_cols = _ilb_sp.find(f"{{{_W}}}cols") if _ilb_sp is not None else None
                    if _ilb_cols is None:
                        continue
                    _ilb_num = _ilb_cols.get(f"{{{_W}}}num")
                    if _ilb_num is None or int(_ilb_num) < 2:
                        continue
                    if len(_ilb_cols.findall(f"{{{_W}}}col")) < 2:
                        continue
                    # Guard: if a 1-col sectPr follows this 2-col sectPr, the 2-col
                    # area is only a sub-section (e.g. sample 26 Education Summary).
                    # In that case the overall layout is mixed-column — do NOT activate
                    # the whole-document 2-col table; let native sectPr flow through.
                    _has_subsequent_1col = False
                    for _slb in _all_lb[_ilb_idx + 1:]:
                        if not (isinstance(_slb, LayoutParagraphBlock) and _slb.xml_proto_xml
                                and "sectPr" in _slb.xml_proto_xml):
                            continue
                        try:
                            _slb_el = etree.fromstring(_slb.xml_proto_xml)
                            _slb_pPr = _slb_el.find(f"{{{_W}}}pPr")
                            _slb_sp = _slb_pPr.find(f"{{{_W}}}sectPr") if _slb_pPr is not None else None
                            _slb_cols = _slb_sp.find(f"{{{_W}}}cols") if _slb_sp is not None else None
                            if _slb_cols is None:
                                _has_subsequent_1col = True
                                break
                            _slb_num = _slb_cols.get(f"{{{_W}}}num", "1")
                            if int(_slb_num) < 2:
                                _has_subsequent_1col = True
                                break
                        except Exception:
                            pass
                    if _has_subsequent_1col:
                        break  # sub-section 2-col; skip whole-doc 2-col table
                    # Found intermediate 2-col sectPr — patch main sectPr and set flag
                    _main_is_multicolumn = True
                    if sectPr is not None:
                        _mc_existing = sectPr.find(f"{{{_W}}}cols")
                        if _mc_existing is not None:
                            sectPr.remove(_mc_existing)
                        sectPr.append(_dc_mc(_ilb_cols))
                    break
                except Exception:
                    pass

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
            # _has_blip guard: skip table conversion when the RIGHT column contains a
            # full-page raster behindDoc image in a header block.  Only RIGHT-side
            # blips cause blank middle pages — when the background paragraph is
            # extracted to body level before the table it creates a full-page element
            # that pushes the table to page 2.  LEFT-side blips (e.g. sample 3 where
            # the background is in the left column) stay inside the left cell and are
            # handled correctly as absolute-positioned drawings without forcing row height.
            _right_blocks = (
                list(doc.layout_blocks)[_col_break_idx + 1:]  # type: ignore[index]
                if _col_break_idx is not None else []
            )
            _has_blip = _header_paras_have_blip_bg(doc, _right_blocks)  # type: ignore[arg-type]
            _contact_ref_names: frozenset[str] = frozenset({
                "contact info", "contact information", "personal references",
                "personal reference", "references",
            })
            _left_para_ids: set[str] = {
                lb.para_id
                for lb in doc.layout_blocks[:_col_break_idx]  # type: ignore[index]
                if isinstance(lb, LayoutParagraphBlock) and lb.para_id
            }
            _needs_table_for_contact = any(
                s.heading and s.heading.para_id in _left_para_ids
                and s.title.strip().lower() in _contact_ref_names
                for s in doc.sections
            )
            if _col_ratio <= 0.80 and (not _has_blip or _needs_table_for_contact):
                # Section-header-left layout (e.g. sample 20): the narrow left
                # column contains ONLY section headings; all body content is in
                # the wide right column.  Use a multi-row table (one row per
                # section) so each heading stays vertically aligned with its
                # corresponding body content regardless of content size changes.
                _left_blocks_for_detect = list(doc.layout_blocks[:_col_break_idx])  # type: ignore[index]
                if _is_docx_section_header_left_layout(doc, _left_blocks_for_detect):
                    _render_docx_section_row_table(
                        doc, body, sectPr,
                        _col_break_idx,
                        _main_pgSz_w, _main_pgSz_h,
                        _main_is_multicolumn,
                    )
                    return

                _render_layout_two_col_table(
                    doc, body, sectPr,
                    _col_break_idx,
                    _main_pgSz_w, _main_pgSz_h,
                    _main_is_multicolumn,
                )
                # Promote page-relative behind-doc drawings from inside table cells
                # to body level so LibreOffice renders them against the page origin
                # instead of the cell origin.  This restores the dark background in
                # templates like sample 3 where a full-page "Group 1" wpg:wgp shape
                # provides the header background.
                _promote_cell_bg_drawings_to_body(body, sectPr)
                # For templates with a blip background extracted to body level
                # (e.g. template 23), the background is a HEADER element: it
                # should only appear on page 1 and must NOT be cloned to page 2.
                # Use default allow_blip=False so the blip is left in place at
                # body start without being duplicated for overflow pages.
                _find_and_move_bg_to_start(body, sectPr)
                return
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

    # Header paragraph ids: name/title paragraphs that should NOT have their
    # fonts capped even when they carry large run-level font sizes.
    _header_para_ids: frozenset[str] = frozenset(
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    )

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
    _spacer_followup_remaining: int = 0  # minimize empty paras following an oversized spacer
    _consecutive_empty_count: int = 0  # consecutive empty para run; minimize from 2nd onward
    _pending_bg_drawings: list = []  # extracted full-height behindDoc drawings awaiting emit
    # Header spacers (name/title/contact area) must not be compressed — they provide
    # the vertical gap between the header section and the first body section.
    _header_para_ids_rfb: frozenset[str] = frozenset(
        pm.para_id for pm in (doc.header_paras or []) if pm.para_id
    )

    # Timeline-binding verbatim set: left-column date sidebar paragraphs and
    # spacers that must NEVER have _set_para_text called on them.  Built from
    # role.layout_binding where kind == "timeline_left_role_right".  Empty
    # frozenset for all templates that do not carry a layout_binding — zero cost.
    _timeline_left_pids: frozenset[str] = frozenset()
    for _tlb_sec in doc.sections:
        for _tlb_role in (_tlb_sec.roles or []):
            _tlb = _tlb_role.layout_binding
            if _tlb and _tlb.get("kind") == "timeline_left_role_right":
                _timeline_left_pids = _timeline_left_pids | frozenset(
                    _tlb.get("left_para_ids") or []
                ) | frozenset(
                    _tlb.get("left_leading_spacer_ids") or []
                ) | frozenset(
                    _tlb.get("left_trailing_spacer_ids") or []
                )
    if _timeline_left_pids:
        _log.debug(
            "TIMELINE_LEFT_VERBATIM_SET: %d para_ids will be emitted verbatim",
            len(_timeline_left_pids),
        )

    # Timeline v2: two-segment table reconstruction (feature-flagged).
    _tl_v2_active = bool(_timeline_left_pids and _TIMELINE_ROW_TABLE_V2_ENABLED)
    _tl_seg1_tbl = None
    _tl_seg2_tbl = None
    _tl_consumed: frozenset = frozenset()
    _tl_seg1_heading_pid: "str | None" = None
    _tl_seg2_trigger_pid: "str | None" = None
    _tl_sectpr_pid: "str | None" = None
    _tl_seg1_emitted = False
    _tl_seg2_emitted = False
    if _tl_v2_active:
        _tl_seg1_tbl, _tl_seg2_tbl, _tl_consumed, _tl_diag = _build_timeline_segments(
            doc, para_lookup, list(doc.layout_blocks),  # type: ignore[arg-type]
            _main_pgSz_w, _main_pgSz_h, sectPr,
        )
        _tl_seg1_heading_pid = _tl_diag.get("seg1_heading_pid")
        _tl_seg2_trigger_pid = _tl_diag.get("seg2_trigger_pid")
        _tl_sectpr_pid = _tl_diag.get("sectpr_pid")
        _log.debug(
            "TIMELINE_V2_READY: consumed=%d, seg1_heading=%r, seg2_trigger=%r, sectpr=%r",
            len(_tl_consumed), _tl_seg1_heading_pid, _tl_seg2_trigger_pid, _tl_sectpr_pid,
        )

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
                    # Stamp w14:paraId so the grader can re-identify paragraphs
                    # in templates that have no native w14:paraId attributes.
                    if not p_elem.get(f"{{{_W14}}}paraId"):
                        p_elem.set(f"{{{_W14}}}paraId", para_id)
                pm = para_lookup.get(para_id)
                if pm is not None:
                    _strip_text_wrapping_breaks(p_elem, pm.text)
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
            # Inline summary injection: insert the summary paragraph for table
            # templates that have no dedicated summary slot.
            #
            # Strategy: when the anchor para is in the header row (row 0) and
            # there is a body row below it, injecting inside the table (either
            # header row or body row) inflates the row height and pushes page-1
            # content to page 2.  Skip the injection for those templates to
            # preserve the two-row layout on page 1.
            #
            # Fallback: anchor not in header row → insert after the anchor para
            # inside the table (legacy behaviour for non-header anchors).
            if _inline_pid and _inline_text and _inline_pid in pid_to_pelem:
                _ref_p = pid_to_pelem[_inline_pid]
                # Inject the summary paragraph immediately after the anchor (typically
                # the role/title line in the header cell, e.g. "registered nurse").
                # The header row's trHeight is removed below so the row auto-sizes.
                _new_p = _make_inline_summary_para(_ref_p, _inline_text)
                _ref_p.addnext(_new_p)
                _log.debug(
                    "INLINE_SUMMARY_INJECTED: new para after para_id=%r",
                    getattr(doc, "_inline_summary_pid", "?"),
                )
                _inline_pid = None  # consume once
            # Remove explicit trHeight on oversized rows and enable row splitting.
            # Original template heights were sized for short placeholder text.
            # After LLM injection content grows and locked trHeight values can
            # push the body row to page 2.  Also, LibreOffice defaults to
            # NOT splitting table rows (unlike Word), so rows that are taller
            # than the remaining page space go entirely to page 2 rather than
            # splitting.  Explicitly setting cantSplit='0' overrides this.
            # Small header rows (e.g. photo+name row) keep their trHeight so
            # the row stays fixed-height and the photo does not shift downward.
            for _tr_h_elem in tbl_elem.findall(f"{{{_W}}}tr"):
                _trPr_h = _tr_h_elem.find(f"{{{_W}}}trPr")
                if _trPr_h is None:
                    _trPr_h = etree.SubElement(_tr_h_elem, f"{{{_W}}}trPr")
                    _tr_h_elem.insert(0, _trPr_h)
                # Only remove trHeight for rows that will be split (oversized).
                # Header rows with a fixed trHeight (e.g. photo cell) must keep
                # it so the photo stays aligned within the original cell height.
                _row_max_p = max(
                    (len(_tc.findall(f"{{{_W}}}p")) for _tc in _tr_h_elem.findall(f"{{{_W}}}tc")),
                    default=0,
                )
                if _row_max_p > _OVERSIZED_ROW_PARA_THRESHOLD:
                    for _trH in list(_trPr_h.findall(f"{{{_W}}}trHeight")):
                        _trPr_h.remove(_trH)
                # Explicitly allow row splitting across pages
                _csplit = _trPr_h.find(f"{{{_W}}}cantSplit")
                if _csplit is None:
                    _csplit = etree.SubElement(_trPr_h, f"{{{_W}}}cantSplit")
                _csplit.set(f"{{{_W}}}val", "0")
            # Extract full-height behindDoc anchors from table cells to
            # body-level paragraphs placed BEFORE the table.  A behindDoc
            # anchor with cy ≥ 10 M EMU (≈ 11 in) inside a table cell
            # forces LibreOffice to size the containing row to match the
            # drawing height, filling page 1 and pushing body rows to page 2
            # (e.g. sample 11 blue sidebar background at cy=11in).
            # Extracting the <w:drawing> element (keeping the paragraph in
            # the cell) and placing it in a zero-height body paragraph
            # preserves the background appearance while fixing row height.
            _extracted_bg_drawings: list = []
            for _bg_drawing in list(tbl_elem.findall(f".//{{{_W}}}drawing")):
                _bg_anchor = _bg_drawing.find(f".//{{{_WP}}}anchor")
                if _bg_anchor is None or _bg_anchor.get("behindDoc") != "1":
                    continue
                _bg_ext = _bg_anchor.find(f"{{{_WP}}}extent")
                if _bg_ext is None:
                    continue
                try:
                    _bg_cy = int(_bg_ext.get("cy", "0"))
                except ValueError:
                    continue
                if _bg_cy >= 10_000_000:
                    from copy import deepcopy as _dc_bg
                    _extracted_bg_drawings.append(_dc_bg(_bg_drawing))
                    _bg_parent = _bg_drawing.getparent()
                    if _bg_parent is not None:
                        _bg_parent.remove(_bg_drawing)
                    _log.debug(
                        "TABLE_BLOCK_BG_EXTRACTED: cy=%d (%.1fin) from tbl",
                        _bg_cy, _bg_cy / 914400,
                    )
            # Apply layoutInCell='0' to remaining anchors for safety.
            for _tc in tbl_elem.findall(f".//{{{_W}}}tc"):
                _fix_anchor_layout_in_cell(_tc)
            # Once we hit a table block, stop compressing spacers (table started).
            _compress_remaining = 0
            _consecutive_empty_count = 0
            elem: Any = tbl_elem
            # Schedule background drawings to be inserted before the table.
            # They are emitted in the elem-insertion block below.
            _pending_bg_drawings = _extracted_bg_drawings

        else:
            # LayoutParagraphBlock
            # Timeline v2: skip consumed blocks; emit tables at trigger points.
            if _tl_v2_active and _tl_consumed and block.para_id in _tl_consumed:
                if (not _tl_seg2_emitted
                        and _tl_seg2_tbl is not None
                        and block.para_id == _tl_seg2_trigger_pid):
                    _tl_seg2_emitted = True
                    _RENDERER_CREATED_TABLES.add(_tl_seg2_tbl)
                    if sectPr is not None:
                        sectPr.addprevious(_tl_seg2_tbl)
                    else:
                        body.append(_tl_seg2_tbl)
                    _log.debug("TIMELINE_SEG2_EMITTED at trigger para_id=%r", block.para_id)
                continue
            if not block.xml_proto_xml:
                # No XML prototype: fall back to para_builder or runtime xml_proto
                pm = para_lookup.get(block.para_id) if block.para_id else None
                if pm is None:
                    continue
                if pm.style.xml_proto is not None:
                    from copy import deepcopy
                    elem = deepcopy(pm.style.xml_proto)
                    _strip_last_rendered_page_breaks(elem)
                    _strip_text_wrapping_breaks(elem, pm.text)
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
                if block.para_id and not elem.get(f"{{{_W14}}}paraId"):
                    elem.set(f"{{{_W14}}}paraId", block.para_id)
                pass  # (keepNext injection removed — was causing extra pages)
            else:
                elem = etree.fromstring(block.xml_proto_xml)
                _strip_last_rendered_page_breaks(elem)
                # Stamp w14:paraId so grader can re-identify paragraphs in templates
                # that have no native w14:paraId (e.g. DOCX-origin templates).
                if block.para_id and not elem.get(f"{{{_W14}}}paraId"):
                    elem.set(f"{{{_W14}}}paraId", block.para_id)
                # Strip single-column sectPr (page-size-only section breaks) to prevent
                # stale section boundaries from creating forced page breaks.  Multi-column
                # sectPr (w:cols w:num≥2) are preserved for newspaper-style column layouts.
                _strip_non_column_section_break(
                    elem, _main_pgSz_w, _main_pgSz_h, _main_is_multicolumn
                )
                # Strip col-break from the seg1 Experience section heading so it
                # flows as a plain paragraph above the table (not a column jump).
                if _tl_v2_active and block.para_id == _tl_seg1_heading_pid:
                    _strip_column_break(elem)
                pm = para_lookup.get(block.para_id) if block.para_id else None
                # Timeline-binding guard: left-column date sidebar and spacer
                # paragraphs are emitted verbatim — _set_para_text must not
                # rewrite them even when para_lookup has an entry.
                # preserve_left_verbatim=True in layout_binding is the contract.
                if _timeline_left_pids and block.para_id in _timeline_left_pids:
                    _log.debug(
                        "TIMELINE_LEFT_VERBATIM: para_id=%r", block.para_id
                    )
                elif pm is not None:
                    # Strip SDT children when the block's model text differs from
                    # what the template SDTs hold (same logic as _render_block_into_elem).
                    # _set_para_text early-exits on ≥2 SDTs to preserve multi-column
                    # contact rows; that guard incorrectly also fires for skills rows
                    # when the LLM replaced their content.
                    _sdt_cnt_lb = sum(1 for _c in elem if _c.tag == f"{{{_W}}}sdt")
                    if _sdt_cnt_lb >= 2:
                        _is_ext_lb = "_ext_" in (block.para_id or "")
                        _should_strip_lb = _is_ext_lb
                        if not _is_ext_lb and pm.text.strip():
                            _xml_parts_lb: list[str] = []
                            for _child_lb in elem:
                                if _child_lb.tag == f"{{{_W}}}sdt":
                                    _sc_lb = _child_lb.find(f"{{{_W}}}sdtContent")
                                    if _sc_lb is not None:
                                        for _r_lb in _sc_lb.findall(f".//{{{_W}}}r"):
                                            for _t_lb in _r_lb.findall(f"{{{_W}}}t"):
                                                if _t_lb.text:
                                                    _xml_parts_lb.append(_t_lb.text)
                            _xml_text_lb = " ".join(_xml_parts_lb)
                            _norm_lb = lambda s: " ".join(s.lower().split())
                            _should_strip_lb = _norm_lb(pm.text) != _norm_lb(_xml_text_lb)
                        if _should_strip_lb:
                            for _sdt_c_lb in list(elem):
                                if _sdt_c_lb.tag != f"{{{_W}}}pPr":
                                    elem.remove(_sdt_c_lb)
                    _render_text_lb = pm.text
                    if pm.semantic == "role_meta" and "\n" in pm.text:
                        _render_text_lb = pm.text.split("\n")[0]
                    # Capture proto state before modification for bold-strip detection.
                    _proto_text_raw_lb = "".join(
                        _t.text or "" for _t in elem.iter(f"{{{_W}}}t")
                    )
                    _pPr_proto_lb = elem.find(f"{{{_W}}}pPr")
                    _proto_para_bold_lb = (
                        _pPr_proto_lb is not None
                        and _pPr_proto_lb.find(f"{{{_W}}}rPr/{{{_W}}}b") is not None
                    )
                    _strip_text_wrapping_breaks(elem, _render_text_lb)
                    _set_para_text(elem, _render_text_lb)
                    _clear_sdt_placeholder(elem)
                    # Reuse the expanded font-cap logic from _render_block_into_elem.
                    # The check covers both _ext_ blocks and any block where the XML
                    # proto carries large run-level fonts but pm.semantic is body content.
                    # Header paragraphs (name/title) are exempt: they are allowed to keep
                    # their original large font to preserve the resume branding.
                    _HEADING_SEMANTICS_LB = frozenset({
                        "section_heading", "role_header",
                        "role_intro", "role_key_technologies", "role_tech_stack",
                        "role_project_label",
                    })
                    _is_header_para_lb = (
                        block.para_id is not None
                        and block.para_id in _header_para_ids
                    )
                    _needs_cap_lb = (
                        bool(block.para_id and "_ext_" in block.para_id)
                        and pm.semantic not in _HEADING_SEMANTICS_LB
                        and not _is_header_para_lb
                    )
                    _cap_lb: int = 36
                    if (not _needs_cap_lb
                            and pm.semantic not in _HEADING_SEMANTICS_LB
                            and not _is_header_para_lb):
                        for _rPr_lb in elem.iter(f"{{{_W}}}rPr"):
                            _parent_lb = _rPr_lb.getparent()
                            if _parent_lb is not None and _parent_lb.tag == f"{{{_W}}}r":
                                _sz_lb = _rPr_lb.find(f"{{{_W}}}sz")
                                if _sz_lb is not None:
                                    try:
                                        if int(_sz_lb.get(f"{{{_W}}}val", "0")) > 36:
                                            _needs_cap_lb = True
                                            break
                                    except ValueError:
                                        pass
                        if _needs_cap_lb:
                            _pPr_lb = elem.find(f"{{{_W}}}pPr")
                            if _pPr_lb is not None:
                                _rPr_lb2 = _pPr_lb.find(f"{{{_W}}}rPr")
                                if _rPr_lb2 is not None:
                                    _sz_lb2 = _rPr_lb2.find(f"{{{_W}}}sz")
                                    if _sz_lb2 is not None:
                                        try:
                                            _ppr_sz_lb = int(_sz_lb2.get(f"{{{_W}}}val", "0"))
                                            if 0 < _ppr_sz_lb < _cap_lb:
                                                _cap_lb = _ppr_sz_lb
                                        except ValueError:
                                            pass
                    if _needs_cap_lb:
                        _cap_para_font_size(elem, max_halfpts=_cap_lb)
                    # Injected bullet blocks cloned from a Heading-N role-header
                    # inherit the heading paragraph style (e.g. blue bold).  When
                    # the semantic is "bullet" or plain "paragraph", normalise the
                    # pStyle to "Normal" so the injected content renders as regular
                    # body text rather than a styled heading.
                    if _needs_cap_lb and pm.semantic in ("bullet", "paragraph"):
                        _pPr_norm = elem.find(f"{{{_W}}}pPr")
                        if _pPr_norm is not None:
                            _pStyle_norm = _pPr_norm.find(f"{{{_W}}}pStyle")
                            if _pStyle_norm is not None:
                                _sval = _pStyle_norm.get(f"{{{_W}}}val", "").lower()
                                if _sval.startswith("heading"):
                                    _pStyle_norm.set(f"{{{_W}}}val", "Normal")
                        # Strip explicit run-level bold/italic/color inherited from
                        # the anchor proto (e.g. para_21 run0 is bold "Mercor").
                        for _rPr_ext in elem.iter(f"{{{_W}}}rPr"):
                            _r_ext = _rPr_ext.getparent()
                            if _r_ext is not None and _r_ext.tag == f"{{{_W}}}r":
                                for _ftag_ext in (
                                    f"{{{_W}}}b", f"{{{_W}}}bCs",
                                    f"{{{_W}}}i", f"{{{_W}}}iCs",
                                    f"{{{_W}}}color",
                                ):
                                    _fel_ext = _rPr_ext.find(_ftag_ext)
                                    if _fel_ext is not None:
                                        _rPr_ext.remove(_fel_ext)
                    # Strip display-only fonts (same as in _render_block_into_elem).
                    _DISPLAY_FONTS_LB = frozenset({
                        "symbol", "wingdings", "wingdings2", "wingdings3",
                        "symbolmt", "webdings", "marlett",
                    })
                    if pm.text.strip():
                        for _rPr_sym_lb in elem.iter(f"{{{_W}}}rPr"):
                            _rFonts_sym_lb = _rPr_sym_lb.find(f"{{{_W}}}rFonts")
                            if _rFonts_sym_lb is not None:
                                _fn_lb = {
                                    _rFonts_sym_lb.get(a, "").lower()
                                    for a in (
                                        f"{{{_W}}}ascii", f"{{{_W}}}hAnsi",
                                        f"{{{_W}}}cs", f"{{{_W}}}eastAsia",
                                    )
                                    if _rFonts_sym_lb.get(a)
                                }
                                if _fn_lb and _fn_lb.issubset(_DISPLAY_FONTS_LB):
                                    _rPr_sym_lb.remove(_rFonts_sym_lb)
                    # Strip bold from rewritten non-heading paras whose XML proto
                    # is ALL BOLD (paragraph-level pPr/rPr/b set).  Template project
                    # headers (e.g. "OTA project") are all-bold; when the LLM assigns
                    # achievement text to such a slot the bold must not carry over.
                    if (
                        pm.text.strip()
                        and pm.semantic not in _HEADING_SEMANTICS_LB
                        and _proto_para_bold_lb
                        and _proto_text_raw_lb.strip() != pm.text.strip()
                    ):
                        for _rPr_ab_lb in elem.iter(f"{{{_W}}}rPr"):
                            _par_ab_lb = _rPr_ab_lb.getparent()
                            if _par_ab_lb is not None and _par_ab_lb.tag in (
                                f"{{{_W}}}r", f"{{{_W}}}pPr"
                            ):
                                for _btag_ab_lb in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                                    _bel_ab_lb = _rPr_ab_lb.find(_btag_ab_lb)
                                    if _bel_ab_lb is not None:
                                        _rPr_ab_lb.remove(_bel_ab_lb)
                    # Strip bullet/list formatting (w:numPr) for semantic types
                    # that represent prose or technology-block text, not list items.
                    _NO_BULLET_SEMANTICS_LB = frozenset({
                        "role_intro", "role_key_technologies",
                        "role_tech_stack", "role_project_label",
                    })
                    if pm.semantic in _NO_BULLET_SEMANTICS_LB and pm.text.strip():
                        _pPr_nb_lb = elem.find(f"{{{_W}}}pPr")
                        if _pPr_nb_lb is not None:
                            _numPr_nb_lb = _pPr_nb_lb.find(f"{{{_W}}}numPr")
                            if _numPr_nb_lb is not None:
                                _pPr_nb_lb.remove(_numPr_nb_lb)
                    # Semantic visual styling (layout-bound path): italic for
                    # narrative intro types, bold for technology/label types.
                    _SEM_ITALIC_LB = frozenset({"role_intro", "project_intro"})
                    _SEM_BOLD_LB = frozenset({
                        "role_key_technologies", "role_tech_stack",
                        "role_project_label", "highlight_header",
                    })
                    if pm.semantic in _SEM_ITALIC_LB and pm.text.strip():
                        _pPr_si_lb = elem.find(f"{{{_W}}}pPr")
                        if _pPr_si_lb is not None:
                            _rPr_ppr_si_lb = _pPr_si_lb.find(f"{{{_W}}}rPr")
                            if _rPr_ppr_si_lb is None:
                                _rPr_ppr_si_lb = etree.SubElement(_pPr_si_lb, f"{{{_W}}}rPr")
                            for _t_si_lb in (f"{{{_W}}}i", f"{{{_W}}}iCs"):
                                if _rPr_ppr_si_lb.find(_t_si_lb) is None:
                                    etree.SubElement(_rPr_ppr_si_lb, _t_si_lb)
                        for _r_si_lb in elem.findall(f".//{{{_W}}}r"):
                            _rPr_si_lb = _r_si_lb.find(f"{{{_W}}}rPr")
                            if _rPr_si_lb is None:
                                _rPr_si_lb = etree.SubElement(_r_si_lb, f"{{{_W}}}rPr")
                                _r_si_lb.insert(0, _rPr_si_lb)
                            for _t_si_lb in (f"{{{_W}}}i", f"{{{_W}}}iCs"):
                                if _rPr_si_lb.find(_t_si_lb) is None:
                                    etree.SubElement(_rPr_si_lb, _t_si_lb)
                    elif pm.semantic in _SEM_BOLD_LB and pm.text.strip():
                        _pPr_sb_lb = elem.find(f"{{{_W}}}pPr")
                        if _pPr_sb_lb is not None:
                            _rPr_ppr_sb_lb = _pPr_sb_lb.find(f"{{{_W}}}rPr")
                            if _rPr_ppr_sb_lb is None:
                                _rPr_ppr_sb_lb = etree.SubElement(_pPr_sb_lb, f"{{{_W}}}rPr")
                            for _t_sb_lb in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                                if _rPr_ppr_sb_lb.find(_t_sb_lb) is None:
                                    etree.SubElement(_rPr_ppr_sb_lb, _t_sb_lb)
                        for _r_sb_lb in elem.findall(f".//{{{_W}}}r"):
                            _rPr_sb_lb = _r_sb_lb.find(f"{{{_W}}}rPr")
                            if _rPr_sb_lb is None:
                                _rPr_sb_lb = etree.SubElement(_r_sb_lb, f"{{{_W}}}rPr")
                                _r_sb_lb.insert(0, _rPr_sb_lb)
                            for _t_sb_lb in (f"{{{_W}}}b", f"{{{_W}}}bCs"):
                                if _rPr_sb_lb.find(_t_sb_lb) is None:
                                    etree.SubElement(_rPr_sb_lb, _t_sb_lb)
                    _log.debug("PARAGRAPH_BLOCK_XML_PATCHED: para_id=%r", block.para_id)
                else:
                    if block.para_id:
                        _log.debug("LAYOUT_BLOCK_MISSING_PARA_ID: para_id=%r", block.para_id)
                    # Structural/orphan paragraph — insert verbatim (original text kept)
                _has_xml_text_ce = any(t.text for t in elem.iter(f"{{{_W}}}t"))
                _pm_text_ce = (pm.text.strip() if pm else "")
                _is_empty_ce = not _pm_text_ce and not _has_xml_text_ce
                _did_collapse = _collapse_oversized_spacer(elem)
                if _did_collapse:
                    # After collapsing a large spacer, also minimize subsequent
                    # consecutive empty paragraphs (e.g. para_104-109 after para_102
                    # in template 17) that together create the same blank gap.
                    _spacer_followup_remaining = 20
                    _consecutive_empty_count = 1
                elif _spacer_followup_remaining > 0:
                    if _is_empty_ce:
                        _minimize_empty_para(elem)
                        _spacer_followup_remaining -= 1
                        _consecutive_empty_count += 1
                        _log.debug("SPACER_FOLLOWUP_MINIMIZED: para_id=%r", block.para_id)
                    else:
                        _spacer_followup_remaining = 0  # hit real content, stop
                        _consecutive_empty_count = 0
                elif _is_empty_ce:
                    # Consecutive-empty compression: the template places several
                    # empty spacer paragraphs between roles (line=200-400 twips
                    # each).  Only the first is needed for visual separation;
                    # minimize all subsequent ones to prevent a large blank gap.
                    # Skip header_para blocks: those spacers sit between the
                    # document header section and the first body section and must
                    # be preserved (sample 28: gap between ENGINEERING and GENERAL INFO).
                    if block.para_id and block.para_id in _header_para_ids_rfb:
                        _consecutive_empty_count = 0
                    else:
                        _consecutive_empty_count += 1
                        if _consecutive_empty_count >= 2:
                            _minimize_empty_para(elem)
                            _log.debug(
                                "CONSECUTIVE_EMPTY_MINIMIZED: para_id=%r count=%d",
                                block.para_id, _consecutive_empty_count,
                            )
                else:
                    _consecutive_empty_count = 0


            # Post-summary spacer compression: once the summary body anchor para
            # has been rendered, compress the spacing of subsequent empty paras.
            # This prevents a cluster of empty spacers from pushing the resume table
            # to page 2 (samples 13/14).  Compression stops at the first non-empty
            # para or when the counter exhausts.
            if block.para_id == _summary_body_pid:
                _compress_remaining = 8  # compress up to 8 following empty paras
            elif _compress_remaining > 0:
                _para_text = (pm.text.strip() if pm else "").strip()
                _is_header_spacer = bool(
                    block.para_id and block.para_id in _header_para_ids_rfb
                )
                if not _para_text and not _is_header_spacer:
                    _zero_para_spacing(elem)
                    _compress_remaining -= 1
                    _log.debug(
                        "SUMMARY_SPACER_COMPRESSED: para_id=%r spacing zeroed",
                        block.para_id,
                    )
                elif _para_text:
                    _compress_remaining = 0  # non-empty para: stop compressing

        # Emit extracted background drawings as zero-height body paragraphs
        # immediately before their table so they render at page coordinates
        # (layoutInCell='0') without forcing table row heights.
        if _pending_bg_drawings:
            for _bg_draw in _pending_bg_drawings:
                _bg_p = etree.Element(f"{{{_W}}}p")
                _bg_pPr = etree.SubElement(_bg_p, f"{{{_W}}}pPr")
                _bg_sp = etree.SubElement(_bg_pPr, f"{{{_W}}}spacing")
                _bg_sp.set(f"{{{_W}}}before", "0")
                _bg_sp.set(f"{{{_W}}}after", "0")
                _bg_sp.set(f"{{{_W}}}line", "1")
                _bg_sp.set(f"{{{_W}}}lineRule", "exact")
                _bg_r = etree.SubElement(_bg_p, f"{{{_W}}}r")
                _bg_r.append(_bg_draw)
                if sectPr is not None:
                    sectPr.addprevious(_bg_p)
                else:
                    body.append(_bg_p)
            _pending_bg_drawings = []

        if sectPr is not None:
            sectPr.addprevious(elem)
        else:
            body.append(elem)

        # Timeline v2: after emitting the seg1 heading para, append seg1 table.
        if (_tl_v2_active
                and not _tl_seg1_emitted
                and _tl_seg1_tbl is not None
                and block.para_id == _tl_seg1_heading_pid):
            _tl_seg1_emitted = True
            _RENDERER_CREATED_TABLES.add(_tl_seg1_tbl)
            if sectPr is not None:
                sectPr.addprevious(_tl_seg1_tbl)
            else:
                body.append(_tl_seg1_tbl)
            _log.debug("TIMELINE_SEG1_EMITTED after heading para_id=%r", block.para_id)

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


def _maybe_clone_solidfill_bg_para(body, sectPr) -> None:
    """Clone a solid-fill sidebar background paragraph for page 2 continuation.

    Some templates (e.g. sample 11) use a behindDoc solidFill rectangle in the
    FIRST body paragraph (before the main table) to paint a left-sidebar
    background.  That paragraph only appears on page 1.  When content overflows
    to page 2, the sidebar has no background.

    Fix: deep-clone that paragraph and insert it just before sectPr so that it
    lands on page 2 when content overflows.  On pages where no overflow occurs,
    the clone and the original overlap with the same fill — visually identical.

    Detects: a direct child <w:p> of <w:body> (not inside a table) that has a
    <wp:anchor behindDoc="1"> with cy >= 10 M EMU and a solidFill (no blip).
    """
    from copy import deepcopy as _deepcopy
    from lxml import etree as _et

    _WP_NS = _WP
    _DML_NS = _DML

    for child in list(body):
        if child.tag != f"{{{_W}}}p":
            continue
        for anchor in child.findall(f".//{{{_WP_NS}}}anchor"):
            if anchor.get("behindDoc") != "1":
                continue
            ext = anchor.find(f"{{{_WP_NS}}}extent")
            if ext is None:
                continue
            try:
                cy_val = int(ext.get("cy", "0"))
            except ValueError:
                continue
            if cy_val < 10_000_000:
                continue
            # Must be solidFill with no blip — blip-based backgrounds are
            # handled separately by _maybe_insert_bg_rect.
            has_blip = anchor.find(f".//{{{_DML_NS}}}blip") is not None
            has_solid = anchor.find(f".//{{{_DML_NS}}}solidFill") is not None
            if has_blip or not has_solid:
                continue
            # Found a qualifying solid-fill sidebar background paragraph.
            clone_p = _deepcopy(child)
            # Make the cloned anchor page-relative so it appears from the TOP
            # of whatever page it lands on (not relative to its paragraph, which
            # would position it mid-page on page 3 content).
            for clone_anchor in clone_p.findall(f".//{{{_WP_NS}}}anchor"):
                clone_anchor.set("layoutInCell", "0")
                _pos_v = clone_anchor.find(f"{{{_WP_NS}}}positionV")
                if _pos_v is not None:
                    _pos_v.set("relativeFrom", "page")
                    _pos_off = _pos_v.find(f"{{{_WP_NS}}}posOffset")
                    if _pos_off is not None:
                        _pos_off.text = "0"
                    else:
                        _et.SubElement(_pos_v, f"{{{_WP_NS}}}posOffset").text = "0"
            if sectPr is not None:
                sectPr.addprevious(clone_p)
            else:
                body.append(clone_p)
            _log.debug(
                "SIDEBAR_BG_CLONE: cloned solid-fill bg para cy=%d for page 2", cy_val
            )
            return  # only one clone needed


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
# Post-processing: split oversized single-row tables
# ---------------------------------------------------------------------------

_OVERSIZED_ROW_PARA_THRESHOLD = 14  # trigger row split when any cell exceeds this

# Tables created by our renderer (from column-flow → 2-cell table conversion) are
# balanced layouts that fit the page naturally.  They must NOT be split by
# _split_oversized_table_rows — splitting creates a visual gap between content rows
# because the left cell tends to drive the row height, leaving the right cell short.
# Store element *references* (not id() integers) — id() values are unreliable across
# function-call boundaries because the lxml proxy can be GC'd after the creating
# function returns, letting a new proxy for the same C element get a different id().
# A strong reference in this set keeps the proxy alive so lxml returns the same
# object on subsequent findall() calls.  Cleared at the start of each render_docx call.
_RENDERER_CREATED_TABLES: set[Any] = set()


# Opt-in flag for the v2 two-segment timeline table renderer.
# Off by default; enabled via _set_timeline_row_table_v2(True) in tests
# or by setting the TIMELINE_ROW_TABLE_V2=1 environment variable.
_TIMELINE_ROW_TABLE_V2_ENABLED: bool = False


def _set_timeline_row_table_v2(enabled: bool = True) -> None:
    global _TIMELINE_ROW_TABLE_V2_ENABLED
    _TIMELINE_ROW_TABLE_V2_ENABLED = enabled


def _trim_trailing_cell_paras(body: Any) -> None:
    """Remove excess trailing empty paragraphs from table cells.

    Template cells often carry multiple trailing empty paragraphs as spacing
    artefacts.  Extra trailing empties inflate the row's rendered height and
    create visible gaps in the opposite (shorter) cell.  We keep exactly one
    mandatory terminal paragraph per cell (required by OOXML) and strip the
    rest so LibreOffice can size each row to its actual content.
    """
    for tbl_elem in body.findall(f"{{{_W}}}tbl"):
        for row in tbl_elem.findall(f"{{{_W}}}tr"):
            for cell in row.findall(f"{{{_W}}}tc"):
                paras = cell.findall(f"{{{_W}}}p")
                if len(paras) <= 1:
                    continue
                trailing: list[Any] = []
                for p in reversed(paras):
                    # Use recursive search so runs nested inside w:hyperlink,
                    # w:sdt, etc. are found.  Injected paragraphs clone the
                    # anchor element structure where w:r may not be a direct
                    # child of w:p — direct search would incorrectly classify
                    # those paragraphs as empty and strip them.
                    runs = p.findall(f".//{{{_W}}}r")
                    text = "".join(
                        (t.text or "")
                        for r in runs
                        for t in r.findall(f"{{{_W}}}t")
                    )
                    if not text.strip():
                        trailing.append(p)
                    else:
                        break
                # Keep trailing[0] (the mandatory OOXML terminal paragraph);
                # remove the extra empty paragraphs before it.
                for p in trailing[1:]:
                    cell.remove(p)


def _minimize_trailing_body_para(body: Any) -> None:
    """Remove the empty trailing body paragraph to prevent blank overflow pages.

    When injected content fills the page so that the mandatory trailing
    paragraph overflows onto page 2, LibreOffice renders a completely blank
    second page.  When the sectPr is already a direct body child (the common
    case for layout_blocks-rendered DOCX), the trailing empty paragraph carries
    no section properties and can be removed entirely.
    """
    # Only act when sectPr is already a direct body child — the trailing
    # paragraph is then truly redundant.
    has_direct_sectPr = body.find(f"{{{_W}}}sectPr") is not None
    if not has_direct_sectPr:
        return

    direct_children = [c for c in body if c.tag in (f"{{{_W}}}p", f"{{{_W}}}tbl")]
    if not direct_children:
        return
    last = direct_children[-1]
    if last.tag != f"{{{_W}}}p":
        return

    # Guard: paragraph must not carry an embedded sectPr (section boundary).
    pPr = last.find(f"{{{_W}}}pPr")
    if pPr is not None and pPr.find(f"{{{_W}}}sectPr") is not None:
        return

    # Guard: paragraph must be empty.
    runs = last.findall(f".//{{{_W}}}r")
    text = "".join(
        (t.text or "") for r in runs for t in r.findall(f"{{{_W}}}t")
    )
    if text.strip():
        return

    body.remove(last)


def _split_oversized_table_rows(body: Any, sectPr: Any) -> None:  # noqa: ARG001
    """Split table rows whose cells contain too many paragraphs.

    LibreOffice cannot split a table row across pages even with cantSplit='0'.
    This pass scans every row in every table in the body.  Rows whose tallest
    cell exceeds _OVERSIZED_ROW_PARA_THRESHOLD paragraphs are physically split
    at Heading2-style paragraph boundaries (or the midpoint when none are found),
    allowing LibreOffice to paginate the content naturally across pages.

    keepNext/keepLines are suppressed on all paragraphs in the new rows so that
    Heading-style properties do not chain rows together and force the whole table
    to the next page.
    """
    from copy import deepcopy
    from lxml import etree as _et

    for tbl_elem in list(body.findall(f"{{{_W}}}tbl")):
        # Skip tables created by _render_layout_two_col_table — those are balanced
        # 2-cell layouts that fit the page; splitting them creates row-height gaps.
        if tbl_elem in _RENDERER_CREATED_TABLES:
            continue
        # Snapshot: newly inserted rows are NOT re-processed in the same pass.
        for row in list(tbl_elem.findall(f"{{{_W}}}tr")):
            cells = row.findall(f"{{{_W}}}tc")
            if not cells:
                continue

            cell_paras = [tc.findall(f"{{{_W}}}p") for tc in cells]
            max_paras = max(len(ps) for ps in cell_paras)
            if max_paras <= _OVERSIZED_ROW_PARA_THRESHOLD:
                continue

            # If any cell contains a nested table, the nested table determines row
            # height dynamically.  Splitting here creates a height mismatch: the
            # para-heavy cell is sliced at the threshold while the sibling cell's
            # nested table is much taller, leaving a large void in the split row.
            if any(tc.find(f"{{{_W}}}tbl") is not None for tc in cells):
                continue

            tallest_idx = max(range(len(cells)), key=lambda i: len(cell_paras[i]))

            # Independent column flow: if a non-tallest cell has ≥2 Heading2/1
            # sections it has its own independent section structure (sidebar) rather
            # than being paired content for the tallest cell.  Splitting at heading
            # boundaries synchronises row heights and creates large voids: a short
            # sidebar section is paired with a tall content section, leaving empty
            # space in the sidebar row while the matching content section grows.
            # Instead: strip trHeight and allow LibreOffice to split at page breaks.
            _independent_flow = False
            for _ci, _ps in enumerate(cell_paras):
                if _ci == tallest_idx:
                    continue
                _h2 = 0
                for _p in _ps:
                    _pPr = _p.find(f"{{{_W}}}pPr")
                    if _pPr is not None:
                        _pSt = _pPr.find(f"{{{_W}}}pStyle")
                        if _pSt is not None:
                            _sv = _pSt.get(f"{{{_W}}}val", "").lower().replace(" ", "")
                            if _sv in ("heading2", "heading1"):
                                _h2 += 1
                if _h2 >= 2:
                    _independent_flow = True
                    break
            # If the sidebar (non-tallest) cell itself exceeds the oversized
            # threshold, vMerge makes things worse: all sidebar paras land in
            # sub-row 0, which then overflows and drags low-area-ratio content
            # (e.g. narrow 2-col skill blocks) onto page 2 alongside the body.
            # Use proportional standard split instead so sidebar sections are
            # paired with matching body content and contribute area on the same page.
            if _independent_flow:
                _non_tallest_max = max(
                    len(ps) for ci, ps in enumerate(cell_paras) if ci != tallest_idx
                )
                if _non_tallest_max > _OVERSIZED_ROW_PARA_THRESHOLD:
                    _independent_flow = False
            if _independent_flow:
                # Independent sidebar: split ONLY the tallest (right) column at
                # Heading2 boundaries.  Left sidebar content stays entirely in
                # sub-row 0; continuation sub-rows use vMerge so the left column
                # appears as one unified sidebar while the right column paginates.
                _if_paras = cell_paras[tallest_idx]
                _if_n = len(_if_paras)
                _if_split: list[int] = []
                for _ii, _ip in enumerate(_if_paras):
                    if _ii == 0:
                        continue
                    _ipPr = _ip.find(f"{{{_W}}}pPr")
                    if _ipPr is not None:
                        _ipSt = _ipPr.find(f"{{{_W}}}pStyle")
                        if _ipSt is not None:
                            _isv = _ipSt.get(f"{{{_W}}}val", "").lower().replace(" ", "")
                            if _isv in ("heading2", "heading1"):
                                _if_split.append(_ii)
                if not _if_split:
                    _if_split = [_if_n // 2]
                _if_bounds = [0] + _if_split + [_if_n]
                _if_slices = [
                    (_if_bounds[i], _if_bounds[i + 1])
                    for i in range(len(_if_bounds) - 1)
                    if _if_bounds[i] < _if_bounds[i + 1]
                ]
                if len(_if_slices) <= 1:
                    # Nothing meaningful to split; just allow splitting.
                    _if_trPr = row.find(f"{{{_W}}}trPr")
                    if _if_trPr is None:
                        _if_trPr = _et.SubElement(row, f"{{{_W}}}trPr")
                        row.insert(0, _if_trPr)
                    for _trH in list(_if_trPr.findall(f"{{{_W}}}trHeight")):
                        _if_trPr.remove(_trH)
                    _if_cs = _if_trPr.find(f"{{{_W}}}cantSplit")
                    if _if_cs is None:
                        _if_cs = _et.SubElement(_if_trPr, f"{{{_W}}}cantSplit")
                    _if_cs.set(f"{{{_W}}}val", "0")
                    continue
                _if_new_rows: list[Any] = []
                for _if_sn, (_if_ss, _if_se) in enumerate(_if_slices):
                    _if_nr = deepcopy(row)
                    _if_ncs = _if_nr.findall(f"{{{_W}}}tc")
                    # Remove trHeight; allow splitting
                    _if_nr_trPr = _if_nr.find(f"{{{_W}}}trPr")
                    if _if_nr_trPr is None:
                        _if_nr_trPr = _et.SubElement(_if_nr, f"{{{_W}}}trPr")
                        _if_nr.insert(0, _if_nr_trPr)
                    for _trH in list(_if_nr_trPr.findall(f"{{{_W}}}trHeight")):
                        _if_nr_trPr.remove(_trH)
                    _if_nr_cs = _if_nr_trPr.find(f"{{{_W}}}cantSplit")
                    if _if_nr_cs is None:
                        _if_nr_cs = _et.SubElement(_if_nr_trPr, f"{{{_W}}}cantSplit")
                    _if_nr_cs.set(f"{{{_W}}}val", "0")
                    for _if_ci, _if_ntc in enumerate(_if_ncs):
                        _if_ntc_ps = list(_if_ntc.findall(f"{{{_W}}}p"))
                        _if_orig_ps = cell_paras[_if_ci]
                        if _if_ci == tallest_idx:
                            # Right column: keep only the current slice.
                            _if_para_slice = _if_orig_ps[_if_ss:_if_se]
                            if not _if_para_slice and _if_orig_ps:
                                _if_para_slice = [_if_orig_ps[-1]]
                            _if_keep = {id(p) for p in _if_para_slice}
                            _if_kidxs = {
                                i for i, op in enumerate(_if_orig_ps)
                                if id(op) in _if_keep
                            }
                            for _if_idx, _if_np in enumerate(_if_ntc_ps):
                                if _if_idx not in _if_kidxs:
                                    _if_ntc.remove(_if_np)
                        else:
                            # Left sidebar cell
                            _if_tc_pr = _if_ntc.find(f"{{{_W}}}tcPr")
                            if _if_tc_pr is None:
                                _if_tc_pr = _et.SubElement(_if_ntc, f"{{{_W}}}tcPr")
                                _if_ntc.insert(0, _if_tc_pr)
                            for _vm in list(_if_tc_pr.findall(f"{{{_W}}}vMerge")):
                                _if_tc_pr.remove(_vm)
                            if _if_sn == 0:
                                # First sub-row: full sidebar content + vMerge restart
                                _if_vm = _et.SubElement(_if_tc_pr, f"{{{_W}}}vMerge")
                                _if_vm.set(f"{{{_W}}}val", "restart")
                            else:
                                # Continuation: empty cell with vMerge continue
                                for _rp in list(_if_ntc.findall(f"{{{_W}}}p")):
                                    _if_ntc.remove(_rp)
                                for _rt in list(_if_ntc.findall(f"{{{_W}}}tbl")):
                                    _if_ntc.remove(_rt)
                                _et.SubElement(_if_ntc, f"{{{_W}}}p")
                                _et.SubElement(_if_tc_pr, f"{{{_W}}}vMerge")
                        # Suppress keepNext/keepLines on all cell paras
                        for _if_cp in _if_ntc.findall(f"{{{_W}}}p"):
                            _if_cpPr = _if_cp.find(f"{{{_W}}}pPr")
                            if _if_cpPr is None:
                                _if_cpPr = _et.SubElement(_if_cp, f"{{{_W}}}pPr")
                                _if_cp.insert(0, _if_cpPr)
                            for _pt in (f"{{{_W}}}keepNext", f"{{{_W}}}keepLines"):
                                _pp = _if_cpPr.find(_pt)
                                if _pp is None:
                                    _pp = _et.SubElement(_if_cpPr, _pt)
                                _pp.set(f"{{{_W}}}val", "0")
                    _if_new_rows.append(_if_nr)
                _if_row_idx = list(tbl_elem).index(row)
                tbl_elem.remove(row)
                for _if_i, _if_nr in enumerate(_if_new_rows):
                    tbl_elem.insert(_if_row_idx + _if_i, _if_nr)
                continue
            tallest_paras = cell_paras[tallest_idx]
            n = len(tallest_paras)

            # Split only at Heading2 boundaries (major sections), not Heading3.
            # Too many tiny rows cause keepNext chains that force the table to
            # the next page; fewer, larger rows allow natural pagination.
            split_indices: list[int] = []
            for i, p in enumerate(tallest_paras):
                if i == 0:
                    continue
                pPr = p.find(f"{{{_W}}}pPr")
                if pPr is not None:
                    pStyle = pPr.find(f"{{{_W}}}pStyle")
                    if pStyle is not None:
                        sv = pStyle.get(f"{{{_W}}}val", "").lower().replace(" ", "")
                        if sv in ("heading2", "heading1"):
                            split_indices.append(i)

            if not split_indices:
                split_indices = [n // 2]

            # Drop the last split point when it would create a very short final
            # sub-row (< threshold paras in the tallest cell).  Short trailing
            # sub-rows produce content-sparse continuation pages because most of
            # the section content is already on the previous page.  Merging the
            # short tail into the preceding sub-row gives page 2 more content and
            # a higher area_ratio.  Only drop when multiple splits exist so we
            # always keep at least one split point.
            if len(split_indices) > 1 and (n - split_indices[-1]) < _OVERSIZED_ROW_PARA_THRESHOLD:
                split_indices = split_indices[:-1]
            boundaries = [0] + split_indices + [n]
            tallest_slices = [
                (boundaries[i], boundaries[i + 1])
                for i in range(len(boundaries) - 1)
                if boundaries[i] < boundaries[i + 1]
            ]
            if len(tallest_slices) <= 1:
                continue

            _log.debug(
                "SPLIT_OVERSIZED_ROW: max_paras=%d → %d rows at indices %r",
                max_paras, len(tallest_slices), split_indices,
            )

            # Pre-compute Heading2 boundaries for non-tallest cells.
            # When the boundary count matches the slice count, use semantic
            # heading-based split instead of proportional so section headings
            # are never separated from their content across rows.
            cell_h2_boundaries: list[list[int]] = []
            for ci_h2, ps_h2 in enumerate(cell_paras):
                if ci_h2 == tallest_idx:
                    cell_h2_boundaries.append([])
                    continue
                h2_idx: list[int] = []
                for i, p in enumerate(ps_h2):
                    if i == 0:
                        continue
                    pPr = p.find(f"{{{_W}}}pPr")
                    if pPr is not None:
                        pStyle = pPr.find(f"{{{_W}}}pStyle")
                        if pStyle is not None:
                            sv = pStyle.get(f"{{{_W}}}val", "").lower().replace(" ", "")
                            if sv in ("heading2", "heading1"):
                                h2_idx.append(i)
                cell_h2_boundaries.append(h2_idx)

            new_rows: list[Any] = []
            # Track the adjusted slice end per non-tallest cell so the next
            # slice starts where the previous one actually ended (not where the
            # proportional formula says it should start).  This prevents content
            # loss when the anti-orphan adjustment moves c_end backward.
            _cell_prop_prev_end: dict[int, int] = {}
            for slice_num, (slice_start, slice_end) in enumerate(tallest_slices):
                new_row = deepcopy(row)
                new_cells_elem = new_row.findall(f"{{{_W}}}tc")

                # Remove trHeight so LibreOffice sizes each new row naturally
                trPr = new_row.find(f"{{{_W}}}trPr")
                if trPr is not None:
                    for trH in list(trPr.findall(f"{{{_W}}}trHeight")):
                        trPr.remove(trH)

                for ci, new_tc in enumerate(new_cells_elem):
                    # Snapshot the deepcopy cell's direct-child paragraphs BEFORE
                    # removing anything; their order in new_tc mirrors orig_ps order.
                    new_tc_ps = list(new_tc.findall(f"{{{_W}}}p"))

                    # Non-first split rows: remove subtables (nested w:tbl elements).
                    # deepcopy copies all content including nested tables; keeping
                    # them in every split row duplicates header/contact sub-tables
                    # (e.g. sample 19 LICENSE NO. appearing twice).  Only the first
                    # split row keeps them since they belong at the top of the cell.
                    if slice_num > 0:
                        for _sub_tbl in list(new_tc.findall(f"{{{_W}}}tbl")):
                            new_tc.remove(_sub_tbl)

                    orig_ps = cell_paras[ci]
                    c_n = len(orig_ps)
                    # Detect drawing-only separator cells (no text in any para).
                    # These are typically vertical dividers sized to the full original
                    # row height (e.g. sample 14 center divider line at 5 in).
                    # Cloning them into every split row forces each row to the drawing
                    # height, producing one page per split row.  Strip the drawings so
                    # each split row sizes to its actual content.
                    _cell_has_text = any(
                        any(t.text for t in p.findall(f".//{{{_W}}}t"))
                        for p in orig_ps
                    )

                    if ci == tallest_idx:
                        para_slice = orig_ps[slice_start:slice_end]
                    elif c_n == 0:
                        para_slice = []
                    else:
                        h2_idx = cell_h2_boundaries[ci]
                        if len(h2_idx) == len(tallest_slices) - 1:
                            # Cell has the same number of sections as tallest —
                            # use its own Heading2 boundaries for a semantic split.
                            cell_bounds = [0] + h2_idx + [c_n]
                            c_start = cell_bounds[slice_num]
                            c_end = cell_bounds[slice_num + 1]
                        else:
                            # Fall back to proportional split.
                            # Use tracked previous slice end (if any) as c_start so
                            # anti-orphan adjustments don't create coverage gaps.
                            c_start = _cell_prop_prev_end.get(ci, round(slice_start * c_n / n))
                            c_end = round(slice_end * c_n / n)
                            c_start = min(c_start, c_n - 1)
                            c_end = max(c_end, c_start + 1)
                            c_end = min(c_end, c_n)
                            # Anti-orphan heading: when the proportional boundary
                            # falls right after a section heading (bold+text para
                            # preceded by an empty para), adjust c_end backward so
                            # the heading stays in the NEXT row with its body.
                            # Example: sample 18 REFERENCES heading orphaned from
                            # Philippe Stolvan by the midpoint split.
                            if slice_num < len(tallest_slices) - 1:
                                for _back in range(1, min(4, c_end - c_start)):
                                    _oi = c_end - _back
                                    if _oi <= c_start:
                                        break
                                    _op = orig_ps[_oi]
                                    _op_text = "".join(
                                        t.text or "" for t in _op.findall(f".//{{{_W}}}t")
                                    ).strip()
                                    if not _op_text:
                                        continue
                                    _op_bold = False
                                    for _or in _op.findall(f".//{{{_W}}}r"):
                                        _orPr = _or.find(f"{{{_W}}}rPr")
                                        if _orPr is not None and _orPr.find(f"{{{_W}}}b") is not None:
                                            _op_bold = True
                                            break
                                    if _op_bold and _oi > c_start and not any(
                                        t.text for t in orig_ps[_oi - 1].findall(f".//{{{_W}}}t")
                                    ):
                                        c_end = _oi
                                        break
                            _cell_prop_prev_end[ci] = c_end
                        para_slice = orig_ps[c_start:c_end]

                    if not para_slice and orig_ps:
                        para_slice = [orig_ps[-1]]

                    # Determine which original para indices should be kept.
                    # new_tc_ps[i] is the deepcopy of orig_ps[i], so matching by
                    # position correctly identifies which deepcopy elements to keep.
                    _keep_ids = {id(p) for p in para_slice}
                    _keep_idxs = {
                        i for i, orig_p in enumerate(orig_ps) if id(orig_p) in _keep_ids
                    }
                    # Remove only the deepcopy paragraphs NOT in the slice.
                    # Paragraphs in the slice stay in new_tc at their original
                    # position so nested subtables (e.g. contact table) preserve
                    # their relative order (before or after those paragraphs).
                    for idx, new_p in enumerate(new_tc_ps):
                        if idx not in _keep_idxs:
                            new_tc.remove(new_p)
                        elif not _cell_has_text:
                            # Separator cell: strip drawings from kept paragraphs
                            # so they don't force the row to the drawing's height.
                            for _dw in list(new_p.findall(f".//{{{_W}}}drawing")):
                                _dw_parent = _dw.getparent()
                                if _dw_parent is not None:
                                    _dw_parent.remove(_dw)

                    # Suppress keepNext/keepLines on all paras so LibreOffice
                    # can paginate between rows without the heading style chain.
                    for cell_p in new_tc.findall(f"{{{_W}}}p"):
                        cell_pPr = cell_p.find(f"{{{_W}}}pPr")
                        if cell_pPr is None:
                            cell_pPr = _et.SubElement(cell_p, f"{{{_W}}}pPr")
                            cell_p.insert(0, cell_pPr)
                        for prop_tag in (f"{{{_W}}}keepNext", f"{{{_W}}}keepLines"):
                            prop = cell_pPr.find(prop_tag)
                            if prop is None:
                                prop = _et.SubElement(cell_pPr, prop_tag)
                            prop.set(f"{{{_W}}}val", "0")

                # Strip leading empty paragraphs from continuation rows to
                # avoid blank gaps at the top of continuation cells (e.g.
                # sample 22 right column — the empty spacer between the first
                # and second Experience roles starts the second split row).
                if slice_num > 0:
                    for _strip_tc in new_cells_elem:
                        while True:
                            _strip_ps = _strip_tc.findall(f"{{{_W}}}p")
                            if len(_strip_ps) <= 1:
                                break
                            if any(t.text for t in _strip_ps[0].findall(f".//{{{_W}}}t")):
                                break
                            _strip_tc.remove(_strip_ps[0])

                new_rows.append(new_row)

            row_idx = list(tbl_elem).index(row)
            tbl_elem.remove(row)
            for offset, new_row in enumerate(new_rows):
                tbl_elem.insert(row_idx + offset, new_row)


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
        _insert_page_images(doc, body, sectPr, d.part)
        d.save(output_path)
        return

    # Single-column PDF with a detected full-width dark header band: use a fresh
    # minimal DOCX (no style template compat settings) and render the dark header
    # as a full-page-width table that starts at the physical page top (margin=0).
    if doc.source_kind == "pdf" and doc.layout.header_bg_color:
        d = Document()
        _patch_bullet_numbering(d)
        body = d.element.body
        sectPr = body.find(f"{{{_W}}}sectPr")
        _apply_pdf_page_geometry(sectPr, doc.layout)
        _render_pdf_single_col_dark_header(doc, body, sectPr, doc_part=d.part)
        # When the entire page has a dark background (full_page_bg in page_images),
        # use the DOCX page background color instead of a floating image.  Behind-text
        # floating images are not reliably rendered by LibreOffice for single-column
        # documents; <w:background> produces a reliable solid page fill.
        _full_bg_list = [
            img for img in (getattr(doc, "page_images", None) or [])
            if img.category == "full_page_bg"
        ]
        if _full_bg_list and doc.layout.header_bg_color:
            _apply_docx_page_background(d, doc.layout.header_bg_color)
            doc.page_images = [img for img in doc.page_images if img.category != "full_page_bg"]
        _insert_page_images(doc, body, sectPr, d.part)
        d.save(output_path)
        return

    # Single-column PDF: detect same-level section groups (e.g. three sections
    # sharing the same y_top, rendered as a multi-column table row).
    if doc.source_kind == "pdf" and doc.layout.column_split_x is None and not doc.layout.header_bg_color:
        _same_level_groups = _detect_same_level_section_groups(doc.sections)
        if _same_level_groups:
            shutil.copy(template_path, output_path)
            _d_grp = Document(output_path)
            _patch_bullet_numbering(_d_grp)
            _body_grp = _d_grp.element.body
            _sectPr_grp = _body_grp.find(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}sectPr")
            for _child in list(_body_grp):
                _body_grp.remove(_child)
            if _sectPr_grp is not None:
                _body_grp.append(_sectPr_grp)
            _apply_pdf_page_geometry(_sectPr_grp, doc.layout)
            if _sectPr_grp is not None:
                _cols_grp = _sectPr_grp.find(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}cols")
                if _cols_grp is not None:
                    _sectPr_grp.remove(_cols_grp)
            _pgSz_g = _sectPr_grp.find(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}pgSz") if _sectPr_grp is not None else None
            _pgMar_g = _sectPr_grp.find(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}pgMar") if _sectPr_grp is not None else None
            _pw_g = int(_pgSz_g.get(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}w", "12240")) if _pgSz_g is not None else 12240
            _lm_g = int(_pgMar_g.get(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}left", "1440")) if _pgMar_g is not None else 1440
            _rm_g = int(_pgMar_g.get(f"{{http://schemas.openxmlformats.org/wordprocessingml/2006/main}}right", "1440")) if _pgMar_g is not None else 1440
            _text_w_g = _pw_g - _lm_g - _rm_g
            _render_pdf_single_col_with_groups(
                doc, _body_grp, _sectPr_grp, _d_grp.part,
                _same_level_groups, _text_w_g,
            )
            _insert_page_images(doc, _body_grp, _sectPr_grp, _d_grp.part)
            _d_grp.save(output_path)
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
            _RENDERER_CREATED_TABLES.clear()
            _render_from_layout_blocks(doc, body, sectPr)
            # Split single-row tables that exceed page height so LibreOffice
            # can paginate them naturally (cantSplit='0' alone is insufficient).
            # Tables created by _render_layout_two_col_table are registered in
            # _RENDERER_CREATED_TABLES and skipped — they are balanced layouts.
            _split_oversized_table_rows(body, sectPr)
            # The split leaves trailing empty paragraphs at the end of each
            # sub-row's cells (template spacing paragraphs that fell between
            # sections).  Strip them here so rows size to actual content.
            _trim_trailing_cell_paras(body)
            # Minimize the mandatory trailing body paragraph so it does not
            # push onto a blank second page when injected content fills page 1.
            _minimize_trailing_body_para(body)
            # Inherit page background color for overflow pages.
            # (a) Solid-fill sidebar backgrounds (e.g. sample 11): clone the
            #     background paragraph from the body start to the body end so
            #     the sidebar color appears on page 2 when content overflows.
            _maybe_clone_solidfill_bg_para(body, sectPr)
            # (b) Blip-image backgrounds (e.g. sample 16): extract dominant
            #     edge color and insert a solid-fill rectangle at body end.
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
        # Strip native word-processor column layout from the style template.
        # For single-column PDFs the column layout is derived from the IR, not
        # from the DOCX style template.  If the style template carries w:cols
        # (e.g. a two-column DOCX used as a style donor), paragraphs would flow
        # through those columns and cause visual column overlap or spillover.
        if doc.layout.column_split_x is None:
            _cols = sectPr.find(f"{{{_W}}}cols")
            if _cols is not None:
                sectPr.remove(_cols)

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

    _h_rule_borders_fallback = (
        _build_h_rule_border_map(doc)
        if doc.source_kind == "pdf" and not has_table_blocks
        else {}
    )

    for item in render_items:
        if isinstance(item, TableBlock):
            _render_table_block(item, doc, body, sectPr)
        else:
            _render_para(item, body, sectPr, preserve_section_break=id(item) in header_para_ids)
            if _h_rule_borders_fallback and item.para_id in _h_rule_borders_fallback:
                # _render_para inserts before sectPr; find the just-added element.
                _last = sectPr.getprevious() if sectPr is not None else (body[-1] if len(body) else None)
                if _last is not None:
                    _apply_h_rule_top_border(_last, _h_rule_borders_fallback[item.para_id])

    if doc.source_kind == "pdf":
        _insert_page_images(doc, body, sectPr, d.part)
    d.save(output_path)
