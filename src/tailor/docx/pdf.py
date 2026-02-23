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


def _docx_to_html(docx_path, font_face_css=""):
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
    XML (falling back to the style chain) and applied as inline styles.
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
            return (
                ' style="' + "; ".join(f"{k}:{v}" for k, v in css.items()) + '"'
                if css else ""
            )

        if not inner.strip():
            return f'<p class="spacer"{_style_attr()}>&nbsp;</p>'

        if style_name in ("Title", "Heading 1"):
            return f"<h1{_style_attr()}>{inner}</h1>"
        elif style_name == "Heading 2":
            return f"<h2{_style_attr()}>{inner}</h2>"
        elif style_name == "List Paragraph":
            return f'<p class="bullet-item"{_style_attr(use_padding=True)}>&#x2022;&#x00A0;{inner}</p>'
        elif style_name == "Body Text":
            return f'<p class="body-text"{_style_attr()}>{inner}</p>'
        elif style_name == "Normal" and is_bold and "|" in para.text:
            return f'<p class="exp-header"{_style_attr()}>{inner}</p>'
        else:
            return f"<p{_style_attr()}>{inner}</p>"

    lines = []
    for para in doc.paragraphs:
        tag = para_html(para)
        if tag is not None:
            lines.append(tag)

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
    method : {'docker', 'local'}
        ``'docker'`` (default) — high-fidelity conversion via LibreOffice
        headless in Docker.  Requires Docker Desktop to be running.

        ``'local'`` — built-in Python conversion via xhtml2pdf; no external
        dependencies but formatting fidelity is lower.
    docker_image : str
        Docker image to use when *method* is ``'docker'``.
    """
    if method == "docker":
        _docx_to_pdf_docker(docx_path, docker_image=docker_image)
    elif method == "local":
        _docx_to_pdf_local(docx_path)
    else:
        raise ValueError(f"Unknown method {method!r}.  Use 'docker' or 'local'.")
