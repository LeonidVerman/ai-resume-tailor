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

    # Clear text from all runs
    for run in paragraph.runs:
        run.text = ""

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


def save_doc_from_template(template_path, output_path, new_text):
    """Modify a copy of the template document, replacing text while preserving formatting.

    For the Experience section, entries are matched individually by their index so that
    extra bullets added by the LLM are inserted with the correct List Paragraph style,
    not cloned from whatever paragraph happens to follow in the template.

    All other sections use blank-line grouping (same as the original algorithm).
    """
    new_text = _sanitize_xml_text(new_text)
    doc = Document(template_path)
    paras = doc.paragraphs

    def is_exp_entry_header(p):
        """Experience entry headers use Normal style with bold text and contain '|'."""
        return (
            p.style.name == "Normal"
            and bool(p.text.strip())
            and "|" in p.text
            and any(r.bold for r in p.runs)
        )

    # --- Find Experience section boundaries in the template ---
    exp_h2_idx = None
    post_exp_h2_idx = None
    for i, p in enumerate(paras):
        if p.style.name == "Heading 2" and "experience" in p.text.lower():
            exp_h2_idx = i
        elif exp_h2_idx is not None and p.style.name == "Heading 2" and post_exp_h2_idx is None:
            post_exp_h2_idx = i
    if post_exp_h2_idx is None:
        post_exp_h2_idx = len(paras)

    # --- Find Experience section boundaries in LLM text ---
    llm_lines = new_text.split("\n")
    llm_exp_idx = None
    llm_post_exp_idx = None
    for i, line in enumerate(llm_lines):
        s = line.strip()
        if s == "Experience":
            llm_exp_idx = i
        elif llm_exp_idx is not None and s in _SECTION_HEADERS and llm_post_exp_idx is None:
            llm_post_exp_idx = i
    if llm_post_exp_idx is None:
        llm_post_exp_idx = len(llm_lines)

    # --- Fall back to the original full-document algorithm if sections not found ---
    if exp_h2_idx is None or llm_exp_idx is None:
        _apply_groups(paras, new_text, doc)
        doc.save(output_path)
        return

    # --- Pre-experience section ---
    # Skip name + contact paragraphs (before the first Heading 2) entirely — they
    # contain hyperlinks whose XML persists even after replace_paragraph_text, which
    # causes email/LinkedIn to appear duplicated.  Only update the sections that
    # follow (Professional Summary and onwards).
    first_h2_idx = next(
        (i for i, p in enumerate(paras) if p.style.name == "Heading 2"),
        exp_h2_idx,
    )
    llm_first_h2_idx = next(
        (
            i for i, line in enumerate(llm_lines)
            if line.strip() and line.strip() == paras[first_h2_idx].text.strip()
        ),
        0,
    )
    _apply_groups(
        paras[first_h2_idx:exp_h2_idx],
        "\n".join(llm_lines[llm_first_h2_idx:llm_exp_idx]),
        doc,
    )

    # --- "Experience" heading ---
    replace_paragraph_text(paras[exp_h2_idx], llm_lines[llm_exp_idx].strip())

    # --- Parse template experience entries ---
    # Each entry starts at a Normal+bold paragraph and ends just before the next one.
    tmpl_entries = []
    cur = None
    for i in range(exp_h2_idx + 1, post_exp_h2_idx):
        p = paras[i]
        if is_exp_entry_header(p):
            if cur is not None:
                tmpl_entries.append(cur)
            cur = [p]
        elif cur is not None:
            cur.append(p)  # date, bullets, and any blanks within the entry
    if cur is not None:
        tmpl_entries.append(cur)

    # --- Parse LLM experience entries ---
    def is_llm_exp_hdr(line):
        s = line.strip()
        return bool(s) and "|" in s and not s.startswith("-")

    llm_entries = []
    cur = None
    for line in llm_lines[llm_exp_idx + 1:llm_post_exp_idx]:
        s = line.strip()
        if not s:
            continue  # skip blank lines between entries
        if is_llm_exp_hdr(line):
            if cur is not None:
                llm_entries.append(cur)
            cur = [s]
        elif cur is not None:
            cur.append(s[2:] if s.startswith("- ") else s)
    if cur is not None:
        llm_entries.append(cur)

    # --- Match and apply experience entries by index ---
    for idx, tmpl_entry in enumerate(tmpl_entries):
        if idx >= len(llm_entries):
            # This template entry has no corresponding LLM entry — remove every
            # paragraph so cleared-but-present empty paragraphs don't create a
            # block of blank lines before the next section.
            for p in tmpl_entry:
                _remove_para(p)
            continue

        llm_entry = llm_entries[idx]

        # Find a bullet paragraph to use as the style source for any new bullets
        bullet_para = next(
            (p for p in tmpl_entry if p.style.name == "List Paragraph"), None
        )

        # Replace the experience header (keep its blue/bold formatting)
        replace_paragraph_text(tmpl_entry[0], llm_entry[0])
        last_para = tmpl_entry[0]

        content_paras = tmpl_entry[1:]   # date + bullets in template
        content_lines = llm_entry[1:]    # date + bullets from LLM

        # Some templates split a header across two paragraphs (e.g. a long company
        # name wraps to a second Normal+bold line).  The LLM always emits a single
        # header line, so remove those continuation paragraphs entirely.
        skip = 0
        for p in content_paras:
            if p.style.name == "Normal" and any(r.bold for r in p.runs):
                _remove_para(p)
                skip += 1
            else:
                break
        content_paras = content_paras[skip:]

        for j, text in enumerate(content_lines):
            if j < len(content_paras):
                replace_paragraph_text(content_paras[j], text)
                last_para = content_paras[j]
            else:
                # Extra bullet: clone from bullet_para to get List Paragraph style
                last_para = insert_paragraph_after(
                    last_para, text, style_source=bullet_para
                )

        # Remove leftover template paragraphs that the LLM didn't fill.
        # Blank paragraphs (separators between entries) are kept so vertical
        # spacing between entries is preserved; content paragraphs (bullets,
        # dates) that went unused are removed to avoid blank-line artefacts.
        for j in range(len(content_lines), len(content_paras)):
            p = content_paras[j]
            if p.text.strip():
                _remove_para(p)
            else:
                _clear_para(p)

    # --- Post-experience section ---
    _apply_groups(paras[post_exp_h2_idx:], "\n".join(llm_lines[llm_post_exp_idx:]), doc)

    doc.save(output_path)
