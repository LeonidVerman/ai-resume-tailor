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


def _render_pdf_two_col(doc: "ResumeDocument", body, sectPr) -> None:
    """Render a two-column PDF-sourced document as a borderless DOCX table.

    Creates a single-row w:tbl with two cells whose widths are proportional to
    the column split detected in the PDF.  The left cell receives cell shading
    from layout.left_col_bg_color when available.  All paragraphs tagged
    column_id='left' go into the left cell; everything else goes into the
    right cell.
    """
    from lxml import etree
    from tailor.compiler.para_builder import build_para_element

    layout = doc.layout
    text_w = _get_text_area_width_twips(sectPr)

    # Compute column widths proportionally so the table fills the text area.
    total_pdf = (layout.left_col_width_twips or 1) + (layout.right_col_width_twips or 1)
    left_fraction = (layout.left_col_width_twips or 1) / total_pdf
    left_w = int(text_w * left_fraction)
    right_w = text_w - left_w

    # Table element
    tbl = etree.Element(f"{{{_W}}}tbl")

    # Table properties: fixed total width, no borders, no cell margins
    tblPr = etree.SubElement(tbl, f"{{{_W}}}tblPr")
    tblW = etree.SubElement(tblPr, f"{{{_W}}}tblW")
    tblW.set(f"{{{_W}}}w", str(text_w))
    tblW.set(f"{{{_W}}}type", "dxa")

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

    # Distribute paragraphs into cells
    left_paras = [
        pm for pm in doc.all_paras
        if pm.paragraph_profile and pm.paragraph_profile.column_id == "left"
    ]
    right_paras = [
        pm for pm in doc.all_paras
        if not (pm.paragraph_profile and pm.paragraph_profile.column_id == "left")
    ]

    for pm in left_paras:
        left_tc.append(build_para_element(pm))
    # DOCX requires at least one paragraph per cell
    if not left_paras:
        etree.SubElement(left_tc, f"{{{_W}}}p")

    for pm in right_paras:
        right_tc.append(build_para_element(pm))
    if not right_paras:
        etree.SubElement(right_tc, f"{{{_W}}}p")

    if sectPr is not None:
        sectPr.addprevious(tbl)
    else:
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

    # PDF sources with a detected two-column layout: render as a borderless
    # two-cell table so that sidebar and main content are placed in separate
    # columns with correct widths, indentation, and background colours.
    if doc.source_kind == "pdf" and doc.layout.column_split_x is not None:
        _render_pdf_two_col(doc, body, sectPr)
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

    for item in render_items:
        if isinstance(item, TableBlock):
            _render_table_block(item, doc, body, sectPr)
        else:
            _render_para(item, body, sectPr)

    d.save(output_path)
