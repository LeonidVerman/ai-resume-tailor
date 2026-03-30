"""Render PDF pages to PNG and produce side-by-side / diff images.

Phase 1 artifacts:
  source_page_N.png        — rendered source PDF page
  output_page_N.png        — rendered output PDF page
  diff_page_N.png          — pixel-level difference highlight
  annotated_side_by_side_page_N.png  — source | output with labels
"""
from __future__ import annotations

import os

_DEFAULT_DPI = 200
_SEPARATOR_WIDTH = 6   # px between source and output in side-by-side


def _render_page_pil(pdf_path: str, page_number: int, dpi: int):
    """Return a PIL Image of *page_number* (1-based) from *pdf_path*."""
    import fitz
    from PIL import Image
    import io

    doc = fitz.open(pdf_path)
    page = doc[page_number - 1]
    mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
    doc.close()
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _make_diff_image(src_img, out_img):
    """Return a PIL Image highlighting pixel differences between src and out."""
    from PIL import Image, ImageChops, ImageFilter
    import numpy as np

    # Resize to the larger of the two for fair comparison
    w = max(src_img.width, out_img.width)
    h = max(src_img.height, out_img.height)

    src_r = src_img.resize((w, h), Image.LANCZOS)
    out_r = out_img.resize((w, h), Image.LANCZOS)

    # Grayscale difference
    src_gray = np.array(src_r.convert("L"), dtype=np.int16)
    out_gray = np.array(out_r.convert("L"), dtype=np.int16)
    diff = np.abs(src_gray - out_gray).astype(np.uint8)

    # Threshold: highlight pixels changed by > 15 gray levels
    threshold = 15
    mask = diff > threshold

    # Create diff overlay: white background, red where changed
    diff_img = Image.new("RGB", (w, h), (255, 255, 255))
    diff_array = np.array(diff_img)
    diff_array[mask] = [220, 50, 50]   # red highlight
    return Image.fromarray(diff_array.astype(np.uint8))


def _make_side_by_side(src_img, out_img, label_src: str = "SOURCE", label_out: str = "OUTPUT"):
    """Return a side-by-side PIL Image with labels."""
    from PIL import Image, ImageDraw, ImageFont

    label_height = 30
    sep = _SEPARATOR_WIDTH

    max_h = max(src_img.height, out_img.height)
    total_w = src_img.width + sep + out_img.width
    total_h = max_h + label_height

    canvas = Image.new("RGB", (total_w, total_h), (240, 240, 240))

    # Paste source
    canvas.paste(src_img, (0, label_height))
    # Paste output
    canvas.paste(out_img, (src_img.width + sep, label_height))

    # Draw labels
    draw = ImageDraw.Draw(canvas)
    # Use default font (no external TTF needed)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except Exception:
        font = ImageFont.load_default()

    draw.rectangle([0, 0, total_w, label_height - 1], fill=(50, 50, 50))
    draw.text((10, 6), label_src, fill=(255, 255, 255), font=font)
    out_label_x = src_img.width + sep + 10
    draw.text((out_label_x, 6), label_out, fill=(255, 255, 255), font=font)

    return canvas


def render_page_artifacts(
    source_pdf: str,
    output_pdf: str,
    run_sample_dir: str,
    dpi: int = _DEFAULT_DPI,
) -> list[str]:
    """Render all pages and produce side-by-side + diff images.

    Returns list of artifact filenames (relative to *run_sample_dir*).
    """
    import fitz

    artifacts: list[str] = []

    src_doc = fitz.open(source_pdf)
    out_doc = fitz.open(output_pdf)
    src_page_count = len(src_doc)
    out_page_count = len(out_doc)
    src_doc.close()
    out_doc.close()

    n_pages = max(src_page_count, out_page_count)

    for page_num in range(1, n_pages + 1):
        src_img = (
            _render_page_pil(source_pdf, page_num, dpi)
            if page_num <= src_page_count
            else None
        )
        out_img = (
            _render_page_pil(output_pdf, page_num, dpi)
            if page_num <= out_page_count
            else None
        )

        # Save individual page renders
        if src_img is not None:
            name = f"source_page_{page_num}.png"
            src_img.save(os.path.join(run_sample_dir, name))
            artifacts.append(name)

        if out_img is not None:
            name = f"output_page_{page_num}.png"
            out_img.save(os.path.join(run_sample_dir, name))
            artifacts.append(name)

        # Side-by-side (use blank white page if one is missing)
        if src_img is not None or out_img is not None:
            from PIL import Image as _Image
            _blank_src = src_img or _Image.new("RGB", (out_img.width, out_img.height), (255, 255, 255))
            _blank_out = out_img or _Image.new("RGB", (src_img.width, src_img.height), (255, 255, 255))
            sbs = _make_side_by_side(_blank_src, _blank_out)
            sbs_name = f"annotated_side_by_side_page_{page_num}.png"
            sbs.save(os.path.join(run_sample_dir, sbs_name))
            artifacts.append(sbs_name)

        # Diff
        if src_img is not None and out_img is not None:
            diff = _make_diff_image(src_img, out_img)
            diff_name = f"diff_page_{page_num}.png"
            diff.save(os.path.join(run_sample_dir, diff_name))
            artifacts.append(diff_name)

    return artifacts
