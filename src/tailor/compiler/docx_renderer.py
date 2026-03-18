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
"""
from __future__ import annotations

import shutil
from copy import deepcopy

from docx import Document

from tailor.compiler.models import ParaModel, ResumeDocument

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# Paragraph text replacement (in cloned XML)
# ---------------------------------------------------------------------------

def _strip_section_break(p_elem) -> None:
    pPr = p_elem.find(f"{{{_W}}}pPr")
    if pPr is not None:
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is not None:
            pPr.remove(sectPr)


def _set_para_text(p_elem, text: str) -> None:
    """Set text on p_elem in-place, preserving all formatting.

    Clears run text from every run, then writes text on the first run.
    If no runs exist a minimal w:r/w:t structure is created.
    """
    _strip_section_break(p_elem)

    all_runs: list = []
    for child in p_elem:
        tag = child.tag
        if tag == f"{{{_W}}}r":
            all_runs.append(child)
        elif tag == f"{{{_W}}}hyperlink":
            for r in child.findall(f"{{{_W}}}r"):
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

    # Clear text from all runs
    for r in all_runs:
        for t in r.findall(f"{{{_W}}}t"):
            t.text = ""
        for t in r.findall(f"{{{_W}}}delText"):
            t.text = ""

    # Write new text on first run
    first = all_runs[0]
    t_elems = first.findall(f"{{{_W}}}t")
    if t_elems:
        t_elems[0].text = text
        if text and (text[0] == " " or text[-1] == " "):
            t_elems[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    else:
        from lxml import etree
        t = etree.SubElement(first, f"{{{_W}}}t")
        t.text = text
        if text and (text[0] == " " or text[-1] == " "):
            t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")


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
    body = d.element.body

    # Preserve the top-level sectPr (page margins, size); remove all content elements
    sectPr = body.find(f"{{{_W}}}sectPr")
    to_remove = [
        child for child in body
        if child.tag.split("}")[-1] in ("p", "tbl")
    ]
    for elem in to_remove:
        body.remove(elem)

    # Append cloned paragraphs in document order
    for pm in doc.all_paras:
        if pm.style.xml_proto is None:
            raise ValueError(
                f"Paragraph '{pm.text[:60]}' has no xml_proto; cannot render it. "
                "This indicates a bug in the updater (new paragraphs must be "
                "created via ParaModel.clone_as())."
            )
        clone = deepcopy(pm.style.xml_proto)
        _set_para_text(clone, pm.text)
        if sectPr is not None:
            sectPr.addprevious(clone)
        else:
            body.append(clone)

    d.save(output_path)
