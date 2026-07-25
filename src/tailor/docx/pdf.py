"""PDF conversion from .docx files.

Two strategies are supported:
  - 'docker'  — high-fidelity via LibreOffice headless inside Docker
  - 'local'   — built-in Python conversion via xhtml2pdf (lower fidelity)
"""

import html as _html_lib
import os

from docx import Document
from xhtml2pdf import pisa

from tailor.config import DOCKER_IMAGE_DEFAULT
from tailor.docx import _W


# ---------------------------------------------------------------------------
# Paragraph style introspection helpers (used for HTML rendering)
# ---------------------------------------------------------------------------

def _para_ind(para):
    """Read effective paragraph indentation (paragraph XML then style chain), in points.

    Returns (left_pt, right_pt, first_pt) where first_pt is negative for a
    hanging indent and positive for a first-line indent.  All values default
    to 0.0 when not found anywhere in the style chain.
    """
    def _tw(ind_el, a):
        v = ind_el.get(f"{{{_W}}}{a}")
        try:
            return int(v) / 20.0
        except (TypeError, ValueError):
            return 0.0

    def _read(pPr_el):
        if pPr_el is None:
            return None
        ind = pPr_el.find(f"{{{_W}}}ind")
        if ind is None:
            return None   # no ind element → keep searching style chain
        left    = _tw(ind, "left")
        right   = _tw(ind, "right")
        hanging = _tw(ind, "hanging")
        first   = _tw(ind, "firstLine")
        if hanging > 0:
            first = -hanging
        return (left, right, first)

    r = _read(para._p.find(f"{{{_W}}}pPr"))
    if r is not None:
        return r
    style = para.style
    while style is not None:
        try:
            r = _read(style.element.find(f"{{{_W}}}pPr"))
            if r is not None:
                return r
        except Exception:
            pass
        style = getattr(style, "base_style", None)
    return (0.0, 0.0, 0.0)


def _para_spacing(para):
    """Read effective paragraph spacing (paragraph XML then style chain).

    Returns (before_pt, after_pt, line_height) where before_pt / after_pt are
    floats in points (or None when absent) and line_height is either a float
    multiplier (e.g. 1.15) or a CSS string like '14.0pt' (or None when absent).
    """
    before = [None]
    after  = [None]
    lh     = [None]

    def _apply(pPr_el):
        if pPr_el is None:
            return
        sp = pPr_el.find(f"{{{_W}}}spacing")
        if sp is None:
            return

        def _tw(a):
            v = sp.get(f"{{{_W}}}{a}")
            try:
                return int(v) / 20.0
            except (TypeError, ValueError):
                return None

        if before[0] is None:
            before[0] = _tw("before")
        if after[0] is None:
            after[0] = _tw("after")
        if lh[0] is None:
            lv_s = sp.get(f"{{{_W}}}line")
            lr_s = sp.get(f"{{{_W}}}lineRule")
            if lv_s:
                try:
                    lv = int(lv_s)
                    if lr_s in (None, "auto"):
                        # auto: lv is in 1/240ths of a single-spaced line
                        lh[0] = round(lv / 240.0, 3)
                    elif lr_s == "exact":
                        # exact: lv is in twips → convert to pt
                        lh[0] = f"{lv / 20.0:.1f}pt"
                    # atLeast: minimum constraint — let renderer use natural line height
                except (TypeError, ValueError):
                    pass

    _apply(para._p.find(f"{{{_W}}}pPr"))
    style = para.style
    while style is not None and (before[0] is None or after[0] is None or lh[0] is None):
        try:
            _apply(style.element.find(f"{{{_W}}}pPr"))
        except Exception:
            pass
        style = getattr(style, "base_style", None)
    return (before[0], after[0], lh[0])


def _get_cell_bg(tc_elem) -> str | None:
    """Return fill color (6-char hex RRGGBB) from a w:tc element, or None."""
    tcPr = tc_elem.find(f"{{{_W}}}tcPr")
    if tcPr is None:
        return None
    shd = tcPr.find(f"{{{_W}}}shd")
    if shd is None:
        return None
    fill = shd.get(f"{{{_W}}}fill", "")
    if fill and fill.lower() not in ("auto", "none") and len(fill) == 6:
        return fill
    return None


def _get_tbl_col_widths_twips(tbl_elem) -> list[int]:
    """Return per-column widths in twips from a w:tbl element's w:tblGrid."""
    tblGrid = tbl_elem.find(f"{{{_W}}}tblGrid")
    if tblGrid is None:
        return []
    return [
        int(col.get(f"{{{_W}}}w", "1440"))
        for col in tblGrid.findall(f"{{{_W}}}gridCol")
    ]


