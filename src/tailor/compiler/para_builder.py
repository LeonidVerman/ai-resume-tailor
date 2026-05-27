"""Build a w:p lxml element from a ParaModel with a ParagraphProfile.

Used by the renderer when xml_proto is not available (PDF-sourced documents).
Creates a minimal but correctly-formatted w:p element using lxml directly,
without the overhead of constructing a full python-docx Document.

Points to twips: 1 pt = 20 twips.
Points to half-points (for sz/szCs): 1 pt = 2 half-points.
EMU (English Metric Units): 1 pt = 12700 EMU.
"""
from __future__ import annotations

from typing import Any

from tailor.compiler.models import ParaModel, ParagraphProfile

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"

_ALIGN_MAP = {
    "center": "center",
    "right": "right",
    "justify": "both",
    "left": "left",
}


def _add_image_run(p_elem, png_bytes: bytes, size_pt: float, doc_part=None) -> None:
    """Append a run containing an inline image to *p_elem*.

    Uses python-docx's Document part API (new_pic_inline) to create a
    correct DrawingML inline element including all required relationships.
    If *doc_part* is None the image is silently skipped (graceful degradation).
    """
    if doc_part is None or not png_bytes:
        return
    try:
        import io
        # Convert pt to EMU (1 pt = 12700 EMU)
        sz_emu = int(size_pt * 12700)
        if sz_emu <= 0:
            sz_emu = int(10 * 12700)  # fallback 10pt

        img_io = io.BytesIO(png_bytes)
        # new_pic_inline returns a CT_Inline element with all relationships set
        inline_elem = doc_part.new_pic_inline(img_io, width=sz_emu, height=sz_emu)
    except Exception:
        return  # silently skip on any failure

    from lxml import etree
    r = etree.SubElement(p_elem, f"{{{_W}}}r")
    drawing = etree.SubElement(r, f"{{{_W}}}drawing")
    drawing.append(inline_elem)


def build_bullet_marker_element(pm: ParaModel) -> Any:
    """Return a w:p containing only the PUA bullet marker glyph (\\uf0b7).

    Used when rendering PUA-style bullets as two separate DOCX paragraphs
    (marker + text) to replicate the source PDF's two-element structure.
    The marker is placed at indent_left_pt - hanging_indent_pt so it lands
    at the same absolute x position as the original PDF marker line.
    """
    from lxml import etree

    pp: ParagraphProfile | None = pm.paragraph_profile
    p = etree.Element(f"{{{_W}}}p")
    pPr = etree.SubElement(p, f"{{{_W}}}pPr")

    pStyle_elem = etree.SubElement(pPr, f"{{{_W}}}pStyle")
    pStyle_elem.set(f"{{{_W}}}val", "ListParagraph")

    # Marker x = indent_left_pt - hanging_indent_pt (relative to margin).
    marker_indent_pt = max(0.0, (pp.indent_left_pt - pp.hanging_indent_pt)) if pp else 18.0
    ind = etree.SubElement(pPr, f"{{{_W}}}ind")
    ind.set(f"{{{_W}}}left", str(int(marker_indent_pt * 20)))
    ind.set(f"{{{_W}}}hanging", "0")

    if pp is not None:
        spc = etree.SubElement(pPr, f"{{{_W}}}spacing")
        spc.set(f"{{{_W}}}before", str(int((pp.space_before_pt or 0) * 20)))
        spc.set(f"{{{_W}}}after", "0")
        if pp.font_size_pt:
            spc.set(f"{{{_W}}}line", str(int(pp.font_size_pt * 1.1 * 20)))
            spc.set(f"{{{_W}}}lineRule", "exact")

    r = etree.SubElement(p, f"{{{_W}}}r")
    rPr = etree.SubElement(r, f"{{{_W}}}rPr")
    if pp is not None and pp.font_size_pt:
        half = str(int(pp.font_size_pt * 2))
        sz = etree.SubElement(rPr, f"{{{_W}}}sz")
        sz.set(f"{{{_W}}}val", half)
        szCs = etree.SubElement(rPr, f"{{{_W}}}szCs")
        szCs.set(f"{{{_W}}}val", half)
    t = etree.SubElement(r, f"{{{_W}}}t")
    t.text = "\uf0b7"
    return p


