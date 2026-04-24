"""DOCX template manipulation.

Fills a .docx template with LLM-generated text while preserving all
paragraph formatting (fonts, colours, indentation, list styles, etc.).
"""

import re
from copy import deepcopy

from docx import Document
from docx.text.paragraph import Paragraph as DocxParagraph

from tailor.docx import _W

# ---------------------------------------------------------------------------
# XML sanitization
# ---------------------------------------------------------------------------

# Characters invalid in XML 1.0: 0x00-0x08, 0x0B-0x0C, 0x0E-0x1F, 0xFFFE, 0xFFFF.
# \x09 (tab), \x0A (newline), \x0D (CR) are the only valid control chars.
_INVALID_XML_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\uFFFE\uFFFF]")

# \x13 (DC3/XOFF) is sometimes emitted by the LLM as a stand-in for an en-dash.
_CHAR_SUBSTITUTIONS: dict[str, str] = {
    "\x13": "\u2013",  # en-dash
    "\x96": "\u2013",  # en-dash (cp1252 byte 0x96)
    "\x97": "\u2014",  # em-dash (cp1252 byte 0x97)
}


def _sanitize_xml_text(text: str) -> str:
    """Replace known problem chars and strip any remaining invalid XML 1.0 chars."""
    for bad, good in _CHAR_SUBSTITUTIONS.items():
        text = text.replace(bad, good)
    return _INVALID_XML_RE.sub("", text)

# Section headers that terminate the Experience block in LLM output
_SECTION_HEADERS = {
    "Education", "Technical Skills", "Skills", "Certifications",
    "Projects", "Publications", "Volunteer", "Awards", "References",
}


# ---------------------------------------------------------------------------
# Paragraph-level helpers
# ---------------------------------------------------------------------------

def _strip_section_break(para):
    """Remove any section break (w:sectPr) embedded in a paragraph's w:pPr.

    Templates sometimes carry a mid-document section break in a paragraph's
    pPr (visible as an unwanted page break).  We always remove it when we
    touch a paragraph so it doesn't bleed into the output.
    """
    pPr = para._p.find(f"{{{_W}}}pPr")
    if pPr is not None:
        sectPr = pPr.find(f"{{{_W}}}sectPr")
        if sectPr is not None:
            pPr.remove(sectPr)


def _strip_pPr_rPr_color(para):
    """Remove a stale color override from the paragraph's pPr/rPr element.

    Word stores character properties for the paragraph mark in pPr/rPr.
    Any run that has no explicit color inherits this value, so a leftover
    color here (e.g. blue 4E80BC from a template experience-header slot)
    causes newly written bullet text to render in that colour instead of
    the document default.  Removing it lets runs fall back to their own
    colour or the style default (black).
    """
    pPr = para._p.find(f"{{{_W}}}pPr")
    if pPr is None:
        return
    rPr = pPr.find(f"{{{_W}}}rPr")
    if rPr is None:
        return
    color = rPr.find(f"{{{_W}}}color")
    if color is not None:
        rPr.remove(color)
    if len(rPr) == 0:          # rPr is now empty — remove it too
        pPr.remove(rPr)


def replace_paragraph_text(paragraph, new_text):
    """Replace text in a paragraph while preserving the formatting of the first run.

    Also strips any embedded section break so the paragraph never forces a
    page break, and clears any stale paragraph-level color so new text renders
    in the correct colour.
    """
    _strip_section_break(paragraph)
    _strip_pPr_rPr_color(paragraph)

    if not paragraph.runs:
        paragraph.text = new_text
        return

    # Store first run's formatting by keeping the run object
    first_run = paragraph.runs[0]

    # Clear text from all runs (direct runs and runs nested inside hyperlinks)
    for run in paragraph.runs:
        run.text = ""
    for hyperlink in paragraph._p.findall(f"{{{_W}}}hyperlink"):
        for t in hyperlink.iter(f"{{{_W}}}t"):
            t.text = ""

    # Set new text on first run (preserves its formatting)
    first_run.text = new_text


def _clear_para(para):
    """Clear a template paragraph that has no matching LLM content.

    Beyond setting text to "", also removes list-numbering (w:numPr) and any
    embedded section break so the empty paragraph doesn't render as a
    visible bullet or trigger an unwanted page break.
    """
    replace_paragraph_text(para, "")
    pPr = para._p.find(f"{{{_W}}}pPr")
    if pPr is not None:
        numPr = pPr.find(f"{{{_W}}}numPr")
        if numPr is not None:
            pPr.remove(numPr)


def _remove_para(para):
    """Physically remove a paragraph from the document XML tree.

    Use this instead of _clear_para when the paragraph should not exist at all
    (e.g. an entire removed experience entry or a surplus content paragraph
    within a matched entry).  Removing rather than blanking eliminates the
    vertical space that blank paragraphs still occupy.
    """
    p = para._p
    parent = p.getparent()
    if parent is not None:
        parent.remove(p)


