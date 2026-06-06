"""Tests for PageImageBlock serialization (Fix C) and full-page image sampling (Fix A).

Fix A: _extract_page_images uses 5-point sampling for full-page images so templates
       with white centers but colored edges/corners are not dropped.
Fix C: PageImageBlock.to_dict/from_dict round-trips image bytes as base64; wired into
       ResumeDocument.to_dict/from_dict so page_images survive DB/IR round-trips.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from tailor.compiler.models import PageImageBlock, ResumeDocument, LayoutProfile, ParaModel, ParaStyle

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_PDF_DIR = _HERE / "samples" / "resume" / "pfd"
_SAMPLE_6  = _PDF_DIR / "6-Template1.pdf"
_SAMPLE_8  = _PDF_DIR / "8-Template3.pdf"
_SAMPLE_9  = _PDF_DIR / "9-Template4.pdf"
_SAMPLE_12 = _PDF_DIR / "12-Nurse-template2.pdf"
_SAMPLE_18 = _PDF_DIR / "18-Project-Engineer-Editable-Resume-Template-Download-in-docx.pdf"
_SAMPLE_39 = _PDF_DIR / "39-backend-developer-1606703830.pdf"


def _minimal_layout() -> LayoutProfile:
    return LayoutProfile(
        page_width_pt=612.0,
        page_height_pt=792.0,
        margin_top_pt=72.0,
        margin_bottom_pt=72.0,
        margin_left_pt=72.0,
        margin_right_pt=72.0,
        default_font_name="Calibri",
        default_font_size_pt=11.0,
    )


def _minimal_doc(page_images: list[PageImageBlock] | None = None) -> ResumeDocument:
    return ResumeDocument(
        header_paras=[],
        sections=[],
        layout=_minimal_layout(),
        all_paras=[],
        source_kind="pdf",
        page_images=page_images or [],
    )


def _solid_png(r: int, g: int, b: int, w: int = 4, h: int = 4) -> bytes:
    """Return a minimal PNG of a solid color using only stdlib."""
    import struct, zlib

    def png_chunk(name: bytes, data: bytes) -> bytes:
        c = zlib.crc32(name + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + name + data + struct.pack(">I", c)

    raw_rows = b""
    for _ in range(h):
        raw_rows += b"\x00" + bytes([r, g, b] * w)

    compressed = zlib.compress(raw_rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", compressed)
        + png_chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# Fix C: PageImageBlock round-trip
# ---------------------------------------------------------------------------

class TestPageImageBlockRoundTrip:

    def test_basic_fields_preserved(self):
        png = _solid_png(10, 20, 30)
        img = PageImageBlock(
            image_bytes=png,
            x_pt=12.5,
            y_pt=34.0,
            width_pt=100.0,
            height_pt=200.0,
            category="full_page_bg",
            page_index=1,
        )
        d = img.to_dict()
        restored = PageImageBlock.from_dict(d)

        assert restored.image_bytes == png
        assert restored.x_pt == 12.5
        assert restored.y_pt == 34.0
        assert restored.width_pt == 100.0
        assert restored.height_pt == 200.0
        assert restored.category == "full_page_bg"
        assert restored.page_index == 1

    def test_category_default_on_missing_key(self):
        png = _solid_png(0, 0, 0)
        img = PageImageBlock(image_bytes=png, x_pt=0, y_pt=0, width_pt=10, height_pt=10)
        d = img.to_dict()
        del d["category"]
        restored = PageImageBlock.from_dict(d)
        assert restored.category == "body_decor"

    def test_page_index_default_zero_on_missing_key(self):
        png = _solid_png(0, 0, 0)
        img = PageImageBlock(image_bytes=png, x_pt=0, y_pt=0, width_pt=10, height_pt=10, page_index=3)
        d = img.to_dict()
        del d["page_index"]
        restored = PageImageBlock.from_dict(d)
        assert restored.page_index == 0

    def test_all_category_values_round_trip(self):
        png = _solid_png(50, 100, 150)
        for cat in ("profile_photo", "header_footer_decor", "body_decor", "full_page_bg",
                    "sidebar_bg", "header_band", "footer_band"):
            img = PageImageBlock(image_bytes=png, x_pt=0, y_pt=0,
                                 width_pt=10, height_pt=10, category=cat)
            assert PageImageBlock.from_dict(img.to_dict()).category == cat


class TestResumeDocumentPageImagesRoundTrip:

    def test_empty_page_images_not_serialised(self):
        doc = _minimal_doc(page_images=[])
        d = doc.to_dict()
        assert "page_images" not in d

    def test_single_image_survives_round_trip(self):
        png = _solid_png(10, 20, 30)
        img = PageImageBlock(
            image_bytes=png, x_pt=5.0, y_pt=8.0,
            width_pt=612.0, height_pt=792.0,
            category="full_page_bg", page_index=0,
        )
        doc = _minimal_doc(page_images=[img])
        restored = ResumeDocument.from_dict(doc.to_dict())

        assert len(restored.page_images) == 1
        r = restored.page_images[0]
        assert r.image_bytes == png
        assert r.x_pt == 5.0
        assert r.y_pt == 8.0
        assert r.width_pt == 612.0
        assert r.height_pt == 792.0
        assert r.category == "full_page_bg"
        assert r.page_index == 0

    def test_multiple_images_order_preserved(self):
        imgs = [
            PageImageBlock(image_bytes=_solid_png(i*10, 0, 0),
                           x_pt=float(i), y_pt=0, width_pt=10, height_pt=10,
                           category="body_decor", page_index=0)
            for i in range(5)
        ]
        doc = _minimal_doc(page_images=imgs)
        restored = ResumeDocument.from_dict(doc.to_dict())
        assert len(restored.page_images) == 5
        for i, r in enumerate(restored.page_images):
            assert r.image_bytes == imgs[i].image_bytes

    def test_backward_compat_missing_page_images_key(self):
        """IR serialized before Fix C (no page_images key) → empty list, no error."""
        doc = _minimal_doc()
        d = doc.to_dict()
        assert "page_images" not in d  # old format
        restored = ResumeDocument.from_dict(d)
        assert restored.page_images == []

    def test_docx_source_unaffected(self):
        """DOCX-sourced documents always have empty page_images → key absent after to_dict."""
        doc = _minimal_doc()
        doc.source_kind = "docx"
        d = doc.to_dict()
        assert "page_images" not in d
        restored = ResumeDocument.from_dict(d)
        assert restored.page_images == []


# ---------------------------------------------------------------------------
# Fix A: full-page image 5-point sampling
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _SAMPLE_6.exists(), reason="sample 6 PDF not in test fixtures")
class TestSample6FullPageImage:

    def test_sample6_full_page_bg_extracted(self):
        """Sample 6 has teal corners and a white center — must not be dropped."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_6, "rb") as f:
            ir = parse_pdf(f.read())

        # The decorative background must survive
        assert ir.pdf_diagnostics is not None
        assert ir.pdf_diagnostics["raster_images_raw"] >= 1, (
            "Sample 6 raster image was not extracted — center-pixel filter still active"
        )
        categories = [img.category for img in ir.page_images]
        assert "full_page_bg" in categories, (
            f"Expected full_page_bg in page_images, got: {categories}"
        )

    def test_sample6_round_trip_preserves_full_page_bg(self):
        """After to_dict/from_dict the full_page_bg image is still present."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_6, "rb") as f:
            ir = parse_pdf(f.read())

        restored = ResumeDocument.from_dict(ir.to_dict())
        categories = [img.category for img in restored.page_images]
        assert "full_page_bg" in categories


@pytest.mark.skipif(not _SAMPLE_18.exists(), reason="sample 18 PDF not in test fixtures")
class TestNearWhiteImageFiltered:
    """Near-white full-page images must remain filtered — no false positives."""

    def test_sample18_not_extracted(self):
        """Sample 18 is effectively white (min channel 250) — must stay filtered."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_18, "rb") as f:
            ir = parse_pdf(f.read())
        categories = [img.category for img in ir.page_images]
        assert "full_page_bg" not in categories, (
            f"Sample 18 near-white image should be filtered, got: {categories}"
        )


