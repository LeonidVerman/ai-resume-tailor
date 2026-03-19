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


def build_para_element(pm: ParaModel, doc_part=None) -> Any:
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
    """
    from lxml import etree

    p = etree.Element(f"{{{_W}}}p")
    pPr = etree.SubElement(p, f"{{{_W}}}pPr")

    # Bullet paragraphs must carry a "ListBullet" pStyle so that docx_parser
    # can re-classify them as "bullet" semantic when the rendered DOCX is
    # re-parsed.  docx_parser._infer_semantic checks 'list' in style_name, and
    # style_map.get(sid, sid) falls back to the raw ID when the style is absent
    # from the template — so "ListBullet" → style_name="ListBullet" → matches.
    if pm.semantic == "bullet":
        pStyle_elem = etree.SubElement(pPr, f"{{{_W}}}pStyle")
        pStyle_elem.set(f"{{{_W}}}val", "ListBullet")

    pp: ParagraphProfile | None = pm.paragraph_profile

    if pp is not None:
        # Paragraph alignment
        if pp.alignment and pp.alignment in _ALIGN_MAP:
            jc = etree.SubElement(pPr, f"{{{_W}}}jc")
            jc.set(f"{{{_W}}}val", _ALIGN_MAP[pp.alignment])

        # Spacing (twips = pt × 20)
        if pp.space_before_pt or pp.space_after_pt:
            spc = etree.SubElement(pPr, f"{{{_W}}}spacing")
            if pp.space_before_pt:
                spc.set(f"{{{_W}}}before", str(int(pp.space_before_pt * 20)))
            if pp.space_after_pt:
                spc.set(f"{{{_W}}}after", str(int(pp.space_after_pt * 20)))

        # Indentation (twips = pt × 20)
        if pp.indent_left_pt:
            ind = etree.SubElement(pPr, f"{{{_W}}}ind")
            ind.set(f"{{{_W}}}left", str(int(pp.indent_left_pt * 20)))

        # Paragraph background shading
        if pp.background_color:
            shd = etree.SubElement(pPr, f"{{{_W}}}shd")
            shd.set(f"{{{_W}}}val", "clear")
            shd.set(f"{{{_W}}}color", "auto")
            shd.set(f"{{{_W}}}fill", pp.background_color)

    # Run with text
    if pm.text:
        r = etree.SubElement(p, f"{{{_W}}}r")
        rPr = etree.SubElement(r, f"{{{_W}}}rPr")

        if pp is not None:
            if pp.bold:
                etree.SubElement(rPr, f"{{{_W}}}b")
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
        t.text = pm.text
        if pm.text[0] == " " or pm.text[-1] == " ":
            t.set(_XML_SPACE, "preserve")

    # Inline icon image (PDF-sourced sidebar icons like phone/email/location)
    if pp is not None and pp.inline_image_bytes and doc_part is not None:
        _add_image_run(p, pp.inline_image_bytes, pp.inline_image_size_pt, doc_part)

    return p
