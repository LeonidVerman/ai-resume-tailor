import html as _html_lib
import json
import os
from copy import deepcopy
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from docx import Document
from docx.text.paragraph import Paragraph as DocxParagraph
from dotenv import load_dotenv
from openai import OpenAI
from playwright.sync_api import sync_playwright
from xhtml2pdf import pisa

# XML namespace shorthand
_W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


# ---------------------------
# Utilities
# ---------------------------

def get_rendered_html(url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(url, timeout=60000)
        page.wait_for_load_state("networkidle")

        html = page.content()
        browser.close()

    return html

def read_docx(file_path):
    doc = Document(file_path)
    return "\n".join([p.text for p in doc.paragraphs])


def _confirm_overwrite(path):
    """Return True if *path* does not exist or the user confirms overwriting it."""
    if not os.path.exists(path):
        return True
    answer = input(f"File already exists: {path}\nOverwrite? [y/N] ").strip().lower()
    return answer in ('y', 'yes')


def _strip_section_break(para):
    """Remove any section break (w:sectPr) embedded in a paragraph's w:pPr.

    Templates sometimes carry a mid-document section break in a paragraph's
    pPr (visible as an unwanted page break).  We always remove it when we
    touch a paragraph so it doesn't bleed into the output.
    """
    pPr = para._p.find(f'{{{_W}}}pPr')
    if pPr is not None:
        sectPr = pPr.find(f'{{{_W}}}sectPr')
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
    pPr = para._p.find(f'{{{_W}}}pPr')
    if pPr is None:
        return
    rPr = pPr.find(f'{{{_W}}}rPr')
    if rPr is None:
        return
    color = rPr.find(f'{{{_W}}}color')
    if color is not None:
        rPr.remove(color)
    if len(rPr) == 0:          # rPr is now empty — remove it too
        pPr.remove(rPr)


def replace_paragraph_text(paragraph, new_text):
    """
    Replace text in a paragraph while preserving the formatting of the first run.
    Also strips any embedded section break so the paragraph never forces a page break,
    and clears any stale paragraph-level color so new text renders in the correct colour.
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
    replace_paragraph_text(para, '')
    pPr = para._p.find(f'{{{_W}}}pPr')
    if pPr is not None:
        numPr = pPr.find(f'{{{_W}}}numPr')
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
    """
    Clone style_source's XML (or ref_para's if style_source is None), set text,
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
    """
    Apply LLM text to a list of template paragraphs using blank-line grouping.
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


# Section headers that terminate the Experience block in LLM output
_SECTION_HEADERS = {'Education', 'Technical Skills', 'Skills', 'Certifications',
                    'Projects', 'Publications', 'Volunteer', 'Awards', 'References'}


def save_doc_from_template(template_path, output_path, new_text):
    """
    Modify a copy of the template document, replacing text while preserving formatting.

    For the Experience section, entries are matched individually by their index so that
    extra bullets added by the LLM are inserted with the correct List Paragraph style,
    not cloned from whatever paragraph happens to follow in the template.

    All other sections use blank-line grouping (same as the original algorithm).
    """
    doc = Document(template_path)
    paras = doc.paragraphs

    def is_exp_entry_header(p):
        """Experience entry headers use Normal style with bold text and contain '|'."""
        return (p.style.name == 'Normal' and bool(p.text.strip())
                and '|' in p.text
                and any(r.bold for r in p.runs))

    # --- Find Experience section boundaries in the template ---
    exp_h2_idx = None
    post_exp_h2_idx = None
    for i, p in enumerate(paras):
        if p.style.name == 'Heading 2' and 'experience' in p.text.lower():
            exp_h2_idx = i
        elif exp_h2_idx is not None and p.style.name == 'Heading 2' and post_exp_h2_idx is None:
            post_exp_h2_idx = i
    if post_exp_h2_idx is None:
        post_exp_h2_idx = len(paras)

    # --- Find Experience section boundaries in LLM text ---
    llm_lines = new_text.split('\n')
    llm_exp_idx = None
    llm_post_exp_idx = None
    for i, line in enumerate(llm_lines):
        s = line.strip()
        if s == 'Experience':
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
    first_h2_idx = next((i for i, p in enumerate(paras)
                         if p.style.name == 'Heading 2'), exp_h2_idx)
    llm_first_h2_idx = next((i for i, line in enumerate(llm_lines)
                              if line.strip() and
                              line.strip() == paras[first_h2_idx].text.strip()),
                             0)
    _apply_groups(paras[first_h2_idx:exp_h2_idx],
                  '\n'.join(llm_lines[llm_first_h2_idx:llm_exp_idx]), doc)

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
        return bool(s) and '|' in s and not s.startswith('-')

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
            cur.append(s[2:] if s.startswith('- ') else s)
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
        bullet_para = next((p for p in tmpl_entry if p.style.name == 'List Paragraph'), None)

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
            if p.style.name == 'Normal' and any(r.bold for r in p.runs):
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
                last_para = insert_paragraph_after(last_para, text,
                                                   style_source=bullet_para)

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
    _apply_groups(paras[post_exp_h2_idx:], '\n'.join(llm_lines[llm_post_exp_idx:]), doc)

    doc.save(output_path)


def _register_fonts():
    """Patch xhtml2pdf for Windows font loading and return @font-face CSS.

    On Windows, xhtml2pdf has two bugs that prevent loading local TTF fonts:
      1. LocalProtocolURI.extract_data does not handle file:///C:/... paths.
      2. BaseFile.get_named_tmp_file uses NamedTemporaryFile, which holds an
         exclusive lock on Windows so ReportLab cannot re-open it by name.

    This function patches both issues and returns a CSS string containing
    @font-face declarations for every Calibri/Cambria variant found in the
    Windows Fonts directory.  On non-Windows or when fonts are absent it
    returns an empty string so the caller can fall back gracefully.
    """
    import sys, tempfile
    from pathlib import Path
    from urllib.parse import urlparse

    fonts_dir = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
    if not os.path.isdir(fonts_dir):
        return ''

    # ── Patch 1: LocalProtocolURI.extract_data ──────────────────────────────
    # self.path is the full URI ("file:///C:/..."), not just the path component.
    # The original code checks self.path.startswith("/") which is always False
    # for file:// URIs, so it always returns None on Windows.
    try:
        import xhtml2pdf.files as _xf

        def _win_lp_extract_data(self):
            parsed = urlparse(self.path or '')
            if parsed.scheme == 'file':
                p = parsed.path          # '/C:/Windows/...' on Windows
                if p.startswith('/') and len(p) >= 3 and p[2] == ':':
                    p = p[1:]            # strip leading slash → 'C:/Windows/...'
                fpath = Path(p)
                if fpath.is_file():
                    self.uri = fpath
                    self.suffix = fpath.suffix
                    with open(fpath, 'rb') as fh:
                        return fh.read()
            return None

        _xf.LocalProtocolURI.extract_data = _win_lp_extract_data

        # ── Patch 2: BaseFile.get_named_tmp_file ────────────────────────────
        # NamedTemporaryFile on Windows holds an exclusive lock; use mkstemp
        # and close the fd before returning so ReportLab can open the file.
        def _win_get_named_tmp_file(self):
            data = self.get_data()
            fd, name = tempfile.mkstemp(suffix=self.suffix or '.tmp')
            try:
                if data:
                    os.write(fd, data)
            finally:
                os.close(fd)        # close fd so other processes can read

            class _TmpProxy:
                def __init__(self, path): self.name = path
                def close(self):
                    try: os.unlink(self.name)
                    except OSError: pass
                def __del__(self): self.close()

            proxy = _TmpProxy(name)
            _xf.files_tmp.append(proxy)
            if self.path is None:
                self.path = name
            return proxy

        _xf.BaseFile.get_named_tmp_file = _win_get_named_tmp_file

    except Exception:
        return ''   # xhtml2pdf unavailable or patching failed – no custom fonts

    # ── Build @font-face CSS for available font files ───────────────────────
    # Use forward slashes in the URL so urlparse handles the drive letter.
    fdir = fonts_dir.replace('\\', '/')

    # (filename, font-weight, font-style)
    font_specs = [
        ('Calibri', [
            ('calibri.ttf',  'normal', 'normal'),
            ('calibrib.ttf', 'bold',   'normal'),
            ('calibrii.ttf', 'normal', 'italic'),
            ('calibriz.ttf', 'bold',   'italic'),
        ]),
        ('Cambria', [
            ('cambria.ttc',  'normal', 'normal'),
            ('cambriab.ttf', 'bold',   'normal'),
            ('cambriai.ttf', 'normal', 'italic'),
            ('cambriaz.ttf', 'bold',   'italic'),
        ]),
    ]

    css_lines = []
    for family, variants in font_specs:
        for fname, weight, style in variants:
            fpath = os.path.join(fonts_dir, fname)
            if os.path.exists(fpath):
                url = f'file:///{fdir}/{fname}'
                css_lines.append(
                    f'@font-face {{ font-family: {family}; '
                    f'src: url("{url}"); '
                    f'font-weight: {weight}; font-style: {style}; }}'
                )

    return '\n'.join(css_lines)


def _para_ind(para):
    """Read effective paragraph indentation (paragraph XML then style chain), in points.

    Returns (left_pt, right_pt, first_pt) where first_pt is negative for a
    hanging indent and positive for a first-line indent.  All values default
    to 0.0 when not found anywhere in the style chain.
    """
    def _tw(ind_el, a):
        v = ind_el.get(f'{{{_W}}}{a}')
        try: return int(v) / 20.0
        except (TypeError, ValueError): return 0.0

    def _read(pPr_el):
        """Return (left, right, first) if a w:ind element exists, else None."""
        if pPr_el is None:
            return None
        ind = pPr_el.find(f'{{{_W}}}ind')
        if ind is None:
            return None   # no ind element → keep searching style chain
        left    = _tw(ind, 'left')
        right   = _tw(ind, 'right')
        hanging = _tw(ind, 'hanging')
        first   = _tw(ind, 'firstLine')
        if hanging > 0:
            first = -hanging
        # An explicit w:ind element always wins, even when all values are zero
        # (paragraph overriding style indentation back to zero is intentional).
        return (left, right, first)

    r = _read(para._p.find(f'{{{_W}}}pPr'))
    if r is not None:
        return r
    style = para.style
    while style is not None:
        try:
            r = _read(style.element.find(f'{{{_W}}}pPr'))
            if r is not None:
                return r
        except Exception:
            pass
        style = getattr(style, 'base_style', None)
    return (0.0, 0.0, 0.0)


def _para_spacing(para):
    """Read effective paragraph spacing (paragraph XML then style chain).

    Returns (before_pt, after_pt, line_height) where before_pt / after_pt are
    floats in points (or None when absent) and line_height is either a float
    multiplier (e.g. 1.15) or a CSS string like '14.0pt' (or None when absent).
    Values are filled from paragraph XML first, then style chain for any that
    remain None.
    """
    before = [None]
    after  = [None]
    lh     = [None]

    def _apply(pPr_el):
        if pPr_el is None:
            return
        sp = pPr_el.find(f'{{{_W}}}spacing')
        if sp is None:
            return
        def _tw(a):
            v = sp.get(f'{{{_W}}}{a}')
            try: return int(v) / 20.0
            except (TypeError, ValueError): return None
        if before[0] is None: before[0] = _tw('before')
        if after[0]  is None: after[0]  = _tw('after')
        if lh[0] is None:
            lv_s = sp.get(f'{{{_W}}}line')
            lr_s = sp.get(f'{{{_W}}}lineRule')
            if lv_s:
                try:
                    lv = int(lv_s)
                    if lr_s in (None, 'auto'):
                        # auto: lv is in 1/240ths of a single-spaced line
                        lh[0] = round(lv / 240.0, 3)
                    elif lr_s == 'exact':
                        # exact: lv is in twips → convert to pt
                        lh[0] = f'{lv / 20.0:.1f}pt'
                    # atLeast: minimum constraint, not an exact target —
                    # let the renderer use natural line height instead.
                except (TypeError, ValueError):
                    pass

    _apply(para._p.find(f'{{{_W}}}pPr'))
    style = para.style
    while style is not None and (before[0] is None or after[0] is None or lh[0] is None):
        try:
            _apply(style.element.find(f'{{{_W}}}pPr'))
        except Exception:
            pass
        style = getattr(style, 'base_style', None)
    return (before[0], after[0], lh[0])


def _docx_to_html(docx_path, font_face_css=''):
    """Convert a .docx to an HTML string, preserving template paragraph styles.

    Maps the template's named styles to semantic HTML + CSS:
      Title / Heading 1  → <h1>  (name line, centered)
      Heading 2          → <h2>  (section headers)
      Normal + bold      → <p class="exp-header">  (experience entry headers)
      Body Text          → <p class="body-text">   (dates, contact)
      List Paragraph     → <p class="bullet-item"> (bullet points)
      Normal             → <p>
    Run-level bold/italic/color/size is preserved.
    Paragraph alignment, indentation, and spacing are read from the paragraph
    XML (falling back to the style chain) and applied as inline styles so the
    output closely matches the source document's layout.
    Font sizes and page margins are read directly from the document.
    """
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document(docx_path)

    # --- Read page margins from document (EMU → cm) ---
    section = doc.sections[0]
    _emu_cm = 2.54 / 914400
    margin_top    = (section.top_margin    or 0) * _emu_cm
    margin_bottom = (section.bottom_margin or 0) * _emu_cm
    margin_left   = (section.left_margin   or 0) * _emu_cm
    margin_right  = (section.right_margin  or 0) * _emu_cm

    # --- Read style-level font sizes (EMU → pt) ---
    def _style_pt(style_name, fallback):
        try:
            fs = doc.styles[style_name].font.size
            if fs:
                return round(fs / 12700, 1)
        except Exception:
            pass
        return fallback

    h1_pt   = _style_pt('Heading 1', 26)
    h2_pt   = _style_pt('Heading 2', 14)
    body_pt = _style_pt('Normal',    11)

    def escape(t):
        return _html_lib.escape(t, quote=False)

    def run_html(run):
        text = escape(run.text)
        if not text:
            return ''
        # Collect inline styles for a single span
        inline = {}
        try:
            color = run.font.color.rgb
            if color:
                inline['color'] = f'#{color}'
        except Exception:
            pass
        try:
            if run.font.size:
                rpt = round(run.font.size / 12700, 1)
                if rpt != body_pt:
                    inline['font-size'] = f'{rpt}pt'
        except Exception:
            pass
        try:
            fn = run.font.name
            if fn:
                inline['font-family'] = fn
        except Exception:
            pass
        if inline:
            style_str = '; '.join(f'{k}:{v}' for k, v in inline.items())
            text = f'<span style="{style_str}">{text}</span>'
        if run.bold:
            text = f'<strong>{text}</strong>'
        if run.italic:
            text = f'<em>{text}</em>'
        return text

    _ALIGN_MAP = {
        WD_ALIGN_PARAGRAPH.CENTER:  'center',
        WD_ALIGN_PARAGRAPH.RIGHT:   'right',
        WD_ALIGN_PARAGRAPH.JUSTIFY: 'justify',
    }

    def para_html(para):
        # Gather text from direct runs + runs inside <w:hyperlink> elements
        parts = []
        for elem in para._p:
            local = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
            if local == 'r':
                from docx.text.run import Run as _Run
                parts.append(run_html(_Run(elem, para)))
            elif local == 'hyperlink':
                link_text = ''
                for child in elem:
                    child_local = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                    if child_local == 'r':
                        from docx.text.run import Run as _Run
                        link_text += escape(_Run(child, para).text)
                if link_text:
                    parts.append(f'<u>{link_text}</u>')

        inner = ''.join(parts)
        style_name = para.style.name
        is_bold = any(r.bold for r in para.runs)

        # Per-paragraph indentation and spacing read from XML / style chain
        left_pt, right_pt, first_pt = _para_ind(para)
        before_pt, after_pt, line_h  = _para_spacing(para)

        # Alignment: paragraph level first, then style fallback
        align = para.alignment
        if align is None:
            try:
                align = para.style.paragraph_format.alignment
            except Exception:
                pass

        def _style_attr(use_padding=False):
            """Build a style="..." attribute string from per-para layout values."""
            css = {}
            a = _ALIGN_MAP.get(align)
            if a:
                css['text-align'] = a
            if use_padding:
                # Hanging indent for bullets: padding-left holds the full block
                # indent; text-indent pulls the first line (bullet char) left.
                pl = left_pt if left_pt > 0.5 else 36.0
                ti = first_pt if first_pt else -18.0
                css['padding-left'] = f'{pl:.1f}pt'
                css['text-indent']  = f'{ti:.1f}pt'
            else:
                if left_pt > 0.5:
                    css['margin-left'] = f'{left_pt:.1f}pt'
                if right_pt > 0.5:
                    css['margin-right'] = f'{right_pt:.1f}pt'
                if first_pt:
                    css['text-indent'] = f'{first_pt:.1f}pt'
            if before_pt is not None:
                css['margin-top'] = f'{before_pt:.1f}pt'
            if after_pt is not None:
                css['margin-bottom'] = f'{after_pt:.1f}pt'
            if line_h is not None:
                css['line-height'] = (str(line_h) if isinstance(line_h, str)
                                      else f'{line_h:.3f}')
            return (' style="' + '; '.join(f'{k}:{v}' for k, v in css.items()) + '"'
                    if css else '')

        if not inner.strip():
            # Blank paragraph: render as a line-height spacer so vertical
            # spacing matches the source document rather than being discarded.
            return f'<p class="spacer"{_style_attr()}>&nbsp;</p>'

        if style_name in ('Title', 'Heading 1'):
            return f'<h1{_style_attr()}>{inner}</h1>'
        elif style_name == 'Heading 2':
            return f'<h2{_style_attr()}>{inner}</h2>'
        elif style_name == 'List Paragraph':
            # Explicit bullet glyph so it comes from the body font (Cambria).
            return f'<p class="bullet-item"{_style_attr(use_padding=True)}>&#x2022;&#x00A0;{inner}</p>'
        elif style_name == 'Body Text':
            return f'<p class="body-text"{_style_attr()}>{inner}</p>'
        elif style_name == 'Normal' and is_bold and '|' in para.text:
            return f'<p class="exp-header"{_style_attr()}>{inner}</p>'
        else:
            return f'<p{_style_attr()}>{inner}</p>'

    lines = []
    for para in doc.paragraphs:
        tag = para_html(para)
        if tag is not None:
            lines.append(tag)

    body = '\n'.join(lines)
    font_face_block = (font_face_css + '\n') if font_face_css else ''
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<style>
{font_face_block}@page {{ margin: {margin_top:.2f}cm {margin_right:.2f}cm {margin_bottom:.2f}cm {margin_left:.2f}cm; }}
body {{ font-family: Cambria, Georgia, serif; font-size: {body_pt}pt; margin: 0; color: #000; }}
h1 {{ font-family: Calibri, Arial, sans-serif; font-size: {h1_pt}pt; font-weight: bold; text-align: center; margin: 0; }}
h2 {{ font-family: Calibri, Arial, sans-serif; font-size: {h2_pt}pt; font-weight: bold; margin: 0; }}
p {{ margin: 0; line-height: 1.15; }}
p.spacer {{ line-height: 1.0; }}
p.body-text {{ }}
p.exp-header {{ font-family: Calibri, Arial, sans-serif; font-weight: bold; }}
p.bullet-item {{ padding-left: 36pt; text-indent: -18pt; }}
</style>
</head><body>
{body}
</body></html>"""