@pytest.mark.skipif(not _SAMPLE_39.exists(), reason="sample 39 PDF not in test fixtures")
class TestSample39FullPageBgRoundTrip:
    """Sample 39 already had full_page_bg extracted — verify it survives Fix C."""

    def test_full_page_bg_survives_serialization(self):
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_39, "rb") as f:
            ir = parse_pdf(f.read())

        original_cats = [img.category for img in ir.page_images]
        assert "full_page_bg" in original_cats

        restored = ResumeDocument.from_dict(ir.to_dict())
        restored_cats = [img.category for img in restored.page_images]
        assert "full_page_bg" in restored_cats

    def test_image_bytes_integrity_after_round_trip(self):
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_39, "rb") as f:
            ir = parse_pdf(f.read())

        original = {img.category: img.image_bytes for img in ir.page_images}
        restored = ResumeDocument.from_dict(ir.to_dict())
        for rimg in restored.page_images:
            assert rimg.image_bytes == original[rimg.category], (
                f"image_bytes mismatch for category={rimg.category}"
            )


# ---------------------------------------------------------------------------
# Fix 1: _extract_vector_lines called (sample 12 orange h_rules)
# Fix 2: Full-page cream background not dropped by avg>=240 filter (sample 12)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _SAMPLE_12.exists(), reason="sample 12 PDF not in test fixtures")
class TestSample12VectorLines:
    """Fix 1: _extract_vector_lines() must be called so orange rules appear."""

    def test_h_rule_images_extracted(self):
        """Sample 12 has orange horizontal rules — at least one h_rule must be present."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_12, "rb") as f:
            ir = parse_pdf(f.read())

        categories = [img.category for img in ir.page_images]
        assert "h_rule" in categories, (
            f"Expected h_rule in page_images, got: {categories}"
        )

    def test_h_rule_count_ge_1(self):
        """At least one orange h_rule block must be extracted."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_12, "rb") as f:
            ir = parse_pdf(f.read())

        h_rules = [img for img in ir.page_images if img.category == "h_rule"]
        assert len(h_rules) >= 1, (
            f"Expected >=1 h_rule images, got {len(h_rules)}"
        )

    def test_line_images_raw_diagnostic_present(self):
        """line_images_raw key must exist in pdf_diagnostics."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_12, "rb") as f:
            ir = parse_pdf(f.read())

        assert ir.pdf_diagnostics is not None
        assert "line_images_raw" in ir.pdf_diagnostics


@pytest.mark.skipif(not _SAMPLE_12.exists(), reason="sample 12 PDF not in test fixtures")
class TestSample12CreameBackground:
    """Fix 2: Cream full-page background must not be dropped by avg>=240 filter."""

    def test_full_page_bg_extracted(self):
        """Sample 12 has a cream background (avg≈246, min<240) — must survive."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_12, "rb") as f:
            ir = parse_pdf(f.read())

        categories = [img.category for img in ir.page_images]
        assert "full_page_bg" in categories, (
            f"Expected full_page_bg in page_images (cream bg filtered), got: {categories}"
        )

    def test_full_page_bg_survives_round_trip(self):
        """Cream full_page_bg must survive to_dict/from_dict."""
        from tailor.compiler.pdf_parser import parse_pdf
        with open(_SAMPLE_12, "rb") as f:
            ir = parse_pdf(f.read())

        restored = ResumeDocument.from_dict(ir.to_dict())
        categories = [img.category for img in restored.page_images]
        assert "full_page_bg" in categories


