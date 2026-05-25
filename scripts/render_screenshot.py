#!/usr/bin/env python3
"""Side-by-side screenshot utility for rendered resume documents.

Produces a single PNG with:
  LEFT  = original template first page
  RIGHT = rendered/generated document first page

Both pages are scaled to the same height (preserving aspect ratio).
A small label is printed above each half.

Supported input formats: DOCX, PDF.
DOCX inputs are converted to PDF via LibreOffice before rendering.

CLI usage
---------
python scripts/render_screenshot.py \\
    --original tests/samples/resume/docx/16-Devops-Engineer.docx \\
    --rendered  tmp/artefacts/rendering/docx/16-Devops-Engineer.docx \\
    --out       tmp/artefacts/rendering/docx/screenshots/16-Devops-Engineer.png

python scripts/render_screenshot.py \\
    --original tests/samples/resume/pfd/16-Devops-Engineer.pdf \\
    --rendered  tmp/artefacts/rendering/pdf/16-Devops-Engineer.pdf \\
    --out       tmp/artefacts/rendering/pdf/screenshots/16-Devops-Engineer.png
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PDF page → PIL Image
# ---------------------------------------------------------------------------

def _pdf_first_page_to_image(pdf_path: Path, zoom: float = 2.0):
    """Render first page of a PDF as a PIL Image using PyMuPDF.

    Parameters
    ----------
    pdf_path:
        Path to the PDF file.
    zoom:
        Rendering zoom factor (e.g. 2 → 2× physical pixel density).
        Use 2–3 for readable screenshots.

    Returns
    -------
    PIL.Image.Image
        RGBA image of the first page.
    """
    import fitz  # PyMuPDF
    from PIL import Image
    import io

    doc = fitz.open(str(pdf_path))
    if doc.page_count == 0:
        raise ValueError(f"PDF has no pages: {pdf_path}")
    page = doc[0]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    img_bytes = pix.tobytes("png")
    doc.close()
    return Image.open(io.BytesIO(img_bytes)).convert("RGB")


# ---------------------------------------------------------------------------
# DOCX → PDF (temp) → PIL Image
# ---------------------------------------------------------------------------

def _docx_to_temp_pdf(docx_path: Path) -> Path:
    """Convert a DOCX to a temporary PDF using LibreOffice subprocess.

    Returns the path to the generated PDF (inside a temp directory).
    The caller is responsible for cleaning up the temp directory.
    """
    import sys
    _repo = Path(__file__).resolve().parents[1]
    if str(_repo / "src") not in sys.path:
        sys.path.insert(0, str(_repo / "src"))

    from tailor.docx.pdf import _docx_to_pdf_subprocess

    tmp_dir = Path(tempfile.mkdtemp(prefix="render_screenshot_"))
    # Copy DOCX into temp dir so LibreOffice writes the PDF next to it
    tmp_docx = tmp_dir / docx_path.name
    shutil.copy2(str(docx_path), str(tmp_docx))

    _docx_to_pdf_subprocess(str(tmp_docx))

    tmp_pdf = tmp_docx.with_suffix(".pdf")
    if not tmp_pdf.exists():
        raise RuntimeError(
            f"LibreOffice did not produce a PDF for {docx_path.name}"
        )
    return tmp_pdf


def _doc_to_image(path: Path, zoom: float = 2.0):
    """Convert any supported document (DOCX or PDF) to a PIL Image of page 1.

    DOCX files are first converted to a temp PDF via LibreOffice.

    Parameters
    ----------
    path:
        Path to a .docx or .pdf file.
    zoom:
        Rendering zoom factor passed to PyMuPDF.

    Returns
    -------
    PIL.Image.Image
        RGB image of the first page.
    """
    suffix = path.suffix.lower()
    tmp_dir: Optional[Path] = None

    try:
        if suffix == ".pdf":
            pdf_path = path
        elif suffix == ".docx":
            pdf_path = _docx_to_temp_pdf(path)
            tmp_dir = pdf_path.parent
        else:
            raise ValueError(f"Unsupported file type {suffix!r}; expected .docx or .pdf")

        return _pdf_first_page_to_image(pdf_path, zoom=zoom)
    finally:
        if tmp_dir is not None and tmp_dir.exists():
            shutil.rmtree(str(tmp_dir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Side-by-side comparison image
# ---------------------------------------------------------------------------

def make_comparison(
    original_path: Path,
    rendered_path: Path,
    out_path: Path,
    zoom: float = 2.0,
    gap: int = 12,
    label_height: int = 28,
    background: tuple[int, int, int] = (245, 245, 245),
) -> None:
    """Create a side-by-side comparison PNG from two documents.

    Parameters
    ----------
    original_path:
        Original template (.docx or .pdf).
    rendered_path:
        Rendered/generated output (.docx or .pdf).
    out_path:
        Destination PNG path.
    zoom:
        PyMuPDF zoom factor for rendering (default 2 = 2× resolution).
    gap:
        Horizontal gap in pixels between the two halves.
    label_height:
        Pixel height reserved above each half for the text label.
    background:
        RGB background colour tuple (default light grey).
    """
    from PIL import Image, ImageDraw, ImageFont

    # Render both pages
    left_img = _doc_to_image(original_path, zoom=zoom)
    right_img = _doc_to_image(rendered_path, zoom=zoom)

    # Scale both to the same height
    target_h = max(left_img.height, right_img.height)

    def _scale_to_height(img: Image.Image, h: int) -> Image.Image:
        if img.height == h:
            return img
        w = round(img.width * h / img.height)
        return img.resize((w, h), Image.LANCZOS)

    left_img = _scale_to_height(left_img, target_h)
    right_img = _scale_to_height(right_img, target_h)

    # Canvas dimensions
    canvas_w = left_img.width + gap + right_img.width
    canvas_h = target_h + label_height

    canvas = Image.new("RGB", (canvas_w, canvas_h), background)
    draw = ImageDraw.Draw(canvas)

    # Paste pages below labels
    canvas.paste(left_img,  (0, label_height))
    canvas.paste(right_img, (left_img.width + gap, label_height))

    # Draw labels
    try:
        font = ImageFont.truetype("arial.ttf", size=max(14, label_height - 8))
    except (IOError, OSError):
        font = ImageFont.load_default()

    label_colour = (60, 60, 60)
    draw.text((left_img.width // 2, label_height // 2), "Original",
              fill=label_colour, font=font, anchor="mm")
    draw.text((left_img.width + gap + right_img.width // 2, label_height // 2), "Rendered",
              fill=label_colour, font=font, anchor="mm")

    # Draw a thin separator line in the gap
    if gap >= 4:
        sep_x = left_img.width + gap // 2
        draw.line([(sep_x, 0), (sep_x, canvas_h)], fill=(200, 200, 200), width=2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(str(out_path), format="PNG", optimize=True)
    _log.debug("screenshot saved: %s", out_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a side-by-side comparison screenshot for a rendered resume.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--original", required=True, type=Path,
                   help="Original template (.docx or .pdf).")
    p.add_argument("--rendered", required=True, type=Path,
                   help="Rendered/generated output (.docx or .pdf).")
    p.add_argument("--out", required=True, type=Path,
                   help="Output PNG path.")
    p.add_argument("--zoom", type=float, default=2.0,
                   help="PyMuPDF rendering zoom (default 2.0).")
    p.add_argument("--gap", type=int, default=12,
                   help="Pixel gap between the two halves (default 12).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _parse_args(argv)

    original = args.original
    rendered = args.rendered

    for p in (original, rendered):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            return 1

    try:
        make_comparison(
            original_path=original,
            rendered_path=rendered,
            out_path=args.out,
            zoom=args.zoom,
            gap=args.gap,
        )
        print(f"Screenshot saved: {args.out}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