# Default Docker image for LibreOffice headless PDF conversion.
# After building Dockerfile.libreoffice, tailor.py auto-detects that image.
_DOCKER_IMAGE_DEFAULT = 'minidocks/libreoffice'


def _docx_to_pdf_docker(docx_path, docker_image=_DOCKER_IMAGE_DEFAULT):
    """Convert a .docx to .pdf using LibreOffice headless inside Docker.

    On Windows the host's Windows\\Fonts directory is bind-mounted read-only
    into the container so LibreOffice has access to Calibri, Cambria, and all
    other installed MS fonts, producing output that closely matches Word's own
    PDF export.

    Parameters
    ----------
    docx_path : str
        Path to the source .docx file.
    docker_image : str
        Docker image to run.  Defaults to ``minidocks/libreoffice`` (pulled
        automatically on first use).  For best font support, build the
        project's custom image and pass its name::

            docker build -f Dockerfile.libreoffice -t ai-resume-tailor-lo .
            docx_to_pdf(path, docker_image='ai-resume-tailor-lo')
    """
    import subprocess
    import platform
    import shlex

    docx_abs  = os.path.abspath(docx_path)
    docx_dir  = os.path.dirname(docx_abs)
    docx_name = os.path.basename(docx_abs)
    pdf_dest  = os.path.splitext(docx_abs)[0] + '.pdf'

    # Docker Desktop on Windows accepts forward-slash paths in volume specs.
    def _dp(p):
        return p.replace('\\', '/')

    volumes      = [f'{_dp(docx_dir)}:/data']
    mount_fonts  = False

    # Bind-mount the Windows Fonts folder so LibreOffice can use MS fonts.
    if platform.system() == 'Windows':
        fonts_dir = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts')
        if os.path.isdir(fonts_dir):
            volumes.append(f'{_dp(fonts_dir)}:/usr/local/share/fonts/windows:ro')
            mount_fonts = True

    cmd = ['docker', 'run', '--rm']
    for v in volumes:
        cmd += ['--volume', v]
    cmd.append(docker_image)

    # When Windows fonts are mounted we refresh the fontconfig cache first
    # so LibreOffice picks them up.  shlex.quote handles spaces in filenames.
    quoted = shlex.quote(f'/data/{docx_name}')
    if mount_fonts:
        cmd += ['sh', '-c',
                f'fc-cache -f 2>/dev/null || true && '
                f'libreoffice --headless --convert-to pdf {quoted} --outdir /data']
    else:
        cmd += ['libreoffice', '--headless', '--convert-to', 'pdf',
                f'/data/{docx_name}', '--outdir', '/data']

    print(f"Converting via LibreOffice Docker ({docker_image}) …")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise RuntimeError(
            "Docker executable not found.  Install Docker Desktop, ensure it is "
            "running, then retry — or use method='local' for the built-in converter."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice Docker conversion timed out after 180 s.")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '(no output)').strip()
        # Give a friendlier hint when the Docker daemon itself is not running.
        if 'error during connect' in detail or 'Cannot connect to the Docker daemon' in detail:
            raise RuntimeError(
                "Docker daemon is not running.  Start Docker Desktop and retry "
                "— or use method='local' for the built-in converter.\n\n"
                f"Docker said:\n{detail}"
            )
        raise RuntimeError(
            f"LibreOffice conversion failed (exit {result.returncode}):\n{detail}"
        )

    if not os.path.exists(pdf_dest):
        raise RuntimeError(
            f"Conversion appeared to succeed but PDF was not found at:\n{pdf_dest}"
        )

    print(f"PDF saved to {pdf_dest}")


def _docx_to_pdf_local(docx_path):
    """Convert a .docx to .pdf using xhtml2pdf (no external dependencies).

    This is the fallback method when Docker is unavailable.  Output fidelity
    is lower than the LibreOffice route — paragraph spacing and indentation
    are approximated from the docx XML, and font rendering depends on which
    system fonts are available to ReportLab.
    """
    docx_abs = os.path.abspath(docx_path)
    pdf_dest = os.path.splitext(docx_abs)[0] + '.pdf'

    font_face_css = _register_fonts()
    html_str = _docx_to_html(docx_abs, font_face_css=font_face_css)
    with open(pdf_dest, 'wb') as f:
        result = pisa.CreatePDF(html_str.encode('utf-8'), dest=f, encoding='utf-8')

    if result.err:
        raise RuntimeError(f"xhtml2pdf conversion failed with {result.err} error(s)")

    print(f"PDF saved to {pdf_dest}")


def docx_to_pdf(docx_path, method='docker', docker_image=_DOCKER_IMAGE_DEFAULT):
    """Convert a .docx to .pdf next to the source file.

    Parameters
    ----------
    docx_path : str
        Path to the source .docx file.
    method : {'docker', 'local'}
        ``'docker'`` (default) — high-fidelity conversion via LibreOffice
        headless in Docker.  Requires Docker Desktop to be running.  On
        first use, Docker pulls ``minidocks/libreoffice`` automatically.

        ``'local'`` — built-in Python conversion via xhtml2pdf; no external
        dependencies but formatting fidelity is lower.
    docker_image : str
        Docker image to use when *method* is ``'docker'``.  Defaults to
        ``minidocks/libreoffice``.  After building the project's custom
        image (``docker build -f Dockerfile.libreoffice -t ai-resume-tailor-lo .``),
        pass ``docker_image='ai-resume-tailor-lo'`` for better font support.
    """
    if method == 'docker':
        _docx_to_pdf_docker(docx_path, docker_image=docker_image)
    elif method == 'local':
        _docx_to_pdf_local(docx_path)
    else:
        raise ValueError(f"Unknown method {method!r}.  Use 'docker' or 'local'.")


# ---------------------------
# OpenAI Setup
# ---------------------------

load_dotenv()
client = OpenAI()


# ---------------------------
# Prompt loader
# ---------------------------

def _load_prompt(name, **kwargs):
    """Load prompts/<name>.txt and substitute {placeholder} values.

    Only tokens of the form ``{word}`` whose name appears in *kwargs* are
    replaced.  All other brace sequences (e.g. JSON examples in the prompt)
    are left exactly as written, so prompt files can contain literal JSON
    without any escaping.
    """
    import re
    prompts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prompts')
    path = os.path.join(prompts_dir, f'{name}.txt')
    try:
        with open(path, encoding='utf-8') as f:
            template = f.read()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"Prompt file not found: {path}\n"
            "Create the file or check the prompts/ directory."
        )
    return re.sub(r'\{(\w+)\}',
                  lambda m: str(kwargs[m.group(1)]) if m.group(1) in kwargs else m.group(0),
                  template)