# ---------------------------------------------------------------------------
# Header-zone rule suppression (renderer guard)
# Helpers
# ---------------------------------------------------------------------------

def _rendered_floating_image_ys(ir, resume_template: str) -> "list[float]":
    """Render *ir* to a DOCX and return the y-offsets (pt) of all floating images."""
    import tempfile, os
    from tailor.compiler.docx_renderer import render_docx
    from docx import Document

    out = tempfile.mktemp(suffix=".docx")
    try:
        render_docx(ir, resume_template, out)
        d = Document(out)
        _WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
        _PT = 12700
        ys = []
        for drawing in d.element.body.iter(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}drawing"
        ):
            for anchor in drawing.iter(f"{{{_WP}}}anchor"):
                posV = anchor.find(f"{{{_WP}}}positionV")
                if posV is not None:
                    off = posV.find(f"{{{_WP}}}posOffset")
                    if off is not None:
                        ys.append(int(off.text) / _PT)
        return ys
    finally:
        if os.path.exists(out):
            os.unlink(out)


@pytest.mark.skipif(not _SAMPLE_8.exists(), reason="sample 8 PDF not in test fixtures")
class TestSample8HeaderZoneRuleSuppressed:
    """Sample 8's single h_rule is in the header zone (y=89 < first_sec_y=124) — must not render."""

    def test_header_zone_rule_not_in_docx(self, tmp_path):
        from tailor.compiler.pdf_parser import parse_pdf
        from tailor.config import RESUME_TEMPLATE

        with open(_SAMPLE_8, "rb") as f:
            ir = parse_pdf(f.read())

        h_rules_in_ir = [img for img in ir.page_images if img.category == "h_rule"]
        assert len(h_rules_in_ir) >= 1, "Sample 8 should have at least one h_rule in IR"

        rendered_ys = _rendered_floating_image_ys(ir, RESUME_TEMPLATE)
        h_rule_y = h_rules_in_ir[0].y_pt
        # Use 1pt tolerance to account for int(y * 12700) / 12700 rounding
        assert not any(abs(ry - h_rule_y) < 1.0 for ry in rendered_ys), (
            f"Header-zone h_rule at y={h_rule_y:.0f} should be suppressed in DOCX output "
            f"(rendered floating image y-values: {[f'{y:.0f}' for y in rendered_ys]})"
        )


