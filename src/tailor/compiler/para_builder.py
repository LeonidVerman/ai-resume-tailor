"""Build a w:p lxml element from a ParaModel with a ParagraphProfile.

Used by the renderer when xml_proto is not available (PDF-sourced documents).
Creates a minimal but correctly-formatted w:p element using lxml directly,
without the overhead of constructing a full python-docx Document.

Points to twips: 1 pt = 20 twips.
Points to half-points (for sz/szCs): 1 pt = 2 half-points.
"""
from __future__ import annotations

from typing import Any

from tailor.compiler.models import ParaModel, ParagraphProfile

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_ALIGN_MAP = {
    "center": "center",
    "right": "right",
    "justify": "both",
    "left": "left",
}


def build_para_element(pm: ParaModel) -> Any:
    """Return a w:p lxml element for *pm*, styled from its ParagraphProfile.

    If *pm* has no ParagraphProfile, a bare w:p with plain text is returned.
    """
    from lxml import etree

    p = etree.Element(f"{{{_W}}}p")
    pPr = etree.SubElement(p, f"{{{_W}}}pPr")

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

        t = etree.SubElement(r, f"{{{_W}}}t")
        t.text = pm.text
        if pm.text[0] == " " or pm.text[-1] == " ":
            t.set(_XML_SPACE, "preserve")

    return p