# ---------------------------
# Step 1 — Extract Company & Role
# ---------------------------

def extract_metadata_ai(job_text):
    prompt = _load_prompt('extract_metadata', job_text=job_text)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)

def extract_metadata_from_html(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, "html.parser")

    # Find JSON-LD script
    script_tag = soup.find("script", type="application/ld+json")

    if not script_tag:
        return {"company": "Unknown", "job_title": "Unknown"}

    data = json.loads(script_tag.string)

    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")

    return {
        "company": company,
        "job_title": job_title
    }


def extract_job_data_from_html(html):
    soup = BeautifulSoup(html, "html.parser")

    script_tags = soup.find_all("script", type="application/ld+json")

    for tag in script_tags:
        try:
            data = json.loads(tag.string)

            # Sometimes JSON-LD is a list
            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "JobPosting":
                        return parse_jobposting(item)
            elif data.get("@type") == "JobPosting":
                return parse_jobposting(data)

        except Exception:
            continue

    return {
        "company": "Unknown",
        "job_title": "Unknown",
        "description": ""
    }


def parse_jobposting(data):
    company = data.get("hiringOrganization", {}).get("name", "Unknown")
    job_title = data.get("title", "Unknown")

    description_html = data.get("description", "")
    description_soup = BeautifulSoup(description_html, "html.parser")
    description_text = description_soup.get_text(separator="\n")

    return {
        "company": company,
        "job_title": job_title,
        "description": description_text
    }


