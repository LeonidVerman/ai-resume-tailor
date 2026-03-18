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

from tailor.compiler.models import ParaModel, ResumeDocument, TableBlock

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
# Item renderers
# ---------------------------------------------------------------------------

def _render_para(pm: ParaModel, body, sectPr) -> None:
    """Render a single ParaModel and insert it before sectPr (or append)."""
    if pm.style.xml_proto is not None:
        clone = deepcopy(pm.style.xml_proto)
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


def _render_table_block(tb: TableBlock, doc: "ResumeDocument", body, sectPr) -> None:
    """Clone a TableBlock's xml_proto, update paragraph text, and insert it."""
    clone = deepcopy(tb.xml_proto)
    clone_paras = clone.findall(f".//{{{_W}}}p")

    if len(clone_paras) == len(tb.para_indices):
        # Happy path: counts match — update each paragraph in place.
        for p_elem, para_idx in zip(clone_paras, tb.para_indices):
            pm = doc.all_paras[para_idx]
            _set_para_text(p_elem, pm.text)
    # else: count mismatch (shouldn't happen unless LLM restructured the table);
    # fall through and insert the unmodified clone so the layout is preserved.

    if sectPr is not None:
        sectPr.addprevious(clone)
    else:
        body.append(clone)


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

    # Remove ALL body children, then re-append sectPr last.
    # Removing only w:p and w:tbl would leave nested paragraphs inside
    # w:sdt (content controls) which survive and produce duplicate content.
    sectPr = body.find(f"{{{_W}}}sectPr")
    for child in list(body):
        body.remove(child)
    if sectPr is not None:
        body.append(sectPr)

    # Determine rendering order: body_items when available (preserves tables),
    # falling back to flat all_paras for PDF-sourced / deserialised documents.
    render_items = doc.body_items if doc.body_items is not None else doc.all_paras

    for item in render_items:
        if isinstance(item, TableBlock):
            _render_table_block(item, doc, body, sectPr)
        else:
            _render_para(item, body, sectPr)

    d.save(output_path)