def insert_paragraph_after(ref_para, text, style_source=None):
    """Clone style_source's XML (or ref_para's if style_source is None), set text,
    insert the clone immediately after ref_para, and return it as a Paragraph.

    Stale paragraph-level colour is stripped from the clone before text is set
    (replace_paragraph_text handles that automatically).
    """
    clone_from = style_source if style_source is not None else ref_para
    new_p = deepcopy(clone_from._p)
    ref_para._p.addnext(new_p)
    new_para = DocxParagraph(new_p, ref_para._p.getparent())
    replace_paragraph_text(new_para, text)
    return new_para


def _apply_groups(para_list, text, doc):
    """Apply LLM text to a list of template paragraphs using blank-line grouping.

    Groups are separated by empty paragraphs in the template and blank lines in text.
    """
    llm_groups = [[]]
    for line in text.split("\n"):
        if not line.strip():
            llm_groups.append([])
        else:
            llm_groups[-1].append(line)

    template_groups = [[]]
    template_sep_paras = []
    for para in para_list:
        if not para.text.strip():
            template_groups.append([])
            template_sep_paras.append(para)
        else:
            template_groups[-1].append(para)

    global_last_para = None

    for i in range(max(len(template_groups), len(llm_groups))):
        template_group = template_groups[i] if i < len(template_groups) else []
        llm_group = llm_groups[i] if i < len(llm_groups) else []

        group_last_para = None

        for j, llm_line in enumerate(llm_group):
            line_text = llm_line.strip()
            if line_text.startswith("- "):
                line_text = line_text[2:]

            if j < len(template_group):
                replace_paragraph_text(template_group[j], line_text)
                group_last_para = template_group[j]
            else:
                anchor = group_last_para or global_last_para
                if anchor is not None:
                    group_last_para = insert_paragraph_after(anchor, line_text)
                else:
                    group_last_para = doc.add_paragraph(line_text)

        for j in range(len(llm_group), len(template_group)):
            _clear_para(template_group[j])

        if group_last_para:
            global_last_para = group_last_para
        elif template_group:
            global_last_para = template_group[-1]

        if i < len(template_sep_paras):
            global_last_para = template_sep_paras[i]


# ---------------------------------------------------------------------------
# Cover-letter normalisation
# ---------------------------------------------------------------------------

def normalize_cover_letter(text: str) -> str:
    """Strip spurious blank lines from LLM cover letter output.

    The cover letter template has exactly one blank-line separator — the one
    immediately before "Sincerely,".  The LLM often outputs blank lines
    between every paragraph, which confuses _apply_groups (it expects the same
    number of blank-line groups as the template).  This function collapses all
    blank lines so the result has the same two-group structure:

        <all body lines — no internal blank lines>
        <blank line>
        Sincerely,
        <name>
    """
    lines = text.split("\n")

    sincerely_idx = next(
        (i for i, l in enumerate(lines) if l.strip().lower().startswith("sincerely")),
        None,
    )

    if sincerely_idx is None:
        # No closing found — just remove blank lines and return as-is.
        return "\n".join(l for l in lines if l.strip())

    body_lines   = [l for l in lines[:sincerely_idx] if l.strip()]
    closing_lines = [l for l in lines[sincerely_idx:] if l.strip()]

    return "\n".join(body_lines) + "\n\n" + "\n".join(closing_lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def read_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])


def save_doc_from_template(template_path, output_path, new_text, classification=None):
    """Render a tailored DOCX from a master resume template and LLM output text.

    For resume documents the compiler pipeline is used:
    - Parses the template into a ResumeDocument IR
    - Parses the LLM plain text into structured sections
    - Matches sections/roles and applies tailored content
    - Renders a fresh DOCX preserving all paragraph formatting

    For cover letters (no experience section detected) the original
    _apply_groups algorithm is used as a fallback.

    Parameters
    ----------
    classification:
        Optional ClassificationOutput from upload-time LLM classification.
        Passed through to compile_resume / apply_tailored.  None → existing behavior.
    """
    new_text = _sanitize_xml_text(new_text)

    # Detect whether this is a resume (has an experience section) or a cover letter.
    # We inspect the template quickly before deciding which path to take.
    from tailor.compiler.docx_parser import parse_docx
    try:
        parsed = parse_docx(template_path)
        has_experience = any(s.semantic_type == "experience" for s in parsed.sections)
    except Exception:
        has_experience = False

    if has_experience:
        from tailor.compiler.pipeline import compile_resume
        return compile_resume(template_path, new_text, output_path, classification=classification)
    else:
        # Cover letter path: use blank-line group filling
        doc = Document(template_path)
        _apply_groups(doc.paragraphs, new_text, doc)
        doc.save(output_path)
        return None