# ---------------------------
# Step 2 — Tailor Resume + Cover
# ---------------------------

def tailor_documents(job_text, resume_template, cover_template, company, job_title):
    prompt = _load_prompt('tailor',
                          company=company,
                          job_title=job_title,
                          job_text=job_text,
                          resume_template=resume_template,
                          cover_template=cover_template)

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
        response_format={"type": "json_object"}
    )

    return json.loads(response.choices[0].message.content)


# ---------------------------
# Debug Autosave
# ---------------------------

def save_debug_data(company, job_title, job_description, llm_response):
    os.makedirs("tmp", exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_company = "".join(c if c.isalnum() or c in "_-" else "_" for c in company).strip("_")
    safe_title = "".join(c if c.isalnum() or c in "_-" else "_" for c in job_title).strip("_")
    filename = f"tmp/{safe_company}-{safe_title}-{timestamp}.json"

    data = {
        "company": company,
        "position": job_title,
        "job_description": job_description,
        "llm_response": llm_response,
    }

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Debug data saved to {filename}")


# ---------------------------
# Main
# ---------------------------

if __name__ == "__main__":

    job_url = input("Enter job URL (or press enter to paste text): ").strip()

    if job_url:
        html = get_rendered_html(job_url)
        job_data = extract_job_data_from_html(html)
    else:
        print("Paste job description (press Enter twice to finish):")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        job_text = "\n".join(lines)

    resume_template = read_docx("templates/Leonid_Verman_Resume_Template.docx")
    cover_template = read_docx("templates/Leonid_Verman_Cover_Letter_Template.docx")

    print("Extracting company & role...")

    company = job_data["company"]
    job_title = job_data["job_title"]
    job_text = job_data["description"]

    print(f"Detected Company: {company}")
    print(f"Detected Role: {job_title}")

    print("Tailoring documents...")
    result = tailor_documents(job_text, resume_template, cover_template, company, job_title)

    save_debug_data(company, job_title, job_text, result)

    os.makedirs("output", exist_ok=True)

    resume_template_path = "templates/Leonid_Verman_Resume_Template.docx"
    cover_template_path = "templates/Leonid_Verman_Cover_Letter_Template.docx"

    resume_docx = f"output/{company}_Resume.docx"
    cover_docx = f"output/{company}_CoverLetter.docx"

    if _confirm_overwrite(resume_docx):
        save_doc_from_template(resume_template_path, resume_docx, result["resume"])

    if _confirm_overwrite(cover_docx):
        save_doc_from_template(cover_template_path, cover_docx, result["cover_letter"])

    resume_pdf = os.path.splitext(resume_docx)[0] + ".pdf"
    if _confirm_overwrite(resume_pdf):
        docx_to_pdf(resume_docx)

    cover_pdf = os.path.splitext(cover_docx)[0] + ".pdf"
    if _confirm_overwrite(cover_pdf):
        docx_to_pdf(cover_docx)

    print("Documents generated successfully.")