def _docx_to_html(docx_path, font_face_css=""):
    """Convert a .docx to an HTML string, preserving template paragraph styles.

    Maps the template's named styles to semantic HTML + CSS:
      Title / Heading 1  → <h1>  (name line, centered)
      Heading 2          → <h2>  (section headers)
      Normal + bold      → <p class="exp-header">  (experience entry headers)
      Body Text          → <p class="body-text">   (dates, contact)
      List Paragraph / ListBullet → <p class="bullet-item"> (bullet points)
      Normal             → <p>
    Run-level bold/italic/color/size is preserved.
    Paragraph alignment, indentation, and spacing are read from the paragraph
    XML (falling back to the style chain) and applied as inline styles.
    Table elements (e.g. two-column PDF-sourced layout) are rendered as HTML
    tables so their content is not silently dropped.
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

    h1_pt   = _style_pt("Heading 1", 26)
    h2_pt   = _style_pt("Heading 2", 14)
    body_pt = _style_pt("Normal",    11)

    def escape(t):
        return _html_lib.escape(t, quote=False)

    def run_html(run):
        text = escape(run.text)
        if not text:
            return ""
        inline = {}
        try:
            color = run.font.color.rgb
            if color:
                inline["color"] = f"#{color}"
        except Exception:
            pass
        try:
            if run.font.size:
                rpt = round(run.font.size / 12700, 1)
                if rpt != body_pt:
                    inline["font-size"] = f"{rpt}pt"
        except Exception:
            pass
        try:
            fn = run.font.name
            if fn:
                inline["font-family"] = fn
        except Exception:
            pass
        if inline:
            style_str = "; ".join(f"{k}:{v}" for k, v in inline.items())
            text = f'<span style="{style_str}">{text}</span>'
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        return text

    _ALIGN_MAP = {
        WD_ALIGN_PARAGRAPH.CENTER:  "center",
        WD_ALIGN_PARAGRAPH.RIGHT:   "right",
        WD_ALIGN_PARAGRAPH.JUSTIFY: "justify",
    }

    def para_html(para):
        # Gather text from direct runs + runs inside <w:hyperlink> elements
        parts = []
        for elem in para._p:
            local = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
            if local == "r":
                from docx.text.run import Run as _Run
                parts.append(run_html(_Run(elem, para)))
            elif local == "hyperlink":
                link_text = ""
                for child in elem:
                    child_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if child_local == "r":
                        from docx.text.run import Run as _Run
                        link_text += escape(_Run(child, para).text)
                if link_text:
                    parts.append(f"<u>{link_text}</u>")

        inner = "".join(parts)
        style_name = para.style.name
        is_bold = any(r.bold for r in para.runs)

        left_pt, right_pt, first_pt = _para_ind(para)
        before_pt, after_pt, line_h  = _para_spacing(para)

        align = para.alignment
        if align is None:
            try:
                align = para.style.paragraph_format.alignment
            except Exception:
                pass

        # Paragraph background shading (used by PDF-sourced DOCX paragraphs)
        para_bg: str | None = None
        try:
            pPr = para._p.find(f"{{{_W}}}pPr")
            if pPr is not None:
                shd = pPr.find(f"{{{_W}}}shd")
                if shd is not None:
                    fill = shd.get(f"{{{_W}}}fill", "")
                    if fill and fill.lower() not in ("auto", "none") and len(fill) == 6:
                        para_bg = fill
        except Exception:
            pass

        def _style_attr(use_padding=False):
            css = {}
            a = _ALIGN_MAP.get(align)
            if a:
                css["text-align"] = a
            if use_padding:
                pl = left_pt if left_pt > 0.5 else 36.0
                ti = first_pt if first_pt else -18.0
                css["padding-left"] = f"{pl:.1f}pt"
                css["text-indent"]  = f"{ti:.1f}pt"
            else:
                if left_pt > 0.5:
                    css["margin-left"] = f"{left_pt:.1f}pt"
                if right_pt > 0.5:
                    css["margin-right"] = f"{right_pt:.1f}pt"
                if first_pt:
                    css["text-indent"] = f"{first_pt:.1f}pt"
            if before_pt is not None:
                css["margin-top"] = f"{before_pt:.1f}pt"
            if after_pt is not None:
                css["margin-bottom"] = f"{after_pt:.1f}pt"
            if line_h is not None:
                css["line-height"] = (
                    str(line_h) if isinstance(line_h, str) else f"{line_h:.3f}"
                )
            if para_bg:
                css["background-color"] = f"#{para_bg}"
            return (
                ' style="' + "; ".join(f"{k}:{v}" for k, v in css.items()) + '"'
                if css else ""
            )

        if not inner.strip():
            return f'<p class="spacer"{_style_attr()}>&nbsp;</p>'

        # "list" or "bullet" anywhere in the style name → render as bullet item
        sn_lower = style_name.lower()
        if style_name in ("Title", "Heading 1"):
            return f"<h1{_style_attr()}>{inner}</h1>"
        elif style_name == "Heading 2":
            return f"<h2{_style_attr()}>{inner}</h2>"
        elif "list" in sn_lower or "bullet" in sn_lower:
            return f'<p class="bullet-item"{_style_attr(use_padding=True)}>&#x2022;&#x00A0;{inner}</p>'
        elif style_name == "Body Text":
            return f'<p class="body-text"{_style_attr()}>{inner}</p>'
        elif style_name == "Normal" and is_bold and "|" in para.text:
            return f'<p class="exp-header"{_style_attr()}>{inner}</p>'
        else:
            return f"<p{_style_attr()}>{inner}</p>"

    def _render_table_html(tbl_elem, out_lines):
        """Render a w:tbl lxml element as an HTML table into out_lines."""
        from docx.text.paragraph import Paragraph as _Paragraph

        col_twips = _get_tbl_col_widths_twips(tbl_elem)
        total_twips = sum(col_twips) if col_twips else 1

        out_lines.append(
            '<table style="width:100%;border-collapse:collapse;border:none" '
            'cellpadding="0" cellspacing="0">'
        )
        if col_twips:
            out_lines.append("<colgroup>")
            for cw in col_twips:
                pct = cw / total_twips * 100
                out_lines.append(f'<col style="width:{pct:.1f}%">')
            out_lines.append("</colgroup>")

        for tr in tbl_elem.findall(f"{{{_W}}}tr"):
            out_lines.append("<tr>")
            for tc in tr.findall(f"{{{_W}}}tc"):
                bg = _get_cell_bg(tc)
                td_style = "vertical-align:top;padding:0 3pt;"
                if bg:
                    td_style += f"background-color:#{bg};"
                out_lines.append(f'<td style="{td_style}">')
                for child in tc:
                    c_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if c_local == "p":
                        tag = para_html(_Paragraph(child, doc))
                        if tag is not None:
                            out_lines.append(tag)
                    elif c_local == "tbl":
                        _render_table_html(child, out_lines)
                out_lines.append("</td>")
            out_lines.append("</tr>")

        out_lines.append("</table>")

    # Iterate body children in document order so tables are not skipped.
    # doc.paragraphs only exposes top-level paragraphs and misses table cells.
    lines = []
    from docx.text.paragraph import Paragraph as _Paragraph
    for child in doc.element.body:
        c_local = child.tag.split("}")[-1] if "}" in child.tag else child.tag
        if c_local == "p":
            tag = para_html(_Paragraph(child, doc))
            if tag is not None:
                lines.append(tag)
        elif c_local == "tbl":
            _render_table_html(child, lines)

    body = "\n".join(lines)
    font_face_block = (font_face_css + "\n") if font_face_css else ""
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


# ---------------------------------------------------------------------------
# Windows font patch for xhtml2pdf
# ---------------------------------------------------------------------------

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
    import sys
    import tempfile
    from pathlib import Path
    from urllib.parse import urlparse

    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    if not os.path.isdir(fonts_dir):
        return ""

    # ── Patch 1: LocalProtocolURI.extract_data ──────────────────────────────
    try:
        import xhtml2pdf.files as _xf

        def _win_lp_extract_data(self):
            parsed = urlparse(self.path or "")
            if parsed.scheme == "file":
                p = parsed.path          # '/C:/Windows/...' on Windows
                if p.startswith("/") and len(p) >= 3 and p[2] == ":":
                    p = p[1:]            # strip leading slash → 'C:/Windows/...'
                fpath = Path(p)
                if fpath.is_file():
                    self.uri = fpath
                    self.suffix = fpath.suffix
                    with open(fpath, "rb") as fh:
                        return fh.read()
            return None

        _xf.LocalProtocolURI.extract_data = _win_lp_extract_data

        # ── Patch 2: BaseFile.get_named_tmp_file ────────────────────────────
        def _win_get_named_tmp_file(self):
            data = self.get_data()
            fd, name = tempfile.mkstemp(suffix=self.suffix or ".tmp")
            try:
                if data:
                    os.write(fd, data)
            finally:
                os.close(fd)        # close fd so other processes can read

            class _TmpProxy:
                def __init__(self, path): self.name = path
                def close(self):
                    try:
                        os.unlink(self.name)
                    except OSError:
                        pass
                def __del__(self): self.close()

            proxy = _TmpProxy(name)
            _xf.files_tmp.append(proxy)
            if self.path is None:
                self.path = name
            return proxy

        _xf.BaseFile.get_named_tmp_file = _win_get_named_tmp_file

    except Exception:
        return ""   # xhtml2pdf unavailable or patching failed – no custom fonts

    # ── Build @font-face CSS for available font files ───────────────────────
    fdir = fonts_dir.replace("\\", "/")

    font_specs = [
        ("Calibri", [
            ("calibri.ttf",  "normal", "normal"),
            ("calibrib.ttf", "bold",   "normal"),
            ("calibrii.ttf", "normal", "italic"),
            ("calibriz.ttf", "bold",   "italic"),
        ]),
        ("Cambria", [
            ("cambria.ttc",  "normal", "normal"),
            ("cambriab.ttf", "bold",   "normal"),
            ("cambriai.ttf", "normal", "italic"),
            ("cambriaz.ttf", "bold",   "italic"),
        ]),
    ]

    css_lines = []
    for family, variants in font_specs:
        for fname, weight, style in variants:
            fpath = os.path.join(fonts_dir, fname)
            if os.path.exists(fpath):
                url = f"file:///{fdir}/{fname}"
                css_lines.append(
                    f"@font-face {{ font-family: {family}; "
                    f'src: url("{url}"); '
                    f"font-weight: {weight}; font-style: {style}; }}"
                )

    return "\n".join(css_lines)


# ---------------------------------------------------------------------------
# Conversion back-ends
# ---------------------------------------------------------------------------

def _docx_to_pdf_docker(docx_path, docker_image=DOCKER_IMAGE_DEFAULT):
    """Convert a .docx to .pdf using LibreOffice headless inside Docker.

    On Windows the host's Windows\\Fonts directory is bind-mounted read-only
    into the container so LibreOffice has access to Calibri, Cambria, and all
    other installed MS fonts.

    Parameters
    ----------
    docx_path : str
        Path to the source .docx file.
    docker_image : str
        Docker image to run.  Defaults to ``minidocks/libreoffice``.
        For best font support, build the project's custom image::

            docker build -f Dockerfile.libreoffice -t ai-resume-tailor-lo .
            docx_to_pdf(path, docker_image='ai-resume-tailor-lo')
    """
    import platform
    import shlex
    import subprocess

    docx_abs  = os.path.abspath(docx_path)
    docx_dir  = os.path.dirname(docx_abs)
    docx_name = os.path.basename(docx_abs)
    pdf_dest  = os.path.splitext(docx_abs)[0] + ".pdf"

    def _dp(p):
        return p.replace("\\", "/")

    volumes     = [f"{_dp(docx_dir)}:/data"]
    mount_fonts = False

    if platform.system() == "Windows":
        fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        if os.path.isdir(fonts_dir):
            volumes.append(f"{_dp(fonts_dir)}:/usr/local/share/fonts/windows:ro")
            mount_fonts = True

    cmd = ["docker", "run", "--rm"]
    for v in volumes:
        cmd += ["--volume", v]
    cmd.append(docker_image)

    quoted = shlex.quote(f"/data/{docx_name}")
    if mount_fonts:
        cmd += [
            "sh", "-c",
            f"fc-cache -f 2>/dev/null || true && "
            f"libreoffice --headless --convert-to pdf {quoted} --outdir /data",
        ]
    else:
        cmd += ["libreoffice", "--headless", "--convert-to", "pdf",
                f"/data/{docx_name}", "--outdir", "/data"]

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
        detail = (result.stderr or result.stdout or "(no output)").strip()
        if "error during connect" in detail or "Cannot connect to the Docker daemon" in detail:
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


def _find_libreoffice_exe() -> str:
    """Return the LibreOffice executable path, checking PATH and Windows defaults."""
    import platform
    import shutil

    # Check PATH first (covers Linux/macOS and Windows if in PATH)
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found

    # Windows common installation paths
    if platform.system() == "Windows":
        for base in (
            r"C:\Program Files\LibreOffice\program",
            r"C:\Program Files (x86)\LibreOffice\program",
        ):
            candidate = os.path.join(base, "soffice.exe")
            if os.path.exists(candidate):
                return candidate

    return "libreoffice"  # fallback — will raise FileNotFoundError if absent


def _docx_to_pdf_subprocess(docx_path):
    """Convert a .docx to .pdf using LibreOffice headless as a local subprocess.

    LibreOffice must be installed (in PATH or at the standard Windows location).
    Produces a .pdf file next to the source DOCX.
    """
    import subprocess
    import tempfile
    import uuid
    from pathlib import Path

    docx_abs = os.path.abspath(docx_path)
    out_dir   = os.path.dirname(docx_abs)
    pdf_dest  = os.path.splitext(docx_abs)[0] + ".pdf"

    lo_exe = _find_libreoffice_exe()
    # Each invocation gets its own user-profile directory so that concurrent
    # requests (e.g. multiple FastAPI workers) don't share the ~/.config/libreoffice
    # lock and silently drop conversions.
    # Path.as_uri() produces the correct file:/// URI on both Windows and Linux.
    profile_dir = os.path.join(tempfile.gettempdir(), f"lo_profile_{uuid.uuid4().hex}")
    profile_uri = Path(profile_dir).as_uri()
    cmd = [
        lo_exe, "--headless",
        f"-env:UserInstallation={profile_uri}",
        "--convert-to", "pdf",
        docx_abs,
        "--outdir", out_dir,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(
            "LibreOffice executable not found. Install LibreOffice and ensure "
            "it is available in PATH — or use method='local' for the built-in converter."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice DOCX→PDF conversion timed out after 120 s.")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "(no output)").strip()
        raise RuntimeError(
            f"LibreOffice DOCX→PDF conversion failed (exit {result.returncode}):\n{detail}"
        )

    if not os.path.exists(pdf_dest):
        raise RuntimeError(
            f"Conversion appeared to succeed but PDF was not found at:\n{pdf_dest}"
        )


def _docx_to_pdf_local(docx_path):
    """Convert a .docx to .pdf using xhtml2pdf (no external dependencies).

    This is the fallback method when Docker is unavailable.  Output fidelity
    is lower than the LibreOffice route.
    """
    docx_abs = os.path.abspath(docx_path)
    pdf_dest = os.path.splitext(docx_abs)[0] + ".pdf"

    font_face_css = _register_fonts()
    html_str = _docx_to_html(docx_abs, font_face_css=font_face_css)
    with open(pdf_dest, "wb") as f:
        result = pisa.CreatePDF(html_str.encode("utf-8"), dest=f, encoding="utf-8")

    if result.err:
        raise RuntimeError(f"xhtml2pdf conversion failed with {result.err} error(s)")

    print(f"PDF saved to {pdf_dest}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def docx_to_pdf(docx_path, method="docker", docker_image=DOCKER_IMAGE_DEFAULT):
    """Convert a .docx to .pdf next to the source file.

    Parameters
    ----------
    docx_path : str
        Path to the source .docx file.
    method : {'docker', 'subprocess', 'local'}
        ``'docker'`` (default) — high-fidelity conversion via LibreOffice
        headless in Docker.  Requires Docker Desktop to be running.

        ``'subprocess'`` — call ``libreoffice`` directly as a subprocess;
        requires LibreOffice installed in PATH (available in the backend
        container).

        ``'local'`` — built-in Python conversion via xhtml2pdf; no external
        dependencies but formatting fidelity is lower.
    docker_image : str
        Docker image to use when *method* is ``'docker'``.
    """
    if method == "docker":
        _docx_to_pdf_docker(docx_path, docker_image=docker_image)
    elif method == "subprocess":
        _docx_to_pdf_subprocess(docx_path)
    elif method == "local":
        _docx_to_pdf_local(docx_path)
    else:
        raise ValueError(f"Unknown method {method!r}.  Use 'docker', 'subprocess', or 'local'.")


# ---------------------------------------------------------------------------
# PDF → DOCX conversion
# ---------------------------------------------------------------------------

def _pdf_to_docx_subprocess(pdf_path):
    """Convert a .pdf to .docx using libreoffice subprocess.

    LibreOffice must be installed on the host system (available in PATH).
    Produces a .docx file next to the source PDF.

    Parameters
    ----------
    pdf_path : str
        Absolute path to the source .pdf file.

    Returns
    -------
    str
        Path to the generated .docx file.

    Raises
    ------
    RuntimeError
        If libreoffice is not found, times out, or conversion fails.
    """
    import subprocess

    pdf_abs = os.path.abspath(pdf_path)
    out_dir = os.path.dirname(pdf_abs)
    docx_dest = os.path.splitext(pdf_abs)[0] + ".docx"

    # --infilter forces LibreOffice to open the PDF as a Writer document
    # (without it, LibreOffice opens PDFs as Draw documents, which cannot
    # be exported to DOCX format).  The filter name must be quoted as a
    # single argument — no space between flag and value.
    cmd = [
        "libreoffice", "--headless",
        "--infilter=writer_pdf_import",
        "--convert-to", "docx:MS Word 2007 XML",
        pdf_abs,
        "--outdir", out_dir,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(
            "LibreOffice executable not found.  Install LibreOffice and ensure "
            "it is available in PATH — or use method='docker' for Docker-based conversion."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice PDF→DOCX conversion timed out after 120 s.")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "(no output)").strip()
        raise RuntimeError(
            f"LibreOffice PDF→DOCX conversion failed (exit {result.returncode}):\n{detail}"
        )

    if not os.path.exists(docx_dest):
        raise RuntimeError(
            f"Conversion appeared to succeed but DOCX was not found at:\n{docx_dest}"
        )

    return docx_dest


def _pdf_to_docx_docker(pdf_path, docker_image=DOCKER_IMAGE_DEFAULT):
    """Convert a .pdf to .docx using LibreOffice headless inside Docker.

    Mirrors the approach used by _docx_to_pdf_docker().

    Parameters
    ----------
    pdf_path : str
        Path to the source .pdf file.
    docker_image : str
        Docker image to run (must have LibreOffice installed).

    Returns
    -------
    str
        Path to the generated .docx file.
    """
    import platform
    import shlex
    import subprocess

    pdf_abs  = os.path.abspath(pdf_path)
    pdf_dir  = os.path.dirname(pdf_abs)
    pdf_name = os.path.basename(pdf_abs)
    docx_dest = os.path.splitext(pdf_abs)[0] + ".docx"

    def _dp(p):
        return p.replace("\\", "/")

    volumes = [f"{_dp(pdf_dir)}:/data"]

    cmd = ["docker", "run", "--rm"]
    for v in volumes:
        cmd += ["--volume", v]
    cmd.append(docker_image)

    quoted = shlex.quote(f"/data/{pdf_name}")
    cmd += [
        "libreoffice", "--headless",
        "--infilter=writer_pdf_import",
        "--convert-to", "docx:MS Word 2007 XML",
        f"/data/{pdf_name}", "--outdir", "/data",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except FileNotFoundError:
        raise RuntimeError(
            "Docker executable not found.  Install Docker Desktop, ensure it is "
            "running, then retry — or use method='subprocess' for direct LibreOffice."
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError("LibreOffice Docker PDF→DOCX conversion timed out after 180 s.")

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "(no output)").strip()
        raise RuntimeError(
            f"LibreOffice Docker PDF→DOCX conversion failed (exit {result.returncode}):\n{detail}"
        )

    if not os.path.exists(docx_dest):
        raise RuntimeError(
            f"Conversion appeared to succeed but DOCX was not found at:\n{docx_dest}"
        )

    return docx_dest


def pdf_to_docx(pdf_path, method="subprocess", docker_image=DOCKER_IMAGE_DEFAULT):
    """Convert a .pdf to .docx next to the source file.

    Parameters
    ----------
    pdf_path : str
        Path to the source .pdf file.
    method : {'subprocess', 'docker'}
        ``'subprocess'`` (default) — call ``libreoffice`` directly; requires
        LibreOffice to be installed on the host system.

        ``'docker'`` — LibreOffice headless inside Docker; portable but
        requires Docker Desktop to be running.
    docker_image : str
        Docker image to use when *method* is ``'docker'``.

    Returns
    -------
    str
        Path to the generated .docx file (next to the source PDF).
    """
    if method == "subprocess":
        docx_path = _pdf_to_docx_subprocess(pdf_path)
    elif method == "docker":
        docx_path = _pdf_to_docx_docker(pdf_path, docker_image=docker_image)
    else:
        raise ValueError(f"Unknown method {method!r}.  Use 'subprocess' or 'docker'.")

    from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
    return sanitize_docx_artifacts(docx_path)