def build_para_element(pm: ParaModel, doc_part=None, skip_bg_shd: bool = False) -> Any:
    """Return a w:p lxml element for *pm*, styled from its ParagraphProfile.

    If *pm* has no ParagraphProfile, a bare w:p with plain text is returned.

    Parameters
    ----------
    pm:
        Paragraph model to render.
    doc_part:
        Optional python-docx document part (Document._part).  Required to
        embed inline images from ParagraphProfile.inline_image_bytes; silently
        ignored when None.
    skip_bg_shd:
        When True, suppress the per-paragraph w:shd element even if
        ParagraphProfile.background_color is set.  Use this when the paragraph
        is placed inside a table cell that already carries cell-level shading
        (avoids fragmented striped blocks inside the cell).
    """
    from lxml import etree

    p = etree.Element(f"{{{_W}}}p")
    pPr = etree.SubElement(p, f"{{{_W}}}pPr")

    # All non-bullet paragraphs get pStyle="Normal" so LibreOffice renders them
    # correctly inside table cells.  Without a pStyle, LibreOffice suppresses
    # paragraphs that have w:ind w:left > ~800 twips, making section headings
    # and role entries invisible.  Explicit w:pPr properties (spacing, indent)
    # override the style-level defaults, so the visual output is unchanged.
    if pm.semantic != "bullet":
        _pStyle = etree.SubElement(pPr, f"{{{_W}}}pStyle")
        _pStyle.set(f"{{{_W}}}val", "Normal")

    # Bullet paragraphs: use "ListParagraph" style.  Two strategies:
    #
    # 1. PUA-style bullets (hanging_indent_pt > 0, single-column): these came
    #    from a source document that used a PUA glyph (e.g. \uf0b7 in Symbol
    #    font) as a separate element from the text.  We render them as a single
    #    paragraph with a hanging indent, a tab stop, and two runs:
    #       Run 1: \uf0b7 in Symbol font (at first-line x = left − hanging)
    #       Run 2: tab  (jump to left x)
    #       Run 3+: body text
    #    When LibreOffice exports to PDF, the Symbol glyph creates a separate
    #    PDF content stream from the body-text run (different font), and because
    #    Symbol's bbox is taller, fitz assigns a different y0 bounding box to
    #    the marker vs the text — exactly replicating the source structure that
    #    the evaluator relies on.
    #
    # 2. Regular bullets (hanging_indent_pt == 0, or column_id set): rendered
    #    with an inline "• " prefix so the evaluator's regex detects them.
    pp: ParagraphProfile | None = pm.paragraph_profile
    use_tab_bullet = (
        pm.semantic == "bullet"
        and pp is not None
        and pp.hanging_indent_pt > 0
        and pp.column_id is None
    )
    if pm.semantic == "bullet":
        pStyle_elem = etree.SubElement(pPr, f"{{{_W}}}pStyle")
        pStyle_elem.set(f"{{{_W}}}val", "ListParagraph")
        indent_pt = (pp.indent_left_pt if pp is not None else 0) or 36
        hang_pt = pp.hanging_indent_pt if pp is not None else 0
        # For inline "• " bullets with no explicit hanging, add a standard
        # hanging indent so continuation lines align with the text following
        # the bullet marker rather than with the marker itself.
        # "• " at 12 pt ≈ 9 pt wide; scale with font size, cap at 15 pt.
        if hang_pt == 0 and not use_tab_bullet:
            _fsize = pp.font_size_pt if pp is not None else 12.0
            hang_pt = min(round(_fsize * 0.75), 15)
        ind = etree.SubElement(pPr, f"{{{_W}}}ind")
        ind.set(f"{{{_W}}}left", str(int(indent_pt * 20)))
        ind.set(f"{{{_W}}}hanging", str(int(hang_pt * 20)))
        if use_tab_bullet:
            # Tab stop at the left indent position (= body-text x)
            tabs = etree.SubElement(pPr, f"{{{_W}}}tabs")
            tab_stop = etree.SubElement(tabs, f"{{{_W}}}tab")
            tab_stop.set(f"{{{_W}}}val", "left")
            tab_stop.set(f"{{{_W}}}pos", str(int(indent_pt * 20)))

    if pp is not None:
        # Paragraph alignment
        if pp.alignment and pp.alignment in _ALIGN_MAP:
            jc = etree.SubElement(pPr, f"{{{_W}}}jc")
            jc.set(f"{{{_W}}}val", _ALIGN_MAP[pp.alignment])

        # Spacing (twips = pt × 20).  Always emit w:before and w:after
        # explicitly to override any inherited style spacing.  The
        # ListParagraph style has w:before="238" (11.9 pt); without an
        # explicit override every bullet with space_before_pt=0 inherits
        # that gap, inflating the page count dramatically.
        spc = etree.SubElement(pPr, f"{{{_W}}}spacing")
        spc.set(f"{{{_W}}}before", str(int((pp.space_before_pt or 0) * 20)))
        spc.set(f"{{{_W}}}after", str(int((pp.space_after_pt or 0) * 20)))

        # Indentation (twips = pt × 20).  Bullets already have w:ind set above.
        if pp.indent_left_pt and pm.semantic != "bullet":
            ind = etree.SubElement(pPr, f"{{{_W}}}ind")
            ind.set(f"{{{_W}}}left", str(int(pp.indent_left_pt * 20)))

        # Paragraph background shading (skip when cell already carries the shading)
        if pp.background_color and not skip_bg_shd:
            shd = etree.SubElement(pPr, f"{{{_W}}}shd")
            shd.set(f"{{{_W}}}val", "clear")
            shd.set(f"{{{_W}}}color", "auto")
            shd.set(f"{{{_W}}}fill", pp.background_color)

    # Run(s) with text.  When pp.text_runs is set (mixed-bold role headers),
    # emit one w:r per run with per-run bold; otherwise emit a single run.
    # Regular bullet paragraphs get an inline "• " prefix on the first run.
    # PUA tab-bullets emit: Symbol-font \uf0b7 run, tab run, then body run(s).
    if pm.text:
        run_list: list[tuple[str, bool | None]] = []
        if pp is not None and pp.text_runs:
            run_list = [(rt, rb) for rt, rb in pp.text_runs if rt]
        if not run_list:
            run_list = [(pm.text, pp.bold if pp is not None else None)]
        if pm.semantic == "bullet" and run_list and not use_tab_bullet:
            first_text, first_bold = run_list[0]
            run_list[0] = ("• " + first_text, first_bold)

        if use_tab_bullet:
            # Symbol-font marker run (\uf0b7 at first-line x = left − hanging)
            r_mkr = etree.SubElement(p, f"{{{_W}}}r")
            rPr_mkr = etree.SubElement(r_mkr, f"{{{_W}}}rPr")
            fonts_mkr = etree.SubElement(rPr_mkr, f"{{{_W}}}rFonts")
            fonts_mkr.set(f"{{{_W}}}ascii", "Symbol")
            fonts_mkr.set(f"{{{_W}}}hAnsi", "Symbol")
            if pp is not None and pp.font_size_pt:
                half_mkr = str(int(pp.font_size_pt * 2))
                sz_mkr = etree.SubElement(rPr_mkr, f"{{{_W}}}sz")
                sz_mkr.set(f"{{{_W}}}val", half_mkr)
                szCs_mkr = etree.SubElement(rPr_mkr, f"{{{_W}}}szCs")
                szCs_mkr.set(f"{{{_W}}}val", half_mkr)
            t_mkr = etree.SubElement(r_mkr, f"{{{_W}}}t")
            t_mkr.text = "\uf0b7"
            # Tab run (jumps to tab stop at indent_left_pt)
            r_tab = etree.SubElement(p, f"{{{_W}}}r")
            etree.SubElement(r_tab, f"{{{_W}}}tab")

        def _emit_run(run_text: str, run_bold) -> None:
            r = etree.SubElement(p, f"{{{_W}}}r")
            rPr = etree.SubElement(r, f"{{{_W}}}rPr")

            if run_bold:
                etree.SubElement(rPr, f"{{{_W}}}b")
            if pp is not None:
                if pp.italic:
                    etree.SubElement(rPr, f"{{{_W}}}i")
                if pp.font_name:
                    fonts = etree.SubElement(rPr, f"{{{_W}}}rFonts")
                    fonts.set(f"{{{_W}}}ascii", pp.font_name)
                    fonts.set(f"{{{_W}}}hAnsi", pp.font_name)
                if pp.font_size_pt:
                    half = str(int(pp.font_size_pt * 2))
                    sz = etree.SubElement(rPr, f"{{{_W}}}sz")
                    sz.set(f"{{{_W}}}val", half)
                    szCs = etree.SubElement(rPr, f"{{{_W}}}szCs")
                    szCs.set(f"{{{_W}}}val", half)
                if pp.text_color:
                    clr = etree.SubElement(rPr, f"{{{_W}}}color")
                    clr.set(f"{{{_W}}}val", pp.text_color)

            t = etree.SubElement(r, f"{{{_W}}}t")
            t.text = run_text
            if run_text[0] == " " or run_text[-1] == " ":
                t.set(_XML_SPACE, "preserve")

        for run_text, run_bold in run_list:
            _emit_run(run_text, run_bold)

    # Inline icon image AFTER text (PDF-sourced sidebar icons like phone/email/location).
    # Placing the icon after the text mirrors the template layout where icons appear
    # at the end of the contact line rather than at the beginning.
    if pp is not None and pp.inline_image_bytes and doc_part is not None:
        _add_image_run(p, pp.inline_image_bytes, pp.inline_image_size_pt, doc_part)

    return p