@pytest.mark.skipif(not _SAMPLE_9.exists(), reason="sample 9 PDF not in test fixtures")
class TestSample9HeaderZoneRuleSuppressed:
    """Sample 9's first h_rule (y=138) is before first section (y=160) — must not render."""

    def test_first_rule_suppressed_body_rules_kept(self):
        from tailor.compiler.pdf_parser import parse_pdf
        from tailor.config import RESUME_TEMPLATE

        with open(_SAMPLE_9, "rb") as f:
            ir = parse_pdf(f.read())

        h_rules_in_ir = sorted(
            [img for img in ir.page_images if img.category == "h_rule"],
            key=lambda img: img.y_pt,
        )
        assert len(h_rules_in_ir) >= 2, "Sample 9 should have multiple h_rules"

        rendered_ys = _rendered_floating_image_ys(ir, RESUME_TEMPLATE)

        # First rule (header-zone) must be absent
        header_rule_y = h_rules_in_ir[0].y_pt
        assert header_rule_y not in rendered_ys, (
            f"Header-zone h_rule at y={header_rule_y:.0f} must not appear in DOCX "
            f"(rendered ys: {[f'{y:.0f}' for y in rendered_ys]})"
        )

        # At least one body-section rule must be present (1pt float tolerance for EMU rounding)
        body_rule_ys = [img.y_pt for img in h_rules_in_ir[1:]]
        assert any(
            any(abs(ry - by) < 1.0 for ry in rendered_ys)
            for by in body_rule_ys
        ), (
            f"At least one body-section rule should appear in DOCX. "
            f"Body rules: {body_rule_ys}, rendered: {rendered_ys}"
        )
