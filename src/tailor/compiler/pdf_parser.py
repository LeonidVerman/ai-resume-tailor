"""Parse a PDF file into a ResumeDocument IR using PyMuPDF (fitz).

Strategy
--------
1. Open PDF; detect scanned documents (total text < 50 chars) → RuntimeError.
2. Cross-page fingerprinting: collect text in top/bottom 8% of each page.
   Any text that appears on ≥ max(2, n_pages//2) pages is treated as a
   repeating header/footer and excluded from content.
3. Extract blocks per page; group consecutive lines into logical paragraphs
   based on Y-gap heuristics.
4. Build a ParagraphProfile for each block from span font metadata:
   - bold: PyMuPDF flags bit 4, or font name contains "Bold".
   - italic: PyMuPDF flags bit 1, or font name contains "Italic"/"Oblique".
   - font_size_pt: most common (mode) size across spans.
   - space_before_pt: Y-gap to the previous block.
   - indent_left_pt: block x0 minus the page's minimum content x0.
5. Semantic inference reuses the same heuristics as docx_parser, adapted for
   PDF font metrics (bold/size) instead of Word style names.
6. Group paragraphs into sections (same as docx_parser: first section_heading
   triggers section grouping; earlier paragraphs go to header_paras).

Note on multi-column layouts
-----------------------------
PyMuPDF returns blocks top-to-bottom across ALL columns simultaneously.
Two-column resumes (sidebar + main content) are detected per page by looking
for a gap ≥ 15 % of the page width in the distribution of block left-edge
(x0) positions.  When detected, blocks are reordered: left column (sorted by
y) followed by right column (sorted by y).  This ensures sidebar content is
not interleaved with main-column content.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
_geo_log = logging.getLogger("tailor.geometry")

from tailor.compiler.models import (
    LayoutProfile,
    ParagraphProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)
from tailor.compiler.pdf_text_normalizer import normalize_spans as _normalize_spans

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_DATE_PLACEHOLDER_RE = re.compile(r"\b20[Xx]{2}\b", re.IGNORECASE)
_SCANNED_CHAR_THRESHOLD = 50  # fewer chars → likely scanned
_CONT_RE = re.compile(r"\s*\(cont\.?\)\s*$", re.IGNORECASE)

# Zone fractions for header/footer detection (top/bottom % of page height)
_HF_TOP_ZONE = 0.08
_HF_BOT_ZONE = 0.92
# Wider zones used for pages 2+ running-header detection (Strategy 4)
_HF_PAGE1_TOP_SCAN = 0.20   # top 20% of page 1 — collect candidate header lines
_HF_PAGE2_PLUS_ZONE = 0.25  # top 25% of pages 2+ — match against page 1 lines


# ---------------------------------------------------------------------------
# Font normalization
# ---------------------------------------------------------------------------

# Ordered list of (normalised_key, standard_windows_font) pairs.
# Keys are lowercase with spaces/hyphens removed.  The first match wins.
# Purpose: map non-standard PDF fonts to the nearest standard Windows font so
# that the rendered DOCX looks reasonable even without the original font.
# Fonts not matched here return None → the document default font is used.
_FONT_NORMALIZATION_MAP: list[tuple[str, str]] = [
    # Standard fonts — pass through as-is
    ("calibri",     "Calibri"),
    ("arial",       "Arial"),
    ("segoeui",     "Segoe UI"),
    ("verdana",     "Verdana"),
    ("tahoma",      "Tahoma"),
    ("trebuchet",   "Trebuchet MS"),
    ("georgia",     "Georgia"),
    ("cambria",     "Cambria"),
    ("constantia",  "Constantia"),
    ("courier",     "Courier New"),
    ("consolas",    "Consolas"),
    ("timesnewroman", "Times New Roman"),
    # Humanist / geometric sans-serif → Calibri (similar width metrics)
    ("opensans",    "Calibri"),
    ("sourcesans",  "Calibri"),
    ("nunitosans",  "Calibri"),
    ("nunito",      "Calibri"),
    ("lato",        "Calibri"),
    ("ubuntu",      "Calibri"),
    ("overpass",    "Calibri"),
    ("cabin",       "Calibri"),
    ("muli",        "Calibri"),
    ("mulish",      "Calibri"),
    ("karla",       "Calibri"),
    ("livvic",      "Calibri"),
    ("outfit",      "Calibri"),
    ("inter",       "Calibri"),
    ("barlow",      "Calibri"),
    ("poppins",     "Calibri"),
    ("figtree",     "Calibri"),
    ("dmsans",      "Calibri"),
    ("worksans",    "Calibri"),
    ("jost",        "Calibri"),
    ("manrope",     "Calibri"),
    ("rubik",       "Calibri"),
    ("hind",        "Calibri"),
    ("noto",        "Calibri"),
    ("montserrat",  "Calibri"),
    ("raleway",     "Calibri"),
    ("josefinsans", "Calibri"),
    ("exo",         "Calibri"),
    ("prompt",      "Calibri"),
    # Classic sans-serif → Arial
    ("helvetica",   "Arial"),
    ("arimo",       "Arial"),
    ("myriad",      "Arial"),
    ("franklin",    "Arial"),
    ("gill",        "Arial"),
    ("impact",      "Arial"),
    # Serif → Times New Roman or Georgia
    ("times",       "Times New Roman"),
    ("garamond",    "Times New Roman"),
    ("palatino",    "Times New Roman"),
    ("merriweather","Times New Roman"),
    ("ebgaramond",  "Times New Roman"),
    ("cormorant",   "Times New Roman"),
    ("lora",        "Georgia"),
    ("playfair",    "Georgia"),
    ("libre",       "Georgia"),
    # Monospace
    ("inconsolata", "Courier New"),
    ("sourcecodepro","Consolas"),
    ("firacode",    "Consolas"),
    ("dejavusansmono","Courier New"),
]

# Regex to strip trailing style qualifiers like "-Bold", "_Italic", "-Regular"
_FONT_STYLE_SUFFIX_RE = re.compile(
    r"[-_\s]*(bold|italic|oblique|regular|medium|light|thin|condensed|"
    r"expanded|narrow|roman|semibold|demibold|black|heavy|ultra|extra|"
    r"pro|display|text|sc|mt|new|sans|serif).*",
    re.IGNORECASE,
)


def _normalize_font_name(raw: str | None) -> str | None:
    """Map a PDF font name to a standard Windows-compatible font name.

    Strips embedded-subset prefixes (e.g. 'ABCDEF+FontName') and style
    suffixes, then looks up the base name in _FONT_NORMALIZATION_MAP.
    Returns None for unknown fonts so the caller can fall back to the
    document's default font rather than using an unmapped custom name.
    """
    if not raw:
        return None
    # Strip 6-char uppercase subset prefix common in embedded PDFs
    if len(raw) > 7 and raw[6] == "+" and raw[:6].isupper():
        raw = raw[7:]
    # Normalise: strip style suffixes, then lowercase and remove non-alpha
    base = _FONT_STYLE_SUFFIX_RE.sub("", raw)
    norm = re.sub(r"[^a-zA-Z]", "", base).lower()
    for key, mapped in _FONT_NORMALIZATION_MAP:
        if key in norm:
            return mapped
    return None  # unknown font → use document default


# ---------------------------------------------------------------------------
# Header/footer detection
# ---------------------------------------------------------------------------

def _block_text(blk: dict) -> str:
    """Concatenate all span texts in a fitz block."""
    parts = []
    for line in blk.get("lines", []):
        for span in line.get("spans", []):
            t = span.get("text", "")
            if t:
                parts.append(t)
    return " ".join(parts).strip()


def _deduplicate_page_indices(doc) -> set[int]:
    """Return the set of page indices to skip because they are exact duplicates.

    When a PDF is a template gallery (the same page repeated N times), every
    copy after the first produces spurious duplicate sections in the parser
    output.  This function fingerprints each page by its normalised text
    content and marks all but the *first* occurrence of any duplicate for
    skipping.

    Only exact duplicates are skipped (normalised whitespace).  Near-similar
    but distinct pages are preserved.
    """
    seen: dict[str, int] = {}  # fingerprint → first page index
    skip: set[int] = set()
    for page in doc:
        text = " ".join(page.get_text().split())
        if not text:
            continue
        if text in seen:
            skip.add(page.number)
        else:
            seen[text] = page.number
    return skip


def _detect_header_footer_texts(doc, skip_pages: "set[int] | None" = None) -> set[str]:
    """Return text strings that appear as page headers/footers.

    Two strategies:
    1. Exact text match: any string in the top/bottom 8% zone on ≥ half the
       pages is a H/F.
    2. Position bucket match: blocks at the same normalised Y (±2%) on ≥ half
       the pages are H/F, even when text changes (e.g. "Page 1 of 2" vs "Page
       2 of 2").
    3. Pages 2+ absolute: any block in the top/bottom 8% zone on page 2 or
       later is unconditionally excluded (page numbers, running heads).

    *skip_pages* is the set of duplicate page indices returned by
    _deduplicate_page_indices.  Skipping those pages prevents content that
    appears on every page of a multi-page identical template (e.g. the
    candidate name on a 3-copy gallery PDF) from being incorrectly classified
    as a running header/footer.
    """
    _skip = skip_pages or set()
    n_pages = len(doc)
    n_active = sum(1 for i in range(n_pages) if i not in _skip)
    if n_active < 2:
        return set()

    hf_texts: set[str] = set()
    min_appearances = max(2, n_active // 2)

    # Maps: y-bucket → list of (page_idx, text) tuples
    bucket_entries: dict[float, list[tuple[int, str]]] = {}
    text_page_count: Counter = Counter()
    page_zone_texts: list[set[str]] = []  # one set per page, zone texts only

    for page_idx, page in enumerate(doc):
        if page_idx in _skip:
            page_zone_texts.append(set())
            continue
        h = page.rect.height
        top_zone = h * _HF_TOP_ZONE
        bot_zone = h * _HF_BOT_ZONE
        seen_on_page: set[str] = set()
        zone_texts: set[str] = set()

        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            y0, y1 = blk["bbox"][1], blk["bbox"][3]
            in_zone = y1 <= top_zone or y0 >= bot_zone
            if not in_zone:
                continue

            text = _block_text(blk)
            if not text:
                continue

            zone_texts.add(text)

            # Strategy 1: exact text repeated
            if text not in seen_on_page:
                seen_on_page.add(text)
                text_page_count[text] += 1

            # Strategy 2: position bucket (2% Y precision)
            bucket = round(y0 / h, 2)
            if bucket not in bucket_entries:
                bucket_entries[bucket] = []
            bucket_entries[bucket].append((page_idx, text))

        page_zone_texts.append(zone_texts)

    # Strategy 1: exact text on many pages
    for text, count in text_page_count.items():
        if count >= min_appearances:
            hf_texts.add(text)

    # Strategy 2: same Y bucket with entries from multiple distinct pages
    for bucket, entries in bucket_entries.items():
        distinct_pages = len({e[0] for e in entries})
        if distinct_pages >= min_appearances:
            for _, text in entries:
                hf_texts.add(text)

    # Strategy 3: pages 2+ — unconditionally exclude top/bottom zone text
    for i, zone_texts in enumerate(page_zone_texts):
        if i >= 1:
            hf_texts.update(zone_texts)

    # Strategy 4: running header repetition — blocks in the top 25% of pages 2+
    # that case-insensitively match any line from page 1's top 20%.
    # Handles resumes where the candidate puts their name/contact at the top of
    # page 2 even if it falls outside the strict 8% zone.
    if n_pages >= 2:
        page1 = doc[0]
        h1 = page1.rect.height
        page1_line_texts_lower: set[str] = set()
        for blk in page1.get_text("dict")["blocks"]:
            if blk.get("type") != 0:
                continue
            if blk["bbox"][1] >= h1 * _HF_PAGE1_TOP_SCAN:
                continue
            for line in blk.get("lines", []):
                lt = "".join(s.get("text", "") for s in line.get("spans", [])).strip()
                if lt:
                    page1_line_texts_lower.add(lt.lower())

        for page_idx in range(1, n_pages):
            page = doc[page_idx]
            h = page.rect.height
            for blk in page.get_text("dict")["blocks"]:
                if blk.get("type") != 0:
                    continue
                if blk["bbox"][1] >= h * _HF_PAGE2_PLUS_ZONE:
                    continue
                for line in blk.get("lines", []):
                    lt = "".join(
                        s.get("text", "") for s in line.get("spans", [])
                    ).strip()
                    if lt and lt.lower() in page1_line_texts_lower:
                        hf_texts.add(lt)

    return hf_texts


# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------

def _fitz_color_to_hex(color) -> str | None:
    """Convert a PyMuPDF color value to a 6-char hex string (RRGGBB, no '#').

    PyMuPDF represents colors as:
    - int: sRGB packed (r<<16 | g<<8 | b), each component 0–255
    - float (grayscale): 0.0–1.0
    - tuple of 3 floats (RGB): each 0.0–1.0
    """
    if color is None:
        return None
    if isinstance(color, int):
        r = (color >> 16) & 0xFF
        g = (color >> 8) & 0xFF
        b = color & 0xFF
        return f"{r:02x}{g:02x}{b:02x}"
    if isinstance(color, float):
        v = int(color * 255 + 0.5)
        return f"{v:02x}{v:02x}{v:02x}"
    if isinstance(color, (tuple, list)) and len(color) == 3:
        r, g, b = color
        return f"{int(r * 255 + 0.5):02x}{int(g * 255 + 0.5):02x}{int(b * 255 + 0.5):02x}"
    return None


def _dominant_text_color(spans: list[dict]) -> str | None:
    """Return the most common text color across spans as hex RRGGBB."""
    counter: Counter = Counter()
    for span in spans:
        color = span.get("color")
        if color is not None:
            counter[color] += 1
    if not counter:
        return None
    return _fitz_color_to_hex(counter.most_common(1)[0][0])


def _extract_header_bg(page) -> tuple[str | None, float]:
    """Detect a full-width dark header rectangle on page 0.

    Returns (hex_bg_color, y_bottom) for the topmost large full-width filled
    rectangle (width ≥ 80 % of page, height > 8 pt, fill not white/near-white).
    Used to apply dark background colors to header_paras whose text would
    otherwise be invisible (white-on-dark templates like sample 3 and 25).

    Returns (None, 0.0) when no such rectangle exists.
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return None, 0.0

    pw = page.rect.width
    best_color: str | None = None
    best_y1 = 0.0

    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        if (x1 - x0) < pw * 0.80:
            continue  # not full-width
        if (y1 - y0) < 8.0:
            continue  # too thin
        hex_color = _fitz_color_to_hex(fill)
        if hex_color is None:
            continue
        # Skip white and near-white fills (luminance > 210/255 = 82%).
        # fafafa (250,250,250) and similar light grays must not be treated as
        # dark header bands — they produce invisible white-on-light rendering.
        try:
            _r = int(hex_color[0:2], 16)
            _g = int(hex_color[2:4], 16)
            _b = int(hex_color[4:6], 16)
            if (_r + _g + _b) / 3 > 210:
                continue  # too light to be a dark header band
        except (ValueError, IndexError):
            continue
        # Take the topmost large rectangle
        if best_color is None or y0 < best_y1:
            best_color = hex_color
            best_y1 = y1

    # Fallback: pixel-sample the centre column when no large drawing rectangle
    # was found (some PDFs render the header band via a form XObject or raw
    # content-stream operators that get_drawings() does not capture).
    if best_color is None:
        try:
            import fitz as _fitz
            ph = page.rect.height
            mat = _fitz.Matrix(1, 1)
            _cx = pw / 2.0
            # Scan top 30% of page to find a non-white band — the band may not
            # start at y=0 (some templates have a white margin above the header).
            _band_color = None
            _band_y0 = 0.0
            _band_y1 = 0.0
            _scan_limit = int(min(ph * 0.30, 250))
            for _y in range(0, _scan_limit, 4):
                _clip = _fitz.Rect(_cx - 1, _y, _cx + 1, _y + 4)
                _px = page.get_pixmap(matrix=mat, clip=_clip, alpha=False).pixel(0, 0)
                _lum = (_px[0] + _px[1] + _px[2]) / 3.0
                if _lum < 100:  # non-white / dark band
                    if _band_color is None:
                        _band_color = _px
                        _band_y0 = float(_y)
                    _band_y1 = float(_y + 4)
                elif _band_color is not None:
                    break  # band ended
            if _band_color is not None and (_band_y1 - _band_y0) > 30.0:
                r, g, b = _band_color[0], _band_color[1], _band_color[2]
                best_color = f"{r:02x}{g:02x}{b:02x}"
                best_y1 = _band_y1
        except Exception:
            pass

    return best_color, best_y1


def _pixel_sample_col_bg(
    page, x_center: float, y_start: float, y_end: float
) -> str | None:
    """Pixel-sample a vertical strip for a consistent non-white background color.

    Samples at x=x_center across [y_start, y_end] in 8 steps.  Returns the
    dominant non-white color as hex RRGGBB when ≥ 4 samples agree, else None.
    Used as a fallback for raster-background PDFs where get_drawings() is empty.
    """
    import fitz as _fitz
    from collections import Counter as _Counter

    colors: list = []
    step = max(1.0, (y_end - y_start) / 8.0)
    for i in range(8):
        y = y_start + (i + 0.5) * step
        try:
            clip = _fitz.Rect(x_center - 1, y, x_center + 1, y + 1)
            pix = page.get_pixmap(matrix=_fitz.Matrix(1, 1), clip=clip, alpha=False)
            if pix.samples and pix.n >= 3:
                r, g, b = pix.samples[0], pix.samples[1], pix.samples[2]
                if (r + g + b) / 3 < 225:  # not near-white
                    colors.append((r, g, b))
        except Exception:
            continue

    if len(colors) < 4:
        return None
    dominant, count = _Counter(colors).most_common(1)[0]
    if count < len(colors) * 0.4:
        return None
    r, g, b = dominant
    if (r + g + b) / 3 >= 225:
        return None
    if (r + g + b) / 3 < 15:  # near-black → text pixels, not a sidebar background
        return None
    return f"{r:02x}{g:02x}{b:02x}"


def _extract_col_info(
    page, gap_midpoint: float | None
) -> tuple[str | None, str | None, float | None]:
    """Detect left/right column background colors and the visual column split.

    Returns (left_bg_hex, right_bg_hex, visual_split_x).

    Looks for filled rectangles that cover ≥ 10 % of the page area and
    classifies them as left or right by centre x vs. gap_midpoint.
    visual_split_x is the right edge (x1) of the largest left-column rect —
    i.e. the visual boundary of the sidebar, which is more accurate than the
    text-block gap midpoint for column-width calculations.

    If no suitable vector drawings are found and gap_midpoint is known, falls
    back to pixel-sampling the column centres (handles raster-background PDFs
    like templates whose background is a single full-page image XObject).
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        drawings = []

    pw = page.rect.width
    ph = page.rect.height
    min_area = pw * ph * 0.10

    left_bg: str | None = None
    right_bg: str | None = None
    visual_split_x: float | None = None
    best_left_area = 0.0
    split = gap_midpoint if gap_midpoint is not None else pw / 2

    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        area = (x1 - x0) * (y1 - y0)
        if area < min_area:
            continue
        # Skip full-width bands (header/footer bars spanning >80% page width) —
        # they are not column backgrounds.
        if (x1 - x0) > pw * 0.80:
            continue
        # Skip rects that start below the top 40% of the page — sidebar
        # backgrounds always start near the top; lower rects are decorative.
        if y0 > ph * 0.40:
            continue
        hex_color = _fitz_color_to_hex(fill)
        if hex_color is None:
            continue
        center_x = (x0 + x1) / 2
        if center_x < split:
            left_bg = hex_color
            if area > best_left_area:
                best_left_area = area
                visual_split_x = x1  # right edge of the largest sidebar rect
        else:
            right_bg = hex_color

    # Pixel-sampling fallback: raster-background PDFs have 0 drawings but still
    # show a colored sidebar.  Sample the left column centre if nothing was found.
    if left_bg is None and gap_midpoint is not None and gap_midpoint > pw * 0.10:
        left_center_x = gap_midpoint * 0.35
        sampled = _pixel_sample_col_bg(page, left_center_x, ph * 0.15, ph * 0.85)
        if sampled is not None:
            left_bg = sampled
            # Use the text-block gap as visual split when no drawing rect exists.
            if visual_split_x is None:
                visual_split_x = gap_midpoint

    return left_bg, right_bg, visual_split_x


def _extract_section_bg_rects(
    page,
) -> list[tuple[float, float, float, float, str]]:
    """Return filled band rects suitable for section heading backgrounds.

    Filters to rects that look like accent bands (not full-column backgrounds
    and not thin rules): height > 8 pt, height < 20 % of page, width > 20 %
    of page width.

    Returns list of (x0, y0, x1, y1, hex_color).
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    ph = page.rect.height
    pw = page.rect.width
    min_h = 8.0
    max_h = ph * 0.20
    min_w = pw * 0.20

    result: list[tuple[float, float, float, float, str]] = []
    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        h = y1 - y0
        w = x1 - x0
        # Full-width top bands (page-spanning header bars) may be taller
        # than max_h but still need their colour propagated to header paras.
        is_header_band = w > pw * 0.80 and y0 < ph * 0.05
        if h < min_h or (h > max_h and not is_header_band) or w < min_w:
            continue
        hex_color = _fitz_color_to_hex(fill)
        if hex_color is None:
            continue
        result.append((x0, y0, x1, y1, hex_color))

    return result


def _pixel_sample_section_bands(
    page, split_x: float, col_bg_hex: str
) -> list[tuple[float, float, float, float, str]]:
    """Detect colored horizontal bands in the left column via pixel sampling.

    Fallback for raster-background PDFs where get_drawings() returns nothing.
    Samples the left column center at 2pt intervals and groups consecutive
    rows whose luminance differs significantly from the column background.

    Returns list of (x0, y0, x1, y1, hex_color) matching the section_bg_rects
    format so they can be used directly for background_color assignment.
    """
    try:
        import fitz as _fitz
        ph = page.rect.height
        sample_x = split_x * 0.5
        mat = _fitz.Matrix(1, 1)

        col_r = int(col_bg_hex[0:2], 16)
        col_g = int(col_bg_hex[2:4], 16)
        col_b = int(col_bg_hex[4:6], 16)
        col_lum = (col_r + col_g + col_b) / 3

        samples: list[tuple[float, int, int, int]] = []
        y = 0.0
        while y < ph:
            clip = _fitz.Rect(sample_x - 1, y, sample_x + 1, y + 1)
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
            if pix.samples and pix.n >= 3:
                r, g, b = pix.samples[0], pix.samples[1], pix.samples[2]
                samples.append((y, r, g, b))
            y += 2.0

        MIN_BAND_H = 8.0
        LUM_THRESH = 40.0  # min luminance difference from col_bg to qualify

        bands: list[tuple[float, float, float, float, str]] = []
        band_start: float | None = None
        band_color: str | None = None

        for y_pos, r, g, b in samples:
            lum = (r + g + b) / 3
            lum_diff = abs(lum - col_lum)
            is_band_pixel = lum_diff > LUM_THRESH and lum < 245
            hex_c = f"{r:02x}{g:02x}{b:02x}" if is_band_pixel else None

            if is_band_pixel:
                if band_start is None or hex_c != band_color:
                    if band_start is not None and (y_pos - band_start) >= MIN_BAND_H:
                        bands.append((0.0, band_start, split_x, y_pos, band_color))  # type: ignore[arg-type]
                    band_start = y_pos
                    band_color = hex_c
            else:
                if band_start is not None:
                    if (y_pos - band_start) >= MIN_BAND_H:
                        bands.append((0.0, band_start, split_x, y_pos, band_color))  # type: ignore[arg-type]
                    band_start = None
                    band_color = None

        if band_start is not None and (ph - band_start) >= MIN_BAND_H:
            bands.append((0.0, band_start, split_x, ph, band_color))  # type: ignore[arg-type]

        return bands
    except Exception:
        return []


def _extract_icon_map(
    page, split_x: float | None
) -> dict[tuple[int, int], bytes]:
    """Detect small icon-like vector drawings in the sidebar and render to PNG.

    Returns a dict mapping (y0_rounded, y1_rounded) → PNG bytes for each
    icon found.  Only drawings in the left column (x0 < split_x) are
    considered.  Drawings that are large (> 20pt tall or wide) or very
    simple (≤ 3 path items — thin rules/lines) are excluded.

    The PNG is rendered at 3× scale for crispness, then stored as bytes.
    """
    result: dict[tuple[int, int], bytes] = {}
    if split_x is None:
        return result
    try:
        drawings = page.get_drawings()
    except Exception:
        return result

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        # Must be in the left column, small (icon-sized), and complex (not a rule)
        if x0 >= split_x:
            continue
        if w > 20 or h > 20 or w < 6 or h < 6:
            continue
        if len(d.get("items", [])) <= 3:
            continue
        try:
            # Render a small region of the page at 3× scale to capture the icon
            import fitz
            scale = 3.0
            clip = fitz.Rect(x0 - 1, y0 - 1, x1 + 1, y1 + 1)
            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
            png_bytes = pix.tobytes("png")
        except Exception:
            continue

        key = (round(y0), round(y1))
        # Keep only one icon per y-band (prefer the larger one)
        if key not in result:
            result[key] = png_bytes

    return result


def _match_icon(
    icon_map: dict[tuple[int, int], bytes], para_y0: float, para_y1: float
) -> bytes | None:
    """Return icon PNG bytes whose y-range overlaps the paragraph's y-range."""
    for (iy0, iy1), png in icon_map.items():
        # Overlap check with a small tolerance
        if iy0 <= para_y1 + 2 and iy1 >= para_y0 - 2:
            return png
    return None


def _extract_bullet_dot_ys(page) -> frozenset:
    """Return frozenset of y0 values (rounded) for small bullet-dot drawings.

    Bullet dots are small filled circles/squares (3–5 pt) with high path
    complexity (≥ 10 items).  They mark list items that have no text prefix
    (skills, languages, certifications) in sidebar-layout PDFs.  Distinct
    from the larger contact icons (6–20 pt) which are handled separately.
    """
    result: set[int] = set()
    for d in page.get_drawings():
        rect = d.get("rect")
        if rect is None:
            continue
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        if not (3.0 <= w <= 5.5 and 3.0 <= h <= 5.5):
            continue
        if len(d.get("items", [])) < 10:
            continue
        if d.get("fill") is None:
            continue
        result.add(round(y0))
    return frozenset(result)


def _has_bullet_dot(bullet_dot_ys: frozenset, para_y0: float) -> bool:
    """Return True if a bullet dot exists within 8 pt of *para_y0*."""
    y = round(para_y0)
    return any(abs(y - dot_y) <= 8 for dot_y in bullet_dot_ys)


def _make_solid_color_png(width_pt: float, height_pt: float, hex_color: str) -> bytes:
    """Create a solid-color PNG of the given dimensions at 2× resolution."""
    import fitz as _fitz

    scale = 2.0
    w_px = max(4, int(width_pt * scale))
    h_px = max(4, int(height_pt * scale))
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    pix = _fitz.Pixmap(_fitz.csRGB, _fitz.IRect(0, 0, w_px, h_px), False)
    pix.set_rect(pix.irect, (r, g, b))
    return pix.tobytes("png")


def _extract_decorative_vector_images(page) -> "list":
    """Extract large decorative filled regions from vector drawings as PageImageBlock.

    Generates synthetic solid-color PNG images for large filled rectangles
    (sidebars, header/footer bands, decorative section blocks) so they can be
    inserted as behind-text floating images in the rendered DOCX.

    Covers regions that ``_extract_col_info`` already uses for ``w:shd`` cell
    backgrounds AND regions outside the column table (footer bands, decorative
    mid-page blocks).

    Thresholds:
    - area >= 3 % of the page  (reduces noise from tiny decorations)
    - fill is non-white (luminance < 240)
    - height >= 5 pt  (excludes hairline rules)
    """
    from tailor.compiler.models import PageImageBlock

    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    pw = page.rect.width
    ph = page.rect.height
    min_area = pw * ph * 0.03

    seen: set = set()
    result: list = []

    for d in drawings:
        fill = d.get("fill")
        if fill is None:
            continue
        rect = d.get("rect")
        if rect is None:
            continue

        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        if w * h < min_area:
            continue
        if h < 5.0:
            continue

        hex_color = _fitz_color_to_hex(fill)
        if hex_color is None:
            continue

        # Skip near-white fills (nothing to overlay)
        r_c = int(hex_color[0:2], 16)
        g_c = int(hex_color[2:4], 16)
        b_c = int(hex_color[4:6], 16)
        if (r_c + g_c + b_c) / 3 >= 240:
            continue

        # Fix B: Near-full-page vector shapes (≥85% of page in both dimensions) are
        # reclassified as "full_page_bg" rather than skipped outright.  Single-column
        # templates need them as floating page backgrounds; two-column templates have
        # Fix A filter them when cell shading already handles the column fill.
        # Near-white full-page shapes are skipped (virtual canvas, nothing to add).
        _is_full_page = w >= pw * 0.85 and h >= ph * 0.85

        if _is_full_page:
            # Near-white full-page shape → skip (no visual contribution)
            if min(r_c, g_c, b_c) > 240:
                continue
            category = "full_page_bg"
        else:
            # Normal classification for partial shapes
            is_full_w = w > pw * 0.75
            is_sidebar = (x0 < pw * 0.10 or x1 > pw * 0.90) and w < pw * 0.65 and h > ph * 0.12
            if is_full_w and y0 < ph * 0.20:
                category = "header_band"
            elif is_full_w and y1 > ph * 0.75:
                category = "footer_band"
            elif is_sidebar:
                category = "sidebar_bg"
            else:
                category = "body_decor"

        key = (round(x0), round(y0), round(x1), round(y1))
        if key in seen:
            continue
        seen.add(key)

        try:
            png_bytes = _make_solid_color_png(w, h, hex_color)
        except Exception:
            continue

        result.append(PageImageBlock(
            image_bytes=png_bytes,
            x_pt=x0, y_pt=y0,
            width_pt=w, height_pt=h,
            category=category,
            page_index=0,
        ))

    return result


def _extract_vector_lines(page) -> "list":
    """Extract thin horizontal/vertical vector rules as PageImageBlock.

    Captures stroke-only paths that form section dividers, column separators,
    and decorative rules (h < 4 pt or w < 4 pt, length > 20 pt, non-white).
    Rasterizes each rule as a solid-color PNG matching the stroke color.

    Does not capture:
    - Tiny glyphs or noise (length < 20 pt)
    - Near-white lines (luminance ≥ 230 / 255 — invisible on white bg)
    - Lines already captured by _extract_decorative_vector_images (filled areas)
    - Hyperlink underlines: multiple segments at the same y (±2 pt snap)
    - Hyperlink underlines: blue-dominant stroke color (e.g. #0000ff, #1155cc)
    - Short decorative fragments: length < 25 % of page width
    """
    from collections import defaultdict

    from tailor.compiler.models import PageImageBlock

    try:
        drawings = page.get_drawings()
    except Exception:
        return []

    pw = page.rect.width
    min_length = pw * 0.25  # require at least 25 % of page width

    # --- Pass 1: collect deduplicated candidates ---
    candidates: list = []
    seen: set = set()

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        color = d.get("color")  # stroke color
        if color is None:
            continue  # no stroke — already handled as filled region
        fill = d.get("fill")
        if fill is not None:
            # Skip filled shapes — handled by _extract_decorative_vector_images
            # UNLESS the fill is white (contact boxes have white fill + stroke)
            try:
                fr, fg, fb = int(fill[0] * 255), int(fill[1] * 255), int(fill[2] * 255)
                if (fr + fg + fb) / 3 < 230:
                    continue  # non-white fill → already captured
            except (TypeError, IndexError):
                pass

        sw = d.get("width") or 1.0
        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0

        effective_h = max(h, sw)
        effective_w = max(w, sw)

        is_h_rule = effective_h <= 4.0 and effective_w >= 20.0
        is_v_rule = effective_w <= 4.0 and effective_h >= 20.0
        if not (is_h_rule or is_v_rule):
            continue

        hex_color = _fitz_color_to_hex(color)
        if hex_color is None:
            continue
        r_c = int(hex_color[0:2], 16)
        g_c = int(hex_color[2:4], 16)
        b_c = int(hex_color[4:6], 16)
        if (r_c + g_c + b_c) / 3 >= 230:
            continue  # near-white line — not visually significant

        key = (round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1))
        if key in seen:
            continue
        seen.add(key)

        line_len = effective_w if is_h_rule else effective_h
        candidates.append({
            "is_h": is_h_rule, "x0": x0, "y0": y0, "sw": sw,
            "eff_w": effective_w, "eff_h": effective_h,
            "len": line_len, "hex": hex_color,
            "r": r_c, "g": g_c, "b": b_c,
        })

    # --- Pass 2: filter hyperlink underlines and short fragments ---

    # Rule A: multiple distinct segments at the same y → hyperlinks in a row
    y_groups: "dict[int, list]" = defaultdict(list)
    for c in candidates:
        y_key = round(c["y0"] / 2) * 2  # 2 pt snap
        y_groups[y_key].append(c)
    single_y = [segs[0] for segs in y_groups.values() if len(segs) == 1]

    result: list = []
    for c in single_y:
        r_c, g_c, b_c = c["r"], c["g"], c["b"]

        # Rule B: strongly blue-dominant stroke = hyperlink underline color
        if b_c > r_c + 80 and b_c > g_c + 80 and b_c > 100:
            continue

        # Rule C: too short to be a section divider
        if c["len"] < min_length:
            continue

        render_x = c["x0"] - c["sw"] / 2 if not c["is_h"] else c["x0"]
        render_y = c["y0"] - c["sw"] / 2 if c["is_h"] else c["y0"]
        render_w = max(c["eff_w"], 1.0)
        render_h = max(c["eff_h"], 1.0)

        try:
            png_bytes = _make_solid_color_png(render_w, render_h, c["hex"])
        except Exception:
            continue

        result.append(PageImageBlock(
            image_bytes=png_bytes,
            x_pt=render_x,
            y_pt=render_y,
            width_pt=render_w,
            height_pt=render_h,
            category="h_rule" if c["is_h"] else "v_rule",
            page_index=0,
        ))

    return result


def _extract_vector_borders(page) -> "list":
    """Extract stroked rectangle borders (contact boxes, section frames) as PageImageBlock.

    Targets rectangles with a significant stroke outline but no fill (or white fill)
    that form visible structural borders. These are rasterized from the page at 2x
    resolution so antialiasing and corner rounding are preserved.

    Thresholds:
    - Area >= 0.5 % of page (eliminates tiny icons)
    - Area < 3 % of page (larger filled areas are handled by _extract_decorative_vector_images)
    - Has stroke color (non-white)
    - Fill is None or white (pure border with no filled background)
    - Stroke width > 0.5 pt (visible border)
    """
    from tailor.compiler.models import PageImageBlock

    try:
        import fitz as _fitz
        drawings = page.get_drawings()
    except Exception:
        return []

    pw = page.rect.width
    ph = page.rect.height
    min_area = pw * ph * 0.005   # 0.5 %
    max_area = pw * ph * 0.30    # 30 % — large contact/section boxes included;
    # _extract_decorative_vector_images handles filled non-white areas only,
    # so white-fill stroked rectangles (contact boxes) won't be double-counted.

    result: list = []
    seen: set = set()
    scale = 2.0
    mat = _fitz.Matrix(scale, scale)

    for d in drawings:
        rect = d.get("rect")
        if rect is None:
            continue
        color = d.get("color")
        if color is None:
            continue
        sw = d.get("width") or 1.0
        if sw < 0.5:
            continue

        fill = d.get("fill")
        # Only capture white-fill or no-fill shapes (filled non-white → handled elsewhere)
        if fill is not None:
            try:
                fr, fg, fb = int(fill[0] * 255), int(fill[1] * 255), int(fill[2] * 255)
                if (fr + fg + fb) / 3 < 230:
                    continue
            except (TypeError, IndexError):
                continue

        x0, y0, x1, y1 = rect
        w, h = x1 - x0, y1 - y0
        area = w * h
        if area < min_area or area > max_area:
            continue

        # Must be roughly rectangular (aspect ratio 0.1 to 10)
        if h < 1.0 or w / h > 10 or h / w > 10:
            continue

        # Stroke color non-white
        hex_color = _fitz_color_to_hex(color)
        if hex_color is None:
            continue
        r_c = int(hex_color[0:2], 16)
        g_c = int(hex_color[2:4], 16)
        b_c = int(hex_color[4:6], 16)
        if (r_c + g_c + b_c) / 3 >= 230:
            continue

        key = (round(x0), round(y0), round(x1), round(y1))
        if key in seen:
            continue
        seen.add(key)

        # Rasterize the clip region (captures the border stroke faithfully)
        try:
            clip = _fitz.Rect(x0 - sw, y0 - sw, x1 + sw, y1 + sw)
            pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
            png_bytes = pix.tobytes("png")
        except Exception:
            continue

        result.append(PageImageBlock(
            image_bytes=png_bytes,
            x_pt=x0 - sw,
            y_pt=y0 - sw,
            width_pt=w + sw * 2,
            height_pt=h + sw * 2,
            category="border_box",
            page_index=0,
        ))

    return result


def _extract_page_images(fitz_doc, page_index: int = 0) -> "list":
    """Extract meaningful raster images from a PDF page.

    Skips:
    - Full-page background images (>= 85 % of page in both dimensions)
    - Tiny icons (< 25 pt) — handled separately by ``_extract_icon_map``
    - Thin separator lines (height < 4 pt)
    - Images reused at > 3 positions (repeated bullets / rule tiles)

    Classifies survivors as:
    - ``'profile_photo'``: roughly square, width < 40 % of page width
    - ``'header_footer_decor'``: top or bottom 25 % of the page
    - ``'body_decor'``: everything else

    Returns a list of ``PageImageBlock`` instances (runtime-only; not serialised).
    """
    import fitz as _fitz
    from tailor.compiler.models import PageImageBlock

    try:
        page = fitz_doc[page_index]
    except Exception:
        return []

    pw = page.rect.width
    ph = page.rect.height
    result: list = []
    seen_xrefs: set = set()

    try:
        all_imgs = page.get_images(full=True)
    except Exception:
        return []

    for img_info in all_imgs:
        xref = img_info[0]
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        try:
            bboxes = page.get_image_rects(xref)
        except Exception:
            continue
        if not bboxes:
            continue

        # Images reused at many positions are repeated UI elements (bullets, tiles)
        if len(bboxes) > 3:
            continue

        bbox = bboxes[0]
        bw = float(bbox.width)
        bh = float(bbox.height)
        x0 = float(bbox.x0)
        y0 = float(bbox.y0)

        # --- Filtering / Classification ---
        # Fix 13: Distinguish blank virtual-canvas images from decorative full-page
        # backgrounds.  Previously all images ≥85% page size were skipped, which
        # silently dropped legitimate decorative backgrounds (sample 13 gradient).
        # Now we sample the center pixel: near-white (luminance > 200) → skip,
        # otherwise preserve as 'full_page_bg' behind-text floating image.
        category = ""
        if bw >= pw * 0.85 and bh >= ph * 0.85:
            try:
                _pix_check = _fitz.Pixmap(fitz_doc, xref)
                if _pix_check.n > 4 or _pix_check.colorspace != _fitz.csRGB:
                    _pix_check = _fitz.Pixmap(_fitz.csRGB, _pix_check)
                # Sample a 3×3 grid at 10 %/50 %/90 % of width and height.
                # A single center-pixel check incorrectly drops templates whose
                # center is white but whose edges/corners carry decorative color
                # (e.g. watercolor brush strokes).  Using grid points at 10 %
                # from the edges catches corner decorations while still skipping
                # truly blank canvases (every sampled point near-white).
                _pw2, _ph2 = _pix_check.width, _pix_check.height
                _sample_pts = [
                    (int(_pw2 * fx), int(_ph2 * fy))
                    for fx in (0.10, 0.50, 0.90)
                    for fy in (0.10, 0.50, 0.90)
                ]
                if all(
                    min(_pix_check.pixel(_sx, _sy)[:3]) > 240
                    for _sx, _sy in _sample_pts
                ):
                    continue  # all 9 sampled points near-white → blank canvas, skip
                category = "full_page_bg"
            except Exception:
                continue  # cannot sample → skip safely

        if not category:
            if bw < 25.0 or bh < 25.0:
                continue  # tiny icon (handled by _extract_icon_map)
            if bh < 4.0:
                continue  # separator / ruling line

            aspect = max(bw, bh) / max(min(bw, bh), 1.0)
            is_squarish = aspect < 2.5
            if is_squarish and bw < pw * 0.45 and bh < ph * 0.40:
                category = "profile_photo"
            elif y0 < ph * 0.25 or (y0 + bh) > ph * 0.78:
                category = "header_footer_decor"
            else:
                category = "body_decor"

        # --- Extraction ---
        try:
            pix = _fitz.Pixmap(fitz_doc, xref)
            # Ensure RGB (no CMYK, no Alpha)
            if pix.n > 4 or pix.colorspace != _fitz.csRGB:
                pix = _fitz.Pixmap(_fitz.csRGB, pix)
            if pix.alpha:
                pix = _fitz.Pixmap(pix, 0)
            png_bytes = pix.tobytes("png")
        except Exception:
            continue

        result.append(PageImageBlock(
            image_bytes=png_bytes,
            x_pt=x0,
            y_pt=y0,
            width_pt=bw,
            height_pt=bh,
            category=category,
            page_index=page_index,
        ))

    return result


# ---------------------------------------------------------------------------
# Layout extraction
# ---------------------------------------------------------------------------

def _extract_layout(doc) -> LayoutProfile:
    page = doc[0]
    rect = page.rect

    # Estimate margins by finding the minimum x0/y0 and maximum x1/y1 of
    # content blocks across the first page.
    margin_left = rect.width * 0.1
    margin_right_from_right = rect.width * 0.1
    margin_top = rect.height * 0.1
    margin_bottom_from_bottom = rect.height * 0.1

    blocks = page.get_text("dict")["blocks"]
    # Use only blocks with non-whitespace text to avoid whitespace-only spacer
    # glyphs (e.g. a single space at x=14) pulling the margin estimate too far left.
    _text_blocks = [
        blk for blk in blocks
        if blk.get("type") == 0
        and any(
            s.get("text", "").strip()
            for line in blk.get("lines", [])
            for s in line.get("spans", [])
        )
    ]
    if _text_blocks:
        xs0 = [blk["bbox"][0] for blk in _text_blocks]
        xs1 = [blk["bbox"][2] for blk in _text_blocks]
        ys1 = [blk["bbox"][3] for blk in _text_blocks]
        margin_left = max(0.0, min(xs0))
        margin_right_from_right = max(0.0, rect.width - max(xs1))
        margin_bottom_from_bottom = max(0.0, rect.height - max(ys1))

    # Top margin: use ALL type-0 blocks (including whitespace-only ones) because
    # decorative spacers / icon glyphs define the visual top of the page layout.
    # Using only non-whitespace blocks overestimates the top margin when the
    # first visible element is preceded by a spacer (e.g. Calibri-based resumes
    # with a top spacer row).  Stray whitespace glyphs at the left edge are a
    # concern for x (left margin) but not for y (top margin) in practice.
    _all_type0_y0 = [blk["bbox"][1] for blk in blocks if blk.get("type") == 0]
    if _all_type0_y0:
        margin_top = max(0.0, min(_all_type0_y0))

    # Collect font stats for default font/size
    font_counter: Counter = Counter()
    size_counter: Counter = Counter()
    for blk in blocks:
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                fn = span.get("font", "")
                sz = span.get("size", 0)
                if fn:
                    font_counter[fn] += 1
                if sz:
                    size_counter[round(sz, 1)] += 1

    raw_default_font = font_counter.most_common(1)[0][0] if font_counter else "Calibri"
    default_font = _normalize_font_name(raw_default_font) or "Calibri"
    default_size = size_counter.most_common(1)[0][0] if size_counter else 11.0

    # Two-column detection on the first page
    split_x = _detect_column_split(blocks, rect.width, rect.height)
    left_col_width_twips: int | None = None
    right_col_width_twips: int | None = None
    left_bg: str | None = None
    right_bg: str | None = None

    if split_x is not None:
        left_bg, right_bg, visual_split_x = _extract_col_info(page, split_x)
        # Use the visual sidebar edge (drawing right-edge) when available;
        # it is more accurate than the text-block gap midpoint for column widths.
        col_boundary = visual_split_x if visual_split_x is not None else split_x
        # Validate: a detected split with no background colour and no visual
        # separator may be a false positive caused by heading indentation rather
        # than a true sidebar layout.
        #
        # Heuristic: when the "right column" has very few characters (< 200)
        # AND the "left column" has proportionally far more content AND the
        # right zone x-span is narrow (< 100 pt), the split is likely an
        # indentation gap (section headings further right than body text) rather
        # than a true sidebar.
        #
        # True two-column sidebars either have a background/separator color or
        # their right zone contains substantial text (> 200 chars) or spans a
        # wide x-range (≥ 100 pt, because body text, role headers, and bullets
        # occupy different x positions in the main column).
        _suppress = False
        if left_bg is None and right_bg is None and visual_split_x is None:
            _top_cut = rect.height * 0.24 if rect.height > 0 else 0.0

            def _block_chars(b):
                return len("".join(
                    s.get("text", "") for l in b.get("lines", [])
                    for s in l.get("spans", [])
                ).strip())

            # Count per span (not per block) so that wide merged blocks
            # containing both left and right content (parallel-column layouts)
            # are correctly classified.
            _right_xs: list[float] = []
            _right_chars = 0
            _left_chars = 0
            for _sb in blocks:
                if _sb.get("type") != 0 or _sb["bbox"][1] < _top_cut:
                    continue
                for _sl in _sb.get("lines", []):
                    for _ss in _sl.get("spans", []):
                        _ssx0 = float(_ss["bbox"][0])
                        _sst = _ss.get("text", "").strip()
                        if _ssx0 >= split_x:
                            _right_chars += len(_sst)
                            _right_xs.append(_ssx0)
                        else:
                            _left_chars += len(_sst)
            _right_range = (max(_right_xs) - min(_right_xs)) if len(_right_xs) > 1 else 0

            # Suppress when right zone is thin: few chars, narrow x-span, and
            # left zone dominates in character count.
            # Threshold: 150 chars rather than 200 so that a narrow SKILLS
            # sidebar (~10 items, ~150–200 chars) is not suppressed when it
            # falls in the 60–80 % x-range zone introduced by the 0.80 guard.
            if (
                _right_chars < 150
                and _right_range < 100
                and _left_chars > _right_chars * 2.5
            ):
                _suppress = True

        if _suppress:
            col_boundary = None
            left_col_width_twips = None
            right_col_width_twips = None
        else:
            left_col_width_twips = int(col_boundary * 20)
            right_col_width_twips = int((rect.width - col_boundary) * 20)
    else:
        left_bg = right_bg = None
        col_boundary = None

    # Infer semantic table layout mode (B1).
    # "synchronized_rows" is set later in parse_pdf after section_row_table is known.
    table_layout_mode: str | None = None
    if col_boundary is not None:
        left_frac = (col_boundary / rect.width) if rect.width > 0 else 0.5
        if left_bg is not None or right_bg is not None:
            # Column with a background colour → visually distinct coloured sidebar
            table_layout_mode = "sidebar_layout"
        elif left_frac <= 0.40 or left_frac >= 0.60:
            # Notably asymmetric column widths → narrow sidebar on one side
            table_layout_mode = "sidebar_layout"
        else:
            # Roughly balanced columns with no background → independent flows
            table_layout_mode = "independent_columns"

    return LayoutProfile(
        page_width_pt=rect.width,
        page_height_pt=rect.height,
        margin_top_pt=margin_top,
        margin_bottom_pt=margin_bottom_from_bottom,
        margin_left_pt=margin_left,
        margin_right_pt=margin_right_from_right,
        default_font_name=default_font,
        default_font_size_pt=default_size,
        column_split_x=col_boundary,
        left_col_width_twips=left_col_width_twips,
        right_col_width_twips=right_col_width_twips,
        left_col_bg_color=left_bg,
        right_col_bg_color=right_bg,
        table_layout_mode=table_layout_mode,
    )


# ---------------------------------------------------------------------------
# Paragraph extraction
# ---------------------------------------------------------------------------

def _dominant_font_info(spans: list[dict]) -> dict:
    """Return the most common font name, size, bold, italic across spans."""
    font_counter: Counter = Counter()
    size_counter: Counter = Counter()
    bold_votes = 0
    italic_votes = 0
    total = 0

    for span in spans:
        fn = span.get("font", "")
        sz = round(span.get("size", 0), 1)
        flags = span.get("flags", 0)

        font_counter[fn] += 1
        if sz:
            size_counter[sz] += 1

        # Bit 4 = bold; bit 1 = italic (PyMuPDF convention)
        if (flags & (1 << 4)) or "bold" in fn.lower():
            bold_votes += 1
        if (flags & (1 << 1)) or any(
            k in fn.lower() for k in ("italic", "oblique")
        ):
            italic_votes += 1
        total += 1

    raw_font = font_counter.most_common(1)[0][0] if font_counter else None
    font_name = _normalize_font_name(raw_font)  # None if unrecognised → use doc default
    font_size_pt = size_counter.most_common(1)[0][0] if size_counter else None
    bold = bold_votes > total / 2 if total else False
    italic = italic_votes > total / 2 if total else False

    return {
        "font_name": font_name,
        "font_size_pt": font_size_pt,
        "bold": bold,
        "italic": italic,
    }


def _detect_column_split(
    blocks: list, page_width: float, page_height: float = 0.0
) -> float | None:
    """Return the x-split between two columns, or None for single-column pages.

    Collects the distinct x0 (left-edge) positions of all text blocks, sorts
    them, and looks for a gap that is ≥ 9 % of the page width.  To qualify as
    a column boundary:
      - the right edge of the gap must fall between 20 % and 70 % of the page width
      - the right-side cluster must span ≥ 30 % of the page height (ensures the
        right column has substantial content, not just a few scattered items)
      - no NARROW body block bridges the gap; full-width design elements
        (≥ 50 % of page width — headers, summary paragraphs that intentionally
        span both columns) are excluded from the bridge check

    Ignoring the top 15 % of the page for the bridging check lets us detect
    columns whose header banner (name, title, summary) spans both columns — a
    common pattern where the top area is a single full-width block and the body
    below it has a true sidebar / main-content split.

    Returns the midpoint of the detected gap as the column split x-coordinate.
    """
    # Exclude blocks in the top 24 % of the page from x0 collection.  Templates
    # with a full-width merged header (name, title, photo row) often place the
    # name text at an x0 that sits BETWEEN the two body columns (e.g. x0=235 on
    # a page whose left column is at x0=60-95 and right column at x0=320).
    # Including that block's x0 creates a spurious gap candidate and causes the
    # detector to return the wrong split.  Using body-only blocks removes those
    # false x0 anchors so the real inter-column gap is found instead.
    #
    # Additionally, use SPAN x0 positions for the distribution analysis instead
    # of block x0 positions.  PyMuPDF sometimes merges adjacent two-column
    # headings at the same Y into a single wide block (e.g. "Experience Education"
    # from x=65 to x=383).  That merged block's x0 (65) is correct for the left
    # column, but it hides the right column's x0 (305) from the gap detection.
    # Using span-level x0 positions captures both column starts from the same
    # merged block, so the true inter-column gap is visible.  Wide BLOCKS (≥ 50 %
    # page width) are still kept in the bridging check as before.
    top_cutoff = page_height * 0.24 if page_height > 0 else 0.0
    # Build a frequency map of x0 positions (rounded to nearest int) across all
    # body blocks (y >= top_cutoff).  An x0 position that appears in only ONE
    # block is likely a right-aligned element within a column (e.g. a date
    # "2023" at x=233 in a column whose body starts at x=65).  True column
    # starts appear in multiple blocks (section headings, role headers, bullets,
    # body text all share the same left edge).
    # Positions appearing in >= 2 blocks are kept as column-boundary candidates.
    _x0_freq: dict[int, int] = {}
    for blk in blocks:
        if blk.get("type") != 0 or blk["bbox"][1] < top_cutoff:
            continue
        rx0 = round(blk["bbox"][0])
        _x0_freq[rx0] = _x0_freq.get(rx0, 0) + 1
    # Always keep positions with 2+ blocks; also allow isolated positions that
    # are within 5 pt of another position (they form the same cluster).
    _kept: set[int] = set()
    for rx0, cnt in _x0_freq.items():
        if cnt >= 2:
            _kept.add(rx0)
    # Add singleton positions that are clustered with a kept position
    for rx0, cnt in _x0_freq.items():
        if cnt < 2:
            if any(abs(rx0 - k) <= 5 for k in _kept):
                _kept.add(rx0)
    x0s = sorted(_kept)
    if len(x0s) < 2:
        return None

    min_gap = page_width * 0.08

    # Guard against 3-column (or more) layouts: count significant gaps that are
    # ADJACENT to each other (i.e. the second gap starts at or before the first
    # gap's right edge).  This distinguishes a true 3-col layout like
    # x=[42,240,410] (gaps at 42→240 and 240→410, adjacent) from a 2-col
    # sidebar where the right column contains varying indent levels that happen
    # to produce a large secondary gap further to the right (e.g. x=[55,237,…]
    # with a gap at 55→237 and an unrelated indent gap at 271→389, which is
    # entirely within the right column and should not veto the sidebar split).
    _first_right: int | None = None
    _adjacent_sig = 0
    for _j in range(len(x0s) - 1):
        if (
            x0s[_j + 1] - x0s[_j] >= min_gap
            and page_width * 0.20 <= x0s[_j + 1] <= page_width * 0.70
        ):
            if _first_right is None:
                _first_right = x0s[_j + 1]
                _adjacent_sig = 1
            elif x0s[_j] <= _first_right:
                _adjacent_sig += 1
    if _adjacent_sig >= 2:
        return None
    # Blocks spanning ≥ 45 % of the page width are treated as cross-column
    # design elements (e.g. name banner, summary paragraph, or PyMuPDF-merged
    # two-column headings like "Experience Education" at x0=65 x1=350).
    # These are excluded from the bridging check so they do not veto a valid
    # column split.  45 % rather than 50 % catches narrower merged blocks that
    # still straddle the column boundary.
    wide_block_min = page_width * 0.45

    for i in range(len(x0s) - 1):
        gap = x0s[i + 1] - x0s[i]
        right_edge = x0s[i + 1]
        if gap >= min_gap and page_width * 0.20 <= right_edge <= page_width * 0.80:
            # Require the right-side content cluster to span a meaningful
            # fraction of the page height so that a handful of right-aligned
            # header items (contact, date) don't trigger false column detection.
            if page_height > 0:
                # Use body blocks only (exclude header / footer bands) so that
                # a single contact line + page-number footer don't create a
                # falsely large vertical span and trigger two-column detection.
                body_top = page_height * 0.15
                body_bot = page_height * 0.90
                right_cluster = [
                    b for b in blocks
                    if b.get("type") == 0
                    and round(b["bbox"][0]) >= right_edge
                    and b["bbox"][1] >= body_top
                    and b["bbox"][3] <= body_bot
                ]
                if not right_cluster:
                    continue
                ry_span = (
                    max(b["bbox"][3] for b in right_cluster)
                    - min(b["bbox"][1] for b in right_cluster)
                ) / page_height
                if ry_span < 0.30:
                    continue

            # Reject if any BODY block bridges the gap: starts in the left
            # "column" and extends at least 5 % past the right edge.  Blocks
            # in the top 24 % (header banner) are excluded.  Full-width blocks
            # (≥ 50 % page width) are also excluded — they are intentional
            # cross-column design elements, not evidence of a single column.
            bridge_x1_threshold = right_edge * 1.05
            bridging = any(
                round(b["bbox"][0]) <= x0s[i]
                and b["bbox"][2] >= bridge_x1_threshold
                and b["bbox"][1] >= top_cutoff
                and (b["bbox"][2] - b["bbox"][0]) < wide_block_min
                for b in blocks if b.get("type") == 0
            )
            if not bridging:
                # When left-column body text extends well past its x0 start
                # (e.g. "PROFESSIONAL EXPERIENCE" at x0=78 but x1=366) the
                # simple x0-gap midpoint under-estimates the left column width.
                # Use the midpoint of the *content* gap instead — but only when
                # max_left_x1 stays strictly below right_edge (i.e. left content
                # does not actually overlap the right column).
                # Exclude full-width cross-column blocks so a contact line at
                # x0=60 x1=530 does not inflate max_left_x1 to 530 and prevent
                # the refinement from kicking in on the real body gap.
                x0_mid = (x0s[i] + x0s[i + 1]) / 2.0
                _body_bot_lbs = page_height * 0.90 if page_height > 0 else float("inf")
                left_body_x1s = [
                    b["bbox"][2] for b in blocks
                    if b.get("type") == 0
                    and round(b["bbox"][0]) <= x0s[i]  # round() matches how x0s was built
                    and b["bbox"][1] >= top_cutoff
                    and b["bbox"][3] <= _body_bot_lbs  # exclude footer blocks
                    and (b["bbox"][2] - b["bbox"][0]) < wide_block_min
                ]
                if left_body_x1s:
                    max_left_x1 = max(left_body_x1s)
                    if max_left_x1 < right_edge:
                        content_mid = (max_left_x1 + right_edge) / 2.0
                        return max(x0_mid, content_mid)
                    return x0_mid
                # No non-wide, non-footer content left of the gap: only full-width
                # blocks or footer items on the left — not a real sidebar column.

    # Secondary detection: right sidebar by vertical content band.
    # Used when the gap-based method fails because wide body text blocks bridge
    # the gap (e.g. experience paragraphs that physically span into the right
    # sidebar x-zone even though they visually stay in the left column).
    # NOTE: the tertiary (parallel-column) detection runs after this one and
    # is only reached when both the primary gap detection and this secondary
    # sidebar detection fail.
    #
    # Look for body spans with x0 in [45%, 85%] of page width that collectively
    # span a significant vertical extent AND are not simply right-aligned dates
    # (date columns pair their items tightly with left-column content at the same Y).
    if page_height > 0:
        _body_h = max(page_height - top_cutoff, 1.0)
        _sbar_lo = page_width * 0.45
        _sbar_hi = page_width * 0.85
        _sbar_spans: list[tuple[float, float]] = []  # (x0, y0)
        _left_y_buckets: set[int] = set()
        for _blk in blocks:
            if _blk.get("type") != 0 or _blk["bbox"][1] < top_cutoff:
                continue
            for _line in _blk.get("lines", []):
                for _span in _line.get("spans", []):
                    if not _span.get("text", "").strip():
                        continue
                    _sx0 = _span["bbox"][0]
                    _sy0 = _span["bbox"][1]
                    if _sy0 < top_cutoff:
                        continue
                    if _sbar_lo <= _sx0 <= _sbar_hi:
                        _sbar_spans.append((_sx0, _sy0))
                    elif _sx0 < page_width * 0.40:
                        _left_y_buckets.add(round(_sy0 / 10))

        if len(_sbar_spans) >= 3:
            _sbar_ys = [y for _, y in _sbar_spans]
            _sbar_coverage = (max(_sbar_ys) - min(_sbar_ys)) / _body_h
            if _sbar_coverage >= 0.25:
                # Guard: reject if the majority of sidebar spans are Y-paired
                # with left-column content — that pattern is a date column, not
                # a sidebar (dates appear alongside role headers at the same Y).
                _paired = sum(
                    1 for _, y in _sbar_spans
                    if round(y / 10) in _left_y_buckets
                )
                if _paired / len(_sbar_spans) < 0.45:
                    # Require at least 3 distinct Y buckets (20 pt grid).
                    _y_buckets = {round(y / 20) for _, y in _sbar_spans}
                    if len(_y_buckets) >= 3:
                        _min_x0 = min(x for x, _ in _sbar_spans)
                        _split = max(_min_x0 - 15.0, page_width * 0.20)
                        if _split <= page_width * 0.85:
                            return _split
    # Tertiary detection: parallel two-column layout.
    # Detects templates where PyMuPDF merges same-y left+right spans into
    # single wide blocks (e.g. EDUCATION left / EDUCATION right at identical
    # y-coordinates).  Right-column paragraphs have no section headings, so
    # _redistribute_parallel_body (called after _group_sections) re-assigns
    # them to the left-column sections by y-range.
    if page_height > 0:
        _wide_min = page_width * 0.40
        _left_zone = page_width * 0.40
        _right_zone = page_width * 0.50
        _par_gaps: list[tuple[float, float]] = []  # (left_cluster_x1, right_cluster_x0)
        for _blk in blocks:
            if _blk.get("type") != 0 or _blk["bbox"][1] < top_cutoff:
                continue
            if (_blk["bbox"][2] - _blk["bbox"][0]) < _wide_min:
                continue
            _lx1s: list[float] = []
            _rx0s: list[float] = []
            for _line in _blk.get("lines", []):
                for _span in _line.get("spans", []):
                    if not _span.get("text", "").strip():
                        continue
                    _sx0, _sx1 = _span["bbox"][0], _span["bbox"][2]
                    if _sx0 < _left_zone:
                        _lx1s.append(_sx1)
                    elif _sx0 > _right_zone:
                        _rx0s.append(_sx0)
            if _lx1s and _rx0s:
                _par_gaps.append((max(_lx1s), min(_rx0s)))
        if len(_par_gaps) >= 3:
            _sorted_left = sorted(l for l, _ in _par_gaps)
            _sorted_right = sorted(r for _, r in _par_gaps)
            _med_left = _sorted_left[len(_sorted_left) // 2]
            _med_right = _sorted_right[len(_sorted_right) // 2]
            if _med_right - _med_left >= page_width * 0.05:
                _split = (_med_left + _med_right) / 2.0
                if page_width * 0.25 <= _split <= page_width * 0.75:
                    log.debug(
                        "PARALLEL_COLUMNS_DETECTED split_x=%.1f (%.0f%% of page) "
                        "from %d wide-block gaps",
                        _split, _split / page_width * 100, len(_par_gaps),
                    )
                    return _split

    return None


def _extract_paragraphs(
    doc, hf_texts: set[str], margin_left: float, layout: "LayoutProfile",
    skip_pages: set[int] | None = None,
) -> list[ParaModel]:
    """Extract all content paragraphs from the document, skipping H/F text.

    When the document has a two-column layout (layout.column_split_x is not
    None), each paragraph is tagged with column_id='left' or 'right' and its
    indent_left_pt is measured from the column's left edge rather than the
    page's left edge.  This prevents right-column bullets from inheriting a
    huge page-relative indent that overflows into the left column.
    """
    paras: list[ParaModel] = []
    prev_block_y1: float | None = None
    hf_texts_lower = {t.lower() for t in hf_texts}
    # layout.column_split_x is the visual sidebar boundary (drawing right-edge);
    # used as the authoritative right-column indent origin.
    layout_split_x = layout.column_split_x  # may be None
    _skip = skip_pages or set()

    for page in doc:
        if page.number in _skip:
            continue
        page_margin_left = margin_left
        blocks = page.get_text("dict")["blocks"]

        # Re-estimate margin_left per page (non-whitespace blocks only so that
        # bare-space glyphs at a small x don't inflate the indent for real content).
        xs0 = [
            blk["bbox"][0] for blk in blocks
            if blk.get("type") == 0
            and any(
                s.get("text", "").strip()
                for line in blk.get("lines", [])
                for s in line.get("spans", [])
            )
        ]
        if xs0:
            page_margin_left = max(0.0, min(xs0))

        # Section heading background bands on this page (for background_color).
        section_bg_rects = _extract_section_bg_rects(page) if layout_split_x is not None else []

        # Icon drawings in the left sidebar (for inline_image_bytes).
        icon_map = _extract_icon_map(page, layout_split_x)

        # Small bullet-dot drawings (3–5 pt filled circles) that mark list
        # items without a text prefix (skills, languages, certifications).
        bullet_dot_ys = _extract_bullet_dot_ys(page)

        # Two-column detection: reorder blocks so the left column is read
        # completely before the right column.  PyMuPDF's default ordering is
        # top-to-bottom across ALL columns, interleaving sidebar content with
        # main content.  Column-aware reordering restores correct reading order.
        # split_x (gap midpoint) is used for block classification (left vs right).
        # Use the layout's authoritative split_x.  When the layout suppressed
        # the split (layout_split_x=None after false-positive validation), use
        # None for all pages so that blocks are never mis-split into left/right
        # columns.  When the layout detected a real two-column boundary, prefer
        # it for consistency (avoids per-page drift); fall back to per-page
        # detection for pages whose content shifts the split slightly.
        if layout_split_x is None:
            split_x = None   # layout decided single-column; don't re-detect
        else:
            split_x = _detect_column_split(blocks, page.rect.width, page.rect.height)
            if split_x is None:
                split_x = layout_split_x
        # right_col_origin: indent is measured from the visual sidebar edge
        # (layout_split_x), which is more accurate than the gap midpoint.
        right_col_origin = layout_split_x if layout_split_x is not None else split_x
        right_col_start_idx: int | None = None
        if split_x is not None:
            left_blks = sorted(
                [b for b in blocks if b.get("type") == 0 and b["bbox"][0] < split_x],
                key=lambda b: b["bbox"][1],
            )
            right_blks = sorted(
                [b for b in blocks if b.get("type") == 0 and b["bbox"][0] >= split_x],
                key=lambda b: b["bbox"][1],
            )
            blocks = left_blks + right_blks
            right_col_start_idx = len(left_blks)

        # Merged-header-band detection: when NO right-column text block exists
        # in the top 22 % of the page the template uses a full-width header
        # banner (name, title, summary) above the two-column body.  Every block
        # in that top band is forced to col_id=None so the renderer places it
        # above the two-column table rather than inside the left cell.
        # When right-column content IS present near the top (sidebar starts at
        # page top with no banner) this is skipped and the normal x-width
        # heuristic applies.
        #
        # Extended detection: some templates have a full-width banner whose text
        # x1 < split_x+20 (e.g. "Lydia Mary" x1=199 on a split_x=276 page),
        # so the cross-column x1 heuristic cannot detect it.  If the right column
        # starts meaningfully below the left column's first block (gap > 50 pt),
        # use the first right-column block's Y as the merged-header boundary.
        _header_band_y = page.rect.height * 0.22 if split_x is not None else 0.0
        _has_right_in_header = split_x is not None and any(
            b.get("type") == 0
            and b["bbox"][0] >= split_x
            and b["bbox"][1] < _header_band_y
            for b in blocks
        )

        _first_right_y = (
            min(
                (b["bbox"][1] for b in blocks
                 if b.get("type") == 0 and b["bbox"][0] >= split_x),
                default=_header_band_y,
            )
            if split_x is not None
            else _header_band_y
        )
        # Detect the "narrow-banner" case: right column starts meaningfully below
        # the topmost left-column block (gap > 50 pt) and is still within the 22%
        # zone (so the normal _has_right_in_header guard fires).
        # Minimum gap (pt) required between the left-column header content and
        # the first right-column block.  This prevents left-column content that
        # starts at nearly the same Y as the right column (e.g. "About Me" at
        # y=135.5 vs "Experiences" at y=135.6) from being treated as a merged
        # header element.
        _MIN_HEADER_GAP = 15

        _has_left_above_right = (
            split_x is not None
            and _has_right_in_header          # right IS in the top zone
            and _first_right_y > 50           # right col starts meaningfully into page
            and any(
                b.get("type") == 0
                and b["bbox"][0] < split_x    # left-area block
                and b["bbox"][1] < _first_right_y - _MIN_HEADER_GAP  # clearly above
                for b in blocks
            )
        )

        # Merged-header-band forcing is only valid for page 0 (the first page
        # that has the candidate's name, title, summary at the very top).
        # On continuation pages (page.number > 0) the top zone is simply the
        # next paragraph of content; forcing it to col=None would place old
        # template experience/education entries above the two-column table.
        merged_header_band = (
            _first_right_y - _MIN_HEADER_GAP if _has_left_above_right
            else _header_band_y if (
                split_x is not None and not _has_right_in_header
                and page.number == 0
            )
            else 0.0
        )

        # Reset inter-page spacing
        prev_block_y1 = None

        for blk_idx, blk in enumerate(blocks):
            # Reset spacing at the left→right column transition so the first
            # right-column block doesn't inherit a huge space_before from the
            # last left-column block (which can be much lower on the page).
            if right_col_start_idx is not None and blk_idx == right_col_start_idx:
                prev_block_y1 = None
            if blk.get("type") != 0:
                continue

            # Collect all spans and per-line texts
            all_spans: list[dict] = []
            line_entries: list[tuple[str, list[dict], float, float, float, float]] = []  # (text, spans, line_y0, line_x0, line_y1, line_x1)

            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                line_text, _norm_artifact = _normalize_spans(spans)
                if line_text:
                    # Collect per-line y0/y1 for accurate _has_bullet_dot matching
                    # and per-line space_before computation.
                    _line_bbox = line.get("bbox")
                    _line_y0 = float(_line_bbox[1]) if _line_bbox else 0.0
                    _line_y1 = float(_line_bbox[3]) if _line_bbox else _line_y0 + 12.0
                    _line_x0 = float(_line_bbox[0]) if _line_bbox else float(blk["bbox"][0])
                    _line_x1 = float(_line_bbox[2]) if _line_bbox else float(blk["bbox"][2])
                    # Coalesce same-y-row fragments: PDFs with font-switch mid-line
                    # (e.g. hyperlinks, styled email addresses) produce multiple
                    # PyMuPDF "lines" at identical y bounds for one visual text row.
                    # Concatenating them avoids emitting one paragraph per fragment,
                    # which multiplies line height and causes page overflow.
                    # Guard: only coalesce if the new fragment's x0 is within 20pt
                    # of the previous fragment's x1 (i.e. they are adjacent or
                    # overlapping in x).  This prevents merging same-y content from
                    # separate layout columns (e.g. a sidebar label and a main-area
                    # value that happen to share the same y coordinate).
                    _prev_x1 = (
                        line_entries[-1][1][-1]["bbox"][2]  # last span's x1
                        if line_entries and line_entries[-1][1]
                        else -999.0
                    )
                    if (
                        line_entries
                        and abs(_line_y0 - line_entries[-1][2]) < 1.0
                        and abs(_line_y1 - line_entries[-1][4]) < 1.0
                        and _line_x0 - _prev_x1 < 20.0
                    ):
                        # Merge into the previous entry: sort by x0, concatenate.
                        prev_text, prev_spans, prev_y0, prev_x0, prev_y1, prev_x1 = line_entries[-1]
                        if _line_x0 >= prev_x0:
                            merged_text = prev_text + " " + line_text
                            merged_x0 = prev_x0
                        else:
                            merged_text = line_text + " " + prev_text
                            merged_x0 = _line_x0
                        merged_x1 = max(prev_x1, _line_x1)
                        line_entries[-1] = (merged_text.strip(), prev_spans + spans, prev_y0, merged_x0, prev_y1, merged_x1)
                        all_spans.extend(spans)
                    else:
                        line_entries.append((line_text, spans, _line_y0, _line_x0, _line_y1, _line_x1))
                        all_spans.extend(spans)

            if not line_entries:
                prev_block_y1 = blk["bbox"][3]
                continue

            bbox = blk["bbox"]
            x0, y0, x1_blk, y1 = bbox[0], bbox[1], bbox[2], bbox[3]

            # Skip headers/footers (check full block text and single-line join,
            # both exact and case-insensitive)
            full_text = "\n".join(t for t, *_ in line_entries)
            single_line = " ".join(t for t, *_ in line_entries)
            if (
                single_line in hf_texts
                or full_text in hf_texts
                or single_line.lower() in hf_texts_lower
                or full_text.lower() in hf_texts_lower
            ):
                prev_block_y1 = y1
                continue

            # Space before = Y gap from previous block (applied to first line only)
            space_before = 0.0
            if prev_block_y1 is not None and y0 > prev_block_y1:
                space_before = y0 - prev_block_y1
            prev_block_y1 = y1

            fi = _dominant_font_info(all_spans)

            # Determine column membership and column-relative indent.
            # For right-column blocks, indent is measured from right_col_origin
            # (the visual sidebar edge) so that a block at x=237 on a page
            # with a 215-pt sidebar gets indent = 22 pt, not page-relative 222 pt.
            # Cross-column detection: a block whose x0 is in the left area but
            # whose x1 extends more than 20 pt past the column split is usually
            # a full-width element (merged name/header banner), col_id=None.
            # Exception: section-row table layouts have left section labels and
            # right body text at the same Y, so PyMuPDF merges them into one
            # block.  For these blocks we defer column assignment to per-line
            # basis (_cross_col_block=True) so each line gets its true col_id.
            _cross_col_block = False
            if merged_header_band > 0 and y0 < merged_header_band:
                # Block is inside the top merged-header band (no right-column
                # content exists there): treat as full-width above the table.
                col_id: str | None = None
                col_origin = page_margin_left
            elif split_x is not None and x0 >= split_x:
                col_id = "right"
                col_origin = right_col_origin if right_col_origin is not None else split_x
            elif split_x is not None and x1_blk > split_x + 20.0:
                # Cross-column block below the header band: split per line.
                col_id = None
                col_origin = page_margin_left
                _cross_col_block = True
            elif split_x is not None:
                col_id = "left"
                col_origin = page_margin_left
            else:
                col_id = None
                col_origin = page_margin_left

            # Dominant text color across all spans in the block
            block_text_color = _dominant_text_color(all_spans)

            # Background color: match block centre-y against section band rects.
            # Also require x-range overlap to avoid cross-column false matches.
            block_bg_color: str | None = None
            block_cy = (y0 + y1) / 2
            for bx0, by0, bx1, by1, band_color in section_bg_rects:
                if by0 <= block_cy <= by1 and bx0 < x1_blk and bx1 > x0:
                    block_bg_color = band_color
                    break

            # Emit one ParaModel per line.
            # Each line within a block inherits the block's font profile.
            # space_before_pt is computed per-line from actual y-gaps so that
            # inter-section spacing baked into the source PDF is preserved in
            # the output DOCX (critical for the evaluator's block-splitting).
            prev_line_y1_in_block: float | None = None
            for line_idx, (line_text, line_spans, line_y0, line_x0, line_y1, line_x1) in enumerate(line_entries):
                # Skip per-line H/F matches (case-insensitive)
                if (
                    line_text in hf_texts
                    or line_text.lower() in hf_texts_lower
                ):
                    continue
                # Skip section continuation markers ("EXPERIENCE (cont.)")
                if _CONT_RE.search(line_text) and len(line_text) < 80:
                    continue
                line_fi = _dominant_font_info(line_spans) if line_spans else fi
                line_text_color = (
                    _dominant_text_color(line_spans) if line_spans else block_text_color
                )
                # For cross-column blocks (section-row table pairs), assign
                # each line its own column based on line_x0 vs split_x.
                if _cross_col_block and split_x is not None:
                    _line_col_id: str | None = "right" if line_x0 >= split_x else "left"
                    _line_col_origin = (
                        (right_col_origin if right_col_origin is not None else split_x)
                        if _line_col_id == "right"
                        else page_margin_left
                    )
                else:
                    _line_col_id = col_id
                    _line_col_origin = col_origin

                # Attach icon image to first line only (line_idx == 0) for
                # left-column paragraphs that coincide with a sidebar icon.
                icon_png: bytes | None = None
                icon_size_pt: float = 0.0
                if line_idx == 0 and _line_col_id == "left" and icon_map:
                    icon_png = _match_icon(icon_map, y0, y1)
                    if icon_png is not None:
                        icon_size_pt = min(y1 - y0, x1_blk - x0)

                # For PUA-only lines (standalone bullet glyphs like \uf0b7),
                # always use per-line x0 so the bullet marker's true indent is
                # captured (e.g. 54pt vs block x0 of 36pt).
                # For non-PUA lines with any positive line-x0 delta, also use
                # per-line x0 to preserve indentation (e.g. body text at x=72
                # or skills values at x=144 inside a block starting at x=36).
                _line_pua_only = bool(line_text.strip()) and all(
                    0xE000 <= ord(c) <= 0xF8FF or c.isspace() for c in line_text
                )
                if _line_pua_only or line_x0 > x0:
                    _indent_x = line_x0
                else:
                    _indent_x = x0
                # Track per-line y-gap but build profile with OLD space_before
                # so _infer_semantic sees the same values as before (preventing
                # reclassification of non-heading items via the well_spaced flag).
                # We patch space_before_pt to the per-line value only AFTER
                # semantic inference confirms the paragraph is a section_heading.
                if prev_line_y1_in_block is not None and line_y0 > prev_line_y1_in_block:
                    _per_line_sb = line_y0 - prev_line_y1_in_block
                else:
                    _per_line_sb = 0.0
                prev_line_y1_in_block = line_y1
                profile = ParagraphProfile(
                    font_name=line_fi["font_name"],
                    font_size_pt=line_fi["font_size_pt"],
                    bold=line_fi["bold"],
                    italic=line_fi["italic"],
                    indent_left_pt=max(0.0, _indent_x - _line_col_origin),
                    body_text_x0_pt=line_x0,
                    space_before_pt=space_before if line_idx == 0 else 0.0,
                    text_color=line_text_color,
                    background_color=block_bg_color,
                    column_id=_line_col_id,
                    inline_image_bytes=icon_png,
                    inline_image_size_pt=icon_size_pt,
                    y_top_pt=line_y0,
                    x_pt=line_x0,
                    y_pt=line_y0,
                    width_pt=max(0.0, line_x1 - line_x0),
                    height_pt=max(0.0, line_y1 - line_y0),
                    page_num=page.number,
                    block_id=f"p{page.number}_b{blk_idx}",
                )
                pm = ParaModel(
                    text=line_text,
                    style=ParaStyle(),
                    semantic="",
                    paragraph_profile=profile,
                )
                pm.semantic = _infer_semantic(pm)
                _geo_log.debug(
                    "GEOMETRY_CAPTURED block=%s page=%d x=%.0f y=%.0f w=%.0f h=%.0f "
                    "col=%s semantic=%s text=%.40r",
                    profile.block_id, page.number,
                    profile.x_pt, profile.y_pt, profile.width_pt, profile.height_pt,
                    profile.column_id, pm.semantic, line_text,
                )
                # Content paragraphs (bullets, body text, date lines) should not carry
                # text_color from the PDF template — those colors come from hyperlinks
                # or author styling and must not bleed onto LLM-generated content.
                # Section headings and role_headers keep their accent color (design intent).
                # Bullet/paragraph colors are stripped here; any color that bleeds via
                # clone_as archetypes is caught by the post-render sweep in pipeline.py.
                if pm.semantic in ("bullet", "paragraph", "role_meta") and pm.paragraph_profile:
                    pm.paragraph_profile.text_color = None
                # Role headers can have mixed-bold text (e.g. "Title | Company | Date"
                # where only the title is bold).  Build per-run (text, bold) pairs
                # so para_builder can render each portion with the correct weight.
                if pm.semantic == "role_header" and pm.paragraph_profile:
                    _runs: list[tuple[str, bool]] = []
                    _cur_text = ""
                    _cur_bold: bool | None = None
                    for _s in line_spans:
                        _s_text = _s.get("text", "")
                        if not _s_text:
                            continue
                        _s_bold = bool(
                            (_s.get("flags", 0) & (1 << 4))
                            or "bold" in _s.get("font", "").lower()
                        )
                        if _cur_bold is None:
                            _cur_bold = _s_bold
                        if _s_bold == _cur_bold:
                            _cur_text += _s_text
                        else:
                            if _cur_text:
                                _runs.append((_cur_text, _cur_bold))
                            _cur_text = _s_text
                            _cur_bold = _s_bold
                    if _cur_text and _cur_bold is not None:
                        _runs.append((_cur_text, _cur_bold))
                    if len({b for _, b in _runs}) > 1:
                        # Mixed bold — store runs; set profile.bold from first run
                        pm.paragraph_profile.text_runs = _runs
                        pm.paragraph_profile.bold = _runs[0][1]
                    elif _runs:
                        # Uniform bold — just update profile.bold
                        pm.paragraph_profile.bold = _runs[0][1]
                # Bullet dots: small filled circle drawings (3–5 pt) that mark
                # list items lacking a text prefix (skills, languages, certs).
                # Use per-line y0 so only the first line of each bullet item is
                # promoted; continuation lines (wrapped at a different y) are left
                # as paragraphs and later merged by _merge_bullet_continuations.
                if pm.semantic == "paragraph" and _has_bullet_dot(bullet_dot_ys, line_y0):
                    pm.semantic = "bullet"
                # Normalize bullet text: strip leading bullet prefix so the IR
                # stores bare content, consistent with DOCX-parsed paragraphs.
                if pm.semantic == "bullet":
                    _BULLET_STRIP_PREFIXES = (
                        "- ", "• ", "· ", "– ", "* ", "▪ ", "▸ ", "◦ ",
                        "→ ", "› ", "● ", "○ ", "■ ", "□ ", "◆ ", "◇ ",
                        "✓ ", "✔ ", "✗ ", "✘ ",
                    )
                    for _pfx in _BULLET_STRIP_PREFIXES:
                        if pm.text.startswith(_pfx):
                            pm.text = pm.text[len(_pfx):]
                            break
                # Bullet formatting: strip PUA prefix chars (e.g. '\uf0b7 ' or '\uf0b7')
                if pm.semantic == "bullet" and pm.text and 0xE000 <= ord(pm.text[0]) <= 0xF8FF:
                    # Strip PUA char + optional trailing space
                    rest = pm.text[1:]
                    if rest.startswith(" "):
                        rest = rest[1:]
                    pm.text = rest
                # Bullet layout: in single-column docs let ListParagraph style
                # control indentation (adding a PDF-based indent on top creates
                # double indentation).  Also cap space_before to avoid excessive
                # vertical gaps between consecutive bullet items.
                if pm.semantic == "bullet" and pm.paragraph_profile:
                    if col_id is None:
                        pm.paragraph_profile.indent_left_pt = 0.0
                    if pm.paragraph_profile.space_before_pt > 3.0:
                        pm.paragraph_profile.space_before_pt = 3.0
                # Section headings: ensure minimum spacing for evaluator block
                # detection (needs baseline_gap > ~1.4 × median_lh ≈ 16 pt;
                # 6 pt + 11 pt line height = 17 pt clears the threshold),
                # and cap maximum to prevent PDF absolute-position gaps from
                # inflating DOCX flow-layout height.
                # Cap raised to 10 pt (was 6 pt): with Priority 1 bullet
                # compaction in place, templates with generous inter-section
                # spacing (8–10 pt) no longer risk overflow, and the extra
                # breathing faithfully reproduces the original visual rhythm.
                if pm.semantic == "section_heading" and pm.paragraph_profile:
                    _heading_sb = min(_per_line_sb, 10.0)
                    if _heading_sb > pm.paragraph_profile.space_before_pt:
                        pm.paragraph_profile.space_before_pt = _heading_sb
                    elif pm.paragraph_profile.space_before_pt > 10.0:
                        pm.paragraph_profile.space_before_pt = 10.0
                    # Preserve the left-column heading indent so it aligns
                    # with the original PDF position.  The renderer adds the
                    # page left-margin offset on top, placing the heading at
                    # margin + indent (matching the source template layout).
                    # Zeroing was previously used to prevent cell overflow, but
                    # the fresh-Document rendering path avoids that concern.
                # Global cap: PDF absolute-position inter-block gaps inflate
                # DOCX flow-layout height.  Apply per-type limits:
                #   role_header: 6 pt max (was 4 pt — with bullet compaction,
                #     inter-role spacing can faithfully reflect the original)
                #   two-col role_header: same 6 pt cap
                #   single-column body paras (paragraph, role_meta, …): 1 pt max
                #     to avoid source PDF body-text gaps (often 2–4 pt) from
                #     accumulating across 30–50 paragraphs and overflowing the
                #     DOCX page.  Two-column paragraphs keep a larger cap (3 pt)
                #     because changing their spacing destabilises LibreOffice's
                #     column layout measurement.
                elif pm.paragraph_profile and pm.semantic not in (
                    "bullet", "section_heading"
                ):
                    sb = pm.paragraph_profile.space_before_pt
                    _is_two_col = pm.paragraph_profile.column_id in ("left", "right")
                    if pm.semantic == "role_header":
                        if sb > 6.0:
                            pm.paragraph_profile.space_before_pt = 6.0
                    elif _is_two_col:
                        if sb > 3.0:
                            pm.paragraph_profile.space_before_pt = 3.0
                    elif sb > 1.0:
                        pm.paragraph_profile.space_before_pt = 1.0
                paras.append(pm)

    return paras


# ---------------------------------------------------------------------------
# Semantic inference (PDF-adapted)
# ---------------------------------------------------------------------------

def _infer_semantic(pm: ParaModel) -> str:
    text = pm.text.strip()
    pp = pm.paragraph_profile

    if not text:
        return "empty"

    # Lines consisting only of Private Use Area codepoints (U+E000–U+F8FF) have
    # no semantic content — they are visual icon glyphs (Fontawesome, Symbol,
    # Dingbats) emitted as isolated text runs by some PDF generators.
    if all(0xE000 <= ord(c) <= 0xF8FF or c.isspace() for c in text):
        return "empty"

    # Line starting with a Private Use Area character followed by text: treat
    # as a bullet (e.g. '\uf0b7 Designed ...' or '\uf0b7Designed ...' from
    # Symbol-font bullets).  Accept both spaced and unspaced PUA prefix.
    if text and 0xE000 <= ord(text[0]) <= 0xF8FF:
        # Single PUA char (visual icon only) already handled by all-PUA check above.
        # Here: PUA prefix + text content — treat as bullet.
        if len(text) == 1 or not (0xE000 <= ord(text[1]) <= 0xF8FF):
            return "bullet"

    bold = pp.bold if pp else False
    font_size = pp.font_size_pt if pp else None
    space_before = pp.space_before_pt if pp else 0.0
    indent = pp.indent_left_pt if pp else 0.0

    # Known section names — normalised match handles:
    #   ALL-CAPS non-bold   : "WORK EXPERIENCE" → "workexperience"
    #   Letter-spaced bold  : "S U M M A R Y"   → "summary"
    #   Numbered bold/caps  : "1. Professional Summary" → "professionalsummary"
    # Guard: require bold OR all-alpha chars are uppercase (ALL-CAPS heading).
    _text_norm = _normalize_heading_text(text)
    _text_alpha = re.sub(r"[^a-zA-Z]", "", text)
    _is_uppercase = bool(_text_alpha) and _text_alpha.upper() == _text_alpha
    if (bold or _is_uppercase) and _text_norm in _ALL_HEADING_NAMES_NOSPACE:
        return "section_heading"

    # Section heading: bold, short, title-case, 2+ words, larger or spaced.
    # Exclude commas: company/location lines ("Software Inc, Vancouver") have
    # commas; resume section headings do not.
    # Exclude lines ending with a preposition/conjunction ("Graduated with",
    # "University of") and lines ending with a mid-sentence period followed
    # by more text ("Design." fragments from body sentences).
    _HEADING_TRAILING_STOPWORDS = frozenset({
        "with", "and", "the", "for", "of", "in", "at", "to", "by",
        "or", "a", "an", "on", "as", "is", "are", "was", "were",
    })
    if (
        bold
        and len(text) <= 60
        and "|" not in text
        and "," not in text
        and not text.startswith(("-", "•", "·", "–", "*"))
    ):
        words = text.split()
        _last_word = words[-1].lower().rstrip(".,;:") if words else ""
        # Sentence fragments end with "." — real headings never do.
        _ends_with_period = bool(words) and words[-1].endswith(".")
        if len(words) >= 2 and _last_word not in _HEADING_TRAILING_STOPWORDS and not _ends_with_period:
            cap_ratio = (
                sum(1 for w in words if w and w[0].isupper()) / len(words)
            )
            larger_font = font_size is not None and font_size >= 12.0
            well_spaced = space_before >= 4.0
            if cap_ratio >= 0.7 and (larger_font or well_spaced):
                return "section_heading"

    # Role header: contains " | " pipe separator
    if "|" in text and not text.startswith(("-", "•")):
        return "role_header"

    # Bullet: starts with a bullet/list prefix character (common and extended set)
    _BULLET_PREFIXES = (
        "- ", "• ", "· ", "– ", "* ", "▪ ", "▸ ", "◦ ", "→ ", "› ",
        "● ", "○ ", "■ ", "□ ", "◆ ", "◇ ", "✓ ", "✔ ", "✗ ", "✘ ",
    )
    if text.startswith(_BULLET_PREFIXES):
        return "bullet"

    # Date/location meta line: contains a year (or placeholder like "20XX"), short, no pipe
    if (_YEAR_RE.search(text) or _DATE_PLACEHOLDER_RE.search(text)) and len(text) <= 80 and "|" not in text:
        return "role_meta"

    return "paragraph"


# ---------------------------------------------------------------------------
# Section grouping (mirrors docx_parser logic)
# ---------------------------------------------------------------------------

_EXPERIENCE_NAMES: frozenset[str] = frozenset({
    "experience", "experiences", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background", "employment summary",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "personal profile",
    "about me", "about", "career summary", "executive summary",
    "general info", "general information",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "skill", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack", "relevant skills", "skills & abilities",
    "skills and abilities", "skill summary", "professional skills",
    "technical stack", "tools", "tools & technologies", "tools and technologies",
    "software skills",
    # Non-standard names used by some templates; kept out of text_parser._SKILLS_NAMES
    # so LLM output stays sem=other, allowing the updater guard (Pass 1) to skip the
    # title match and let the standard skills section ("TECHNICAL SKILLS") match via
    # semantic type in Pass 2.
    "my qualifications", "qualifications",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees", "educational history",
    "education summary",
})

_ALL_KNOWN: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES
)

_ALL_HEADING_NAMES: frozenset[str] = (
    _ALL_KNOWN | frozenset({
        "projects", "certifications", "certification", "publications",
        "awards", "honors", "languages", "references", "activities",
        "volunteer", "volunteering", "leadership", "interests",
        "additional information", "communication",
        "affiliations", "affiliations and awards", "affiliations & awards",
        "certifications and training", "training and certifications",
        "professional certifications",
        "portfolio", "qualifications", "key qualifications",
        "achievements", "accomplishments", "training",
        # Contact sections appear in sidebar/column layouts; must be recognised
        # as headings so they don't bleed into adjacent experience sections.
        "contact", "contact info", "contact information",
    })
)

# Space-stripped versions of the above sets for normalised heading matching.
# Handles: ALL-CAPS ("WORK EXPERIENCE"), letter-spaced ("S U M M A R Y"),
# and numbered-prefix ("1. Professional Summary") headings.
_ALL_HEADING_NAMES_NOSPACE: frozenset[str] = frozenset(
    n.replace(" ", "") for n in _ALL_HEADING_NAMES
)
_EXPERIENCE_NAMES_NOSPACE: frozenset[str] = frozenset(
    n.replace(" ", "") for n in _EXPERIENCE_NAMES
)
_SUMMARY_NAMES_NOSPACE: frozenset[str] = frozenset(
    n.replace(" ", "") for n in _SUMMARY_NAMES
)
_SKILLS_NAMES_NOSPACE: frozenset[str] = frozenset(
    n.replace(" ", "") for n in _SKILLS_NAMES
)
_EDUCATION_NAMES_NOSPACE: frozenset[str] = frozenset(
    n.replace(" ", "") for n in _EDUCATION_NAMES
)
# Compiled once — strips leading "1. " / "2) " list numbering.
_NUM_PREFIX_RE = re.compile(r"^\d+[\.\)]\s*")


def _normalize_heading_text(text: str) -> str:
    """Return a normalised, space-stripped lowercase heading key.

    Strips leading numbered-list prefixes ("1. ", "2) ") and removes all
    whitespace and non-breaking spaces so that letter-spaced headings
    ("S U M M A R Y"), numbered headings ("1. Professional Summary"), and
    ALL-CAPS headings ("WORK EXPERIENCE") all map to the same key as the
    canonical lowercase name ("summary", "professionalsummary",
    "workexperience") stored in *_NOSPACE sets.
    """
    t = _NUM_PREFIX_RE.sub("", text)
    return re.sub(r"[\s\xa0]+", "", t).lower()


def _classify_section(heading_text: str) -> str:
    t = _normalize_heading_text(heading_text)
    if t in _EXPERIENCE_NAMES_NOSPACE:
        return "experience"
    if t in _SUMMARY_NAMES_NOSPACE:
        return "summary"
    if t in _SKILLS_NAMES_NOSPACE:
        return "skills"
    if t in _EDUCATION_NAMES_NOSPACE:
        return "education"
    return "other"


def _group_roles(body_paras: list[ParaModel]) -> list[RoleEntry]:
    """Group experience body paragraphs into RoleEntry objects."""
    roles: list[RoleEntry] = []
    header: ParaModel | None = None
    header_extra: list[ParaModel] = []
    meta: list[ParaModel] = []
    bullets: list[ParaModel] = []
    # Paragraphs buffered for sibling-geometry check: indented but not yet
    # confirmed as bullets (require ≥2 siblings before promoting).
    pending: list[ParaModel] = []
    # Date lines seen before the role title in Pattern B (date-before-title)
    # format.  Cleared when adopted into meta for the new role.
    pre_header_meta: list[ParaModel] = []
    # True when the current role was established via Pattern B (a date/meta
    # line preceded the title).  Used to detect the next role boundary: in
    # Pattern B, a new role_meta in header/meta/bullets state is the date for
    # the NEXT role, not additional meta for the current one.
    used_pattern_b = False
    state = "init"

    # Only use indent-based bullet promotion when the section has at least one
    # paragraph that is already semantically a bullet (explicit marker in the
    # PDF).  Without explicit bullets, large indent differences are layout
    # artefacts (e.g. right column in a two-column PDF) and must not be
    # promoted — they are kept as meta lines and round-trip as plain paragraphs.
    has_explicit_bullets = any(p.semantic == "bullet" for p in body_paras)

    # Only apply separate-line-format heuristics (init-state paragraph as role
    # header; bullets-state indent-based new-role detection) when the section
    # has NO pipe-format role_header paragraphs.  Resumes that do use "Title |
    # Company" separators already have role_header semantics and must follow the
    # standard path — otherwise a plain paragraph before the first role_header
    # would be incorrectly treated as a standalone role entry.
    has_pipe_role_headers = any(p.semantic == "role_header" for p in body_paras)

    # When the section has date/meta lines, role detection works even without
    # explicit bullet markers — the date lines are sufficient role-boundary
    # signals (Pattern A: title→date, Pattern B: date→title).
    has_role_meta = any(p.semantic == "role_meta" for p in body_paras)

    def _hdr_indent() -> float:
        """Return the indent of the current role header (0 if unknown)."""
        pp = header.paragraph_profile if header else None
        return pp.indent_left_pt if pp else 0.0

    def _is_implicit_role_title(pm: ParaModel) -> bool:
        """True when pm looks like a role title that was mis-classified as paragraph.

        Targets bold ALL-CAPS short paragraphs in two-column layouts whose
        space_before=0 because they were non-first lines in a cross-column block,
        preventing _infer_semantic from detecting the Y-gap.
        """
        _pp = pm.paragraph_profile
        if _pp is None or not _pp.bold:
            return False
        if _pp.column_id not in ("left", "right"):
            return False
        _txt = pm.text.strip()
        _alpha = re.sub(r"[^a-zA-Z]", "", _txt)
        if not _alpha or _alpha != _alpha.upper():
            return False
        if len(_txt.split()) > 6 or len(_txt) > 50:
            return False
        return _pp.indent_left_pt <= _hdr_indent() + 6.0

    def _has_list_indent(pm: ParaModel) -> bool:
        """True when pm is indented ≥8 pt more than the role header.

        This detects list geometry (e.g. Leonid's \uf0b7 bullets at +18pt)
        while excluding paragraphs that merely start at the column body
        margin (+6 pt or less in two-column layouts like 1849228).
        """
        pp = pm.paragraph_profile
        indent = pp.indent_left_pt if pp else 0.0
        return (indent - _hdr_indent()) >= 8.0

    def _promote_pending() -> None:
        for pb in pending:
            pb.semantic = "bullet"
            bullets.append(pb)
        pending.clear()

    def _flush() -> None:
        nonlocal header, used_pattern_b
        if header is None:
            return
        # Unfulfilled pending paragraphs had only one sibling → not bullets.
        meta.extend(pending)
        pending.clear()
        # Normalise meta-line layout properties that don't translate to DOCX:
        # • Column-relative indents (90+ pt in two-column PDFs) create huge
        #   indentation in the single-column output.  Zeroing is skipped for
        #   two-column paragraphs (column_id is set) whose indents are
        #   column-relative and must be preserved for correct cell rendering.
        # • Large space_before values come from inter-bullet group gaps in the
        #   original PDF; they were capped at 3 pt for bullet paragraphs but
        #   must also be capped here to prevent page-count regressions.
        for _pm in meta:
            if _pm.paragraph_profile:
                _in_two_col = _pm.paragraph_profile.column_id in ("left", "right")
                if not _in_two_col and _pm.paragraph_profile.indent_left_pt > 4.0:
                    _pm.paragraph_profile.indent_left_pt = 0.0
                if _pm.paragraph_profile.space_before_pt > 3.0:
                    _pm.paragraph_profile.space_before_pt = 3.0

        # When no explicit bullet markers exist (has_explicit_bullets=False),
        # promote paragraph-semantic meta lines to bullets so the updater can
        # use them as cloning archetypes.  This preserves the original x-position
        # of role body content (e.g. 49 pt for template 16 experience entries)
        # rather than falling back to the role header indent (70 pt).
        # Restricted to two-column paragraphs (column_id set): single-column
        # resumes use PUA-glyph bullets that are merged to "bullet" semantic
        # AFTER _group_roles runs, so their paragraphs must stay in meta here.
        if not bullets:
            _promoted: list[ParaModel] = []
            _remaining_meta: list[ParaModel] = []
            for _pm in meta:
                if (
                    _pm.semantic == "paragraph"
                    and _pm.paragraph_profile is not None
                    and _pm.paragraph_profile.column_id in ("left", "right")
                ):
                    _pm.semantic = "bullet"
                    _promoted.append(_pm)
                else:
                    _remaining_meta.append(_pm)
            if _promoted:
                bullets.extend(_promoted)
                meta[:] = _remaining_meta

        roles.append(RoleEntry(
            header=header,
            header_extra=list(header_extra),
            meta_lines=list(meta),
            bullets=list(bullets),
            role_id=header.text.strip(),
        ))
        header = None
        header_extra.clear()
        meta.clear()
        bullets.clear()
        used_pattern_b = False

    def _peek(start: int, max_dist: int = 2) -> str | None:
        """Return the semantic of the next non-empty paragraph within max_dist steps."""
        for i in range(start, min(start + max_dist, len(body_paras))):
            if body_paras[i].semantic != "empty":
                return body_paras[i].semantic
        return None

    def _start_pattern_b(date_pm: ParaModel) -> None:
        """Flush the current role and buffer date_pm for the next Pattern B role."""
        nonlocal state
        _flush()
        pre_header_meta.append(date_pm)
        state = "init"

    for idx, pm in enumerate(body_paras):
        s = pm.semantic
        if s == "role_header":
            _flush()
            header = pm
            # Absorb any buffered Pattern B dates into this role's meta.
            if pre_header_meta:
                meta.extend(pre_header_meta)
                pre_header_meta.clear()
            state = "header"
        elif state == "init":
            # Allow Pattern B (date already buffered → paragraph is the title)
            # even when pipe-format role_headers exist elsewhere in this section.
            # Sections can mix formats: the first role may lack a pipe separator
            # while later roles use "Title | Company" (e.g. sample 27).
            _allow_with_pipe = bool(pre_header_meta)
            if s == "paragraph" and (not has_pipe_role_headers or _allow_with_pipe):
                _txt_init = pm.text.strip()
                _can_promote = (
                    # When pipe-format roles exist, only promote via an explicit
                    # buffered date (Pattern B) — suppress heuristics that could
                    # mis-classify preamble text as a role title.
                    (not has_pipe_role_headers and has_explicit_bullets)
                    # Pattern B: a date line was already buffered — this paragraph
                    # is the role title that follows the date.
                    or bool(pre_header_meta)
                    # Pattern A: paragraph immediately followed by a date line AND
                    # starts with a capital letter (job titles start with capitals;
                    # preamble/description fragments start with lowercase).
                    or (
                        not has_pipe_role_headers
                        and has_role_meta
                        and _peek(idx + 1) == "role_meta"
                        and bool(_txt_init) and _txt_init[0].isupper()
                    )
                )
                # Guard: if the very next paragraph is already a role_header,
                # this paragraph is likely the company / affiliation name that
                # precedes the role title (e.g. "Ginyard International Co."
                # before "RESPONSIBLE FOR NETWORK AND SOFTWARE").  Treating it as
                # a role title would create a spurious empty role.  Buffer it in
                # pre_header_meta instead so it becomes meta for the real role.
                _next_is_role_header = _peek(idx + 1) == "role_header"
                if _can_promote and _next_is_role_header:
                    _can_promote = False

                if _can_promote:
                    # Separate-line format: this paragraph is the role title.
                    header = pm
                    if pre_header_meta:
                        # Pattern B: adopt buffered date(s) into this role's meta.
                        meta.extend(pre_header_meta)
                        pre_header_meta.clear()
                        used_pattern_b = True
                    state = "header"
                else:
                    # Not promotable — buffer as pre-role meta so it is not lost.
                    pre_header_meta.append(pm)
            elif s == "role_meta":
                # Pattern B: date appears before the title — buffer it.
                # Buffer even when pipe-format role headers exist elsewhere in
                # this section (mixed-format sections need date signal for the
                # non-pipe roles that precede the pipe-format ones).
                pre_header_meta.append(pm)
        elif state == "header":
            if s == "role_meta":
                if used_pattern_b:
                    # Pattern B: current role already has its date (from
                    # pre_header_meta); this date belongs to the NEXT role.
                    _start_pattern_b(pm)
                else:
                    meta.append(pm)
                    state = "meta"
            elif s == "bullet":
                _promote_pending()
                bullets.append(pm)
                state = "bullets"
            elif s == "paragraph":
                # Short line with no year and no sentence-end AND aligned with
                # the role header → continuation of the role header (e.g. a
                # wrapped company name).  A paragraph at a substantially different
                # indent from the header is role content (bullets/body), not a
                # wrapped header fragment, even if it is short.
                _txt = pm.text.strip()
                _para_ind = pm.paragraph_profile.indent_left_pt if pm.paragraph_profile else 0.0
                _in_two_col_para = (
                    pm.paragraph_profile is not None
                    and pm.paragraph_profile.column_id in ("left", "right")
                )
                _is_continuation = (
                    len(_txt) <= 60
                    and not _YEAR_RE.search(_txt)
                    and not _SENTENCE_END_RE.search(_txt)
                    # In two-column layouts, only treat as continuation when
                    # the paragraph is at the same indent as the role header
                    # (≤ 5 pt difference).  A company name at a different
                    # x-position (e.g. 49 pt vs 70 pt for the header) is
                    # role body content, not a wrapped header fragment.
                    and (not _in_two_col_para or abs(_para_ind - _hdr_indent()) <= 5.0)
                )
                if _is_continuation:
                    pm.semantic = "role_meta"
                    header_extra.append(pm)
                elif _has_list_indent(pm) and has_explicit_bullets:
                    pending.append(pm)
                    state = "meta"
                else:
                    meta.append(pm)
                    state = "meta"
            elif s == "empty":
                pass
            else:
                _promote_pending()
                bullets.append(pm)
                state = "bullets"
        elif state == "meta":
            if s == "role_meta":
                if used_pattern_b:
                    # Pattern B continuation: this date starts the next role.
                    _start_pattern_b(pm)
                elif re.match(
                    r"^\(\d{4}[-–—](?:\d{4}|now|present|current|today)\)$",
                    pm.text.strip(), re.IGNORECASE
                ) or re.match(r"^\d{4}[-–—](?:\d{4}|now|present|current|today)$",
                    pm.text.strip(), re.IGNORECASE):
                    # Pure year-range date (e.g. "(2014-Now)", "2010-2013") appearing
                    # after meta content: this is the next role's date, not a continuation.
                    _start_pattern_b(pm)
                else:
                    meta.append(pm)
            elif s == "bullet":
                # Explicit marker: confirm any buffered pending as bullets.
                _promote_pending()
                bullets.append(pm)
                state = "bullets"
            elif s == "paragraph":
                if _has_list_indent(pm) and has_explicit_bullets:
                    pending.append(pm)
                    if len(pending) >= 2:
                        # Two or more siblings with list geometry → promote all.
                        _promote_pending()
                        state = "bullets"
                elif (
                    not has_pipe_role_headers
                    and has_role_meta
                    and not used_pattern_b
                    and _peek(idx + 1) == "role_meta"
                    and pm.text.strip()[:1].isupper()
                ):
                    # Pattern A new-role: this paragraph starts with a capital
                    # and is immediately followed by a date line — treat it as
                    # the next role's title rather than content for the current role.
                    _flush()
                    header = pm
                    state = "header"
                elif _is_implicit_role_title(pm) and (meta or bullets):
                    # Bold ALL-CAPS short paragraph at header indent inside a
                    # two-column experience section — likely a role title whose
                    # space_before was 0 because it was a non-first line in a
                    # cross-column text block (so _infer_semantic couldn't detect
                    # the Y-gap and classify it as section_heading).
                    _flush()
                    pm.semantic = "role_header"
                    header = pm
                    state = "header"
                else:
                    # Non-indented paragraph → flush pending to meta, keep as meta.
                    meta.extend(pending)
                    pending.clear()
                    meta.append(pm)
            elif s == "empty":
                pass
            else:
                _promote_pending()
                bullets.append(pm)
                state = "bullets"
        elif state == "bullets":
            if s == "bullet":
                bullets.append(pm)
            elif s == "role_meta":
                if used_pattern_b:
                    # Pattern B continuation from bullets state.
                    _start_pattern_b(pm)
                elif re.match(
                    r"^\(\d{4}[-–—](?:\d{4}|now|present|current|today)\)$",
                    pm.text.strip(), re.IGNORECASE
                ) or re.match(r"^\d{4}[-–—](?:\d{4}|now|present|current|today)$",
                    pm.text.strip(), re.IGNORECASE):
                    # Pure year-range date (e.g. "(2014-Now)", "2010-2013") appearing
                    # after bullets: this is the next role's date.  Start a new role.
                    _start_pattern_b(pm)
                else:
                    # role_meta can appear mid-bullet-list when a line contains
                    # a year (e.g. "Resolved 150 bugs since June 2023 …") but is
                    # clearly a continuation bullet, not a date/meta line.
                    # Reclassify so parser_semantic reflects the actual role in
                    # the output (prevents EXPERIENCE_ROLE_BOUNDARY_INSIDE_BULLETS).
                    pm.semantic = "bullet"
                    bullets.append(pm)
            elif s == "paragraph":
                _txt = pm.text.strip()
                if _txt and _txt[0].islower():
                    # Lowercase start → continuation fragment of the previous bullet.
                    pm.semantic = "bullet"
                    bullets.append(pm)
                elif (
                    not has_pipe_role_headers
                    and has_explicit_bullets
                    and bullets
                    and pm.paragraph_profile is not None
                    and pm.paragraph_profile.indent_left_pt <= _hdr_indent() + 4.0
                    and pm.paragraph_profile.indent_left_pt < (
                        bullets[0].paragraph_profile.indent_left_pt
                        if bullets[0].paragraph_profile else 0.0
                    ) - 4.0
                ):
                    # Paragraph back at header-level indent (significantly less
                    # indented than current bullets) → new role in separate-line
                    # format.  Flush current role and start fresh.
                    _flush()
                    header = pm
                    state = "header"
                elif (
                    not has_pipe_role_headers
                    and has_role_meta
                    and not used_pattern_b
                    and _peek(idx + 1) == "role_meta"
                    and pm.text.strip()[:1].isupper()
                ):
                    # Pattern A new-role from bullets state (title→date format).
                    _flush()
                    header = pm
                    state = "header"
                elif _is_implicit_role_title(pm):
                    # Bold ALL-CAPS short paragraph at header indent in a two-column
                    # layout — likely a role title mis-classified as paragraph due
                    # to space_before=0 from cross-column block line position.
                    _flush()
                    pm.semantic = "role_header"
                    header = pm
                    state = "header"
                elif len(bullets) >= 2:
                    # Established list (≥2 bullets): promote as continuation.
                    pm.semantic = "bullet"
                    bullets.append(pm)
                # else: single bullet + capitalised paragraph → not promoted.
        else:
            pass

    _flush()

    # Date-only body: the section body has only role_meta lines and no headers
    # were ever detected (e.g. a "WORK HISTORY" section where dates are placed
    # in the same column as the section label but the job titles were absorbed
    # into a different text block).  Synthesise a minimal stub role per date
    # line so the section is not reported as EXPERIENCE_NO_ROLES.
    if not roles and pre_header_meta:
        non_empty = [p for p in body_paras if p.semantic != "empty"]
        if non_empty and all(p.semantic == "role_meta" for p in non_empty):
            for pm in pre_header_meta:
                roles.append(
                    RoleEntry(
                        header=pm,
                        header_extra=[],
                        meta_lines=[],
                        bullets=[],
                        role_id=pm.text.strip(),
                    )
                )

    return roles


def _merge_split_section_headings(paras: list[ParaModel]) -> list[ParaModel]:
    """Merge consecutive short ALL-CAPS paragraph pairs that form a known heading.

    Handles templates where a section heading like "WORK HISTORY" is split
    across two PDF text blocks: ["WORK", "HISTORY"].  Each word alone is not
    recognised as a section heading, but together they are.

    Only paragraph-typed (non-heading, non-bullet) items are candidates.
    The check is safe because the combined text must appear verbatim in
    _ALL_HEADING_NAMES_NOSPACE, making accidental merges very unlikely.
    """
    result: list[ParaModel] = []
    _alpha_upper = re.compile(r"^[A-Z\s\xa0]+$")
    for pm in paras:
        _txt = pm.text.strip()
        if (
            pm.semantic == "paragraph"
            and _txt
            and len(_txt) < 20
            and _alpha_upper.match(_txt)
            and result
            and result[-1].semantic == "paragraph"
        ):
            _prev_txt = result[-1].text.strip()
            if (
                _prev_txt
                and len(_prev_txt) < 20
                and _alpha_upper.match(_prev_txt)
                and _normalize_heading_text(_prev_txt + _txt) in _ALL_HEADING_NAMES_NOSPACE
            ):
                _prev = result.pop()
                _merged = _prev.with_text(_prev_txt + " " + _txt)
                _merged.semantic = "section_heading"
                result.append(_merged)
                continue
        result.append(pm)
    return result


def _rescue_cross_col_paras(
    left_secs: "list[ResumeSection]",
    right_secs: "list[ResumeSection]",
) -> None:
    """Fix region ownership when PDFs store blocks in column-cell order.

    Some PDFs (e.g. templates with internal Education-Summary sub-tables)
    store their right sub-column blocks *after* the sidebar section heading
    in the document's byte-order.  This causes `_group_sections` to assign
    those paragraphs to the wrong section (e.g. Education bullets land in
    Contact Info body).

    Detection: a body paragraph whose y_top_pt is ≥ 30 pt *above* its
    section heading's y_top_pt was placed there by column-cell ordering, not
    by semantic proximity.  We re-attribute it to the left-column section
    whose y-range best contains the paragraph.

    Operates in-place; removed paragraphs' column_id is set to "left".
    """
    if not left_secs or not right_secs:
        return

    # Build y-extent for each left section (heading y + body para y-values).
    left_extents: list[tuple[float, float, "ResumeSection"]] = []
    for ls in left_secs:
        ys: list[float] = []
        if ls.heading.paragraph_profile and ls.heading.paragraph_profile.y_top_pt:
            ys.append(ls.heading.paragraph_profile.y_top_pt)
        for pm in ls.body_paras:
            if pm.paragraph_profile and pm.paragraph_profile.y_top_pt:
                ys.append(pm.paragraph_profile.y_top_pt)
        if ys:
            left_extents.append((min(ys), max(ys), ls))

    if not left_extents:
        return

    for rs in right_secs:
        heading_y = (
            rs.heading.paragraph_profile.y_top_pt
            if rs.heading.paragraph_profile else 0.0
        )
        normal_body: list["ParaModel"] = []
        for pm in rs.body_paras:
            para_y = pm.paragraph_profile.y_top_pt if pm.paragraph_profile else 0.0
            if para_y > 0 and heading_y > 0 and para_y < heading_y - 30.0:
                # Paragraph is significantly above its section heading →
                # mis-assigned due to column-cell ordering.  Find the best
                # left-column owner by y-range containment.
                best: "ResumeSection | None" = None
                for y_min, y_max, ls in left_extents:
                    # Allow 40 pt tolerance on each side.
                    if y_min - 40.0 <= para_y <= y_max + 40.0:
                        best = ls
                        break
                if best is not None:
                    if pm.paragraph_profile is not None:
                        pm.paragraph_profile.column_id = "left"
                    best.body_paras.append(pm)
                    continue  # do not keep in right section
            normal_body.append(pm)
        rs.body_paras = normal_body


def _redistribute_parallel_body(
    right_paras: list[ParaModel],
    left_secs: list[ResumeSection],
) -> None:
    """Assign headingless right-column paragraphs to left-column sections by y-position.

    For parallel two-column layouts where section headings only appear in the
    left column, _group_sections returns all right-column content in right_hdrs
    (no right sections found). This function assigns each right-column paragraph
    to the left section whose heading y is closest and at or below the
    paragraph's y. Paragraphs at y=0 (geometry not captured) go to the first
    section. Operates in-place.
    """
    if not right_paras or not left_secs:
        return

    # Build (heading_y, section) pairs sorted ascending by heading y.
    anchors: list[tuple[float, ResumeSection]] = []
    for sec in left_secs:
        hy = 0.0
        if sec.heading.paragraph_profile:
            hy = sec.heading.paragraph_profile.y_top_pt or 0.0
        anchors.append((hy, sec))
    anchors.sort(key=lambda t: t[0])

    for pm in right_paras:
        para_y = 0.0
        if pm.paragraph_profile:
            para_y = pm.paragraph_profile.y_top_pt or 0.0

        # Walk anchors in ascending y order; take the last one whose heading
        # y ≤ para_y + 20 pt (tolerance for slight y-misalignment between
        # heading text and the first right-column line at the same band).
        target_sec = anchors[0][1]
        for hy, sec in anchors:
            if hy <= para_y + 20.0:
                target_sec = sec
        target_sec.body_paras.append(pm)


def _group_sections(
    paras: list[ParaModel],
) -> tuple[list[ParaModel], list[ResumeSection]]:
    """Split flat paragraph list into header_paras + sections."""
    paras = _merge_split_section_headings(paras)
    header_paras: list[ParaModel] = []
    sections: list[ResumeSection] = []
    current: ResumeSection | None = None
    found_section = False
    # Through-empty-section absorb: stash an empty experience section so that
    # a non-known heading encountered one or more known sections later can
    # still be absorbed into it as a role_header.  This handles two-column PDF
    # layouts where the experience section label and the job-title headings
    # land in different text-extraction bands, with other known section labels
    # (Education, Contact Info) appearing in between.
    _last_empty_exp: ResumeSection | None = None
    _last_empty_exp_insert_idx: int | None = None
    # True when `current` has already been inserted into `sections` (via
    # through-empty absorb) and must not be appended again.
    _current_in_sections = False

    for pm in paras:
        if pm.semantic == "section_heading":
            # Before the first known section, only accept known headings to
            # avoid treating names / job titles as sections.  Use normalised
            # comparison so ALL-CAPS, letter-spaced, and numbered headings are
            # matched the same way as in _infer_semantic.
            if not found_section and _normalize_heading_text(pm.text) not in _ALL_HEADING_NAMES_NOSPACE:
                # Try combining with the last header_para if it is also a
                # section_heading — handles split headings like "WORK" + "HISTORY"
                # → "WORK HISTORY" that are each unrecognised individually.
                if (
                    header_paras
                    and header_paras[-1].semantic == "section_heading"
                    and _normalize_heading_text(header_paras[-1].text + pm.text)
                    in _ALL_HEADING_NAMES_NOSPACE
                ):
                    _prev = header_paras.pop()
                    pm = _prev.with_text(_prev.text.strip() + " " + pm.text.strip())
                    pm.semantic = "section_heading"
                    # Fall through to section processing below.
                else:
                    header_paras.append(pm)
                    continue

            # Two-column / table PDF layout: the experience section label
            # ("WORK EXPERIENCE") appears alone as a heading with no body
            # paragraphs; the actual job-title entries follow as bold-heading
            # paragraphs in the adjacent column.  Absorb the first such
            # non-known heading as a role_header so _group_roles can detect it.
            _htext = _normalize_heading_text(pm.text)
            if (
                current is not None
                and current.semantic_type == "experience"
                and len(current.body_paras) == 0
                and _htext not in _ALL_HEADING_NAMES_NOSPACE
            ):
                pm.semantic = "role_header"
                current.body_paras.append(pm)
                continue

            # Through-empty absorb: a non-known heading appeared after one or
            # more known sections that separated it from the stashed empty
            # experience section.  Finalize the intervening "current" section,
            # restore the stash as current, and absorb this heading into it.
            if _last_empty_exp is not None and _htext not in _ALL_HEADING_NAMES_NOSPACE:
                if current is not None:
                    _finalise(current)
                    if not _current_in_sections:
                        sections.append(current)
                pm.semantic = "role_header"
                _last_empty_exp.body_paras.append(pm)
                sections.insert(_last_empty_exp_insert_idx, _last_empty_exp)
                current = _last_empty_exp
                _current_in_sections = True
                _last_empty_exp = None
                _last_empty_exp_insert_idx = None
                continue

            # Extended absorb: for active experience or education sections,
            # non-known section_headings are role titles / institution names —
            # absorb them as role_headers rather than creating new sections.
            # Consecutive role_headers (multi-line title like "RESPONSIBLE FOR
            # NETWORK / AND SOFTWARE") are merged into the previous one.
            if (
                current is not None
                and current.semantic_type in ("experience", "education")
                and _htext not in _ALL_HEADING_NAMES_NOSPACE
            ):
                if (
                    current.body_paras
                    and current.body_paras[-1].semantic == "role_header"
                ):
                    # Continuation line of a wrapped role/institution title — merge
                    last_rh = current.body_paras[-1]
                    current.body_paras[-1] = last_rh.with_text(
                        last_rh.text + " " + pm.text.strip()
                    )
                else:
                    pm.semantic = "role_header"
                    current.body_paras.append(pm)
                continue

            found_section = True
            if current is not None:
                if (
                    current.semantic_type == "experience"
                    and len(current.body_paras) == 0
                ):
                    # Stash the empty experience section; do not finalize yet.
                    # If a previous stash exists, finalize it now (it will not
                    # get any role content).
                    if _last_empty_exp is not None:
                        _finalise(_last_empty_exp)
                        sections.insert(_last_empty_exp_insert_idx, _last_empty_exp)
                    _last_empty_exp = current
                    _last_empty_exp_insert_idx = len(sections)
                else:
                    _finalise(current)
                    if not _current_in_sections:
                        sections.append(current)
            _current_in_sections = False
            current = ResumeSection(
                title=pm.text.strip(),
                heading=pm,
                semantic_type=_classify_section(pm.text),
            )
        elif not found_section:
            header_paras.append(pm)
        elif current is not None:
            current.body_paras.append(pm)

    # Finalize any stash that was never absorbed.
    if _last_empty_exp is not None:
        _finalise(_last_empty_exp)
        sections.insert(_last_empty_exp_insert_idx, _last_empty_exp)

    if current is not None:
        _finalise(current)
        if not _current_in_sections:
            sections.append(current)

    return header_paras, sections


_SENTENCE_END_RE = re.compile(r"[.?!:]\s*$")


def _merge_bullet_continuations(bullets: list[ParaModel]) -> list[ParaModel]:
    """Join PDF line-wrapped bullet fragments into single bullet ParaModels.

    PyMuPDF emits one ParaModel per visual text line.  A long bullet that
    wraps to the next line produces two separate paragraphs.  The second
    line (continuation) can be identified by:
      - the previous line does NOT end with sentence-ending punctuation
      - the current line starts with a lowercase letter

    When a continuation is detected the texts are joined with a space and
    the first ParaModel's text is updated (the continuation is dropped).
    """
    if not bullets:
        return bullets
    result: list[ParaModel] = [bullets[0]]
    for pm in bullets[1:]:
        text = pm.text.strip()
        if (not _SENTENCE_END_RE.search(result[-1].text.rstrip())
                and text and text[0].islower()):
            # Merge into previous bullet
            merged_text = result[-1].text.rstrip() + " " + text
            result[-1] = result[-1].with_text(merged_text)
        else:
            result.append(pm)
    return result


def _merge_pua_bullet_pairs(paras: list[ParaModel]) -> list[ParaModel]:
    """Merge consecutive [PUA-only 'empty'] + ['paragraph'] pairs into bullets.

    Some PDF generators (e.g. Symbol/Wingdings font bullets) emit the bullet
    glyph (e.g. '\\uf0b7') as a standalone text element on its own line at the
    bullet x-position, followed by the bullet text on the next line.  After
    _infer_semantic, the glyph line becomes 'empty' and the text line becomes
    'paragraph'.  This function fuses such pairs into a single 'bullet'
    ParaModel, transferring the glyph line's indent_left_pt so the output
    bullet aligns with the source PDF.
    """
    result: list[ParaModel] = []
    i = 0
    while i < len(paras):
        pm = paras[i]
        if (
            pm.semantic == "empty"
            and pm.text.strip()
            and all(0xE000 <= ord(c) <= 0xF8FF or c.isspace() for c in pm.text)
            and i + 1 < len(paras)
            and paras[i + 1].semantic == "paragraph"
        ):
            next_pm = paras[i + 1]
            next_pm.semantic = "bullet"
            if next_pm.paragraph_profile is not None and pm.paragraph_profile is not None:
                # Compute hanging-indent geometry from per-line x0 values so
                # the output DOCX uses w:left (body text anchor) + w:hanging
                # (marker outdent) rather than w:left = marker position.
                # Example (Resume-Sample-1): marker at x=54, text at x=72,
                # col_origin=36  →  w:left=36pt, w:hanging=18pt.
                pua_x0 = pm.paragraph_profile.body_text_x0_pt      # abs marker x
                text_x0 = next_pm.paragraph_profile.body_text_x0_pt  # abs text x
                pua_indent = pm.paragraph_profile.indent_left_pt    # marker rel to col
                # col_origin = pua_x0 - pua_indent
                new_indent = max(0.0, text_x0 - (pua_x0 - pua_indent))
                hanging = max(0.0, text_x0 - pua_x0)
                next_pm.paragraph_profile.indent_left_pt = new_indent
                next_pm.paragraph_profile.hanging_indent_pt = hanging
            result.append(next_pm)
            i += 2
        else:
            result.append(pm)
            i += 1
    return result


def _finalise(section: ResumeSection) -> None:
    if section.semantic_type == "experience":
        # Run role grouping BEFORE PUA bullet merging so that _group_roles sees
        # the original semantic flags (no merged bullets → has_explicit_bullets
        # stays False for PUA-only bullet sections, preventing spurious role
        # splits on plain paragraph lines like company names).
        section.roles = _group_roles(section.body_paras)
        for role in section.roles:
            role.bullets = _merge_pua_bullet_pairs(role.bullets)
            role.bullets = _merge_bullet_continuations(role.bullets)
    # Merge PUA-only+paragraph pairs in body_paras after role grouping.
    section.body_paras = _merge_pua_bullet_pairs(section.body_paras)


# ---------------------------------------------------------------------------
# Section-label column helpers (for "row-per-section" table layouts)
# ---------------------------------------------------------------------------

_FOOTER_ITEM_RE = re.compile(r"[@]|^\+?\d[\d\s\-().]{4,}|https?://|www\.", re.IGNORECASE)


def _merge_left_col_wraps(left_raw: "list[ParaModel]") -> "list[ParaModel]":
    """Merge consecutive word-wrapped section label lines in the left column.

    Some templates split two-word section labels across two lines, e.g.
    'GENERAL' / 'INFO', 'EDUCATION' / 'SUMMARY', 'WORK' / 'HISTORY'.
    This function merges such pairs into a single 'GENERAL INFO' paragraph so
    that _is_section_label_column can recognise 'WORK HISTORY' as a known
    experience heading and _interleave_section_label_column creates one section
    per label rather than two.

    Merge criteria:
    - Both lines are bold AND ALL-CAPS AND ≤ 3 words.
    - Y gap between them is ≤ 30 pt (word-wrap spacing, not a new section).
    """
    _MAX_WRAP_GAP = 30.0
    result: "list[ParaModel]" = []
    i = 0
    while i < len(left_raw):
        pm = left_raw[i]
        pp = pm.paragraph_profile
        t = pm.text.strip()
        is_label = (
            pp is not None
            and pp.bold
            and t
            and t.upper() == t        # ALL-CAPS
            and len(t.split()) <= 3
        )
        if is_label and i + 1 < len(left_raw):
            nxt = left_raw[i + 1]
            npp = nxt.paragraph_profile
            nt = nxt.text.strip()
            y_gap = (npp.y_top_pt if npp else 9999) - (pp.y_top_pt if pp else 0.0)
            next_is_label = (
                npp is not None
                and npp.bold
                and nt
                and nt.upper() == nt
                and len(nt.split()) <= 3
            )
            if next_is_label and 0 < y_gap <= _MAX_WRAP_GAP:
                merged_pm = pm.with_text(t + " " + nt)
                if merged_pm.paragraph_profile:
                    merged_pm.paragraph_profile.y_top_pt = pp.y_top_pt
                result.append(merged_pm)
                i += 2
                continue
        result.append(pm)
        i += 1
    return result


def _is_section_label_column(left_raw: "list[ParaModel]") -> bool:
    """Return True when the left column contains only known section label text.

    Detects a 'section-row table' layout where the left column holds only
    section headings and the right column holds all body content.

    Two acceptance paths:
    1. Known-name match: ≥ 2 items match _ALL_HEADING_NAMES_NOSPACE AND
       match rate ≥ 60 % (after filtering contact-info items).
    2. Structural match: after filtering contact info, ≥ 2 items are ALL-CAPS
       + bold + ≤ 4 words — consistent with a template using non-standard
       section label names (e.g. 'GENERAL INFO', 'EDUCATION SUMMARY').
    """
    if len(left_raw) < 2:
        return False
    if any(pm.semantic == "bullet" for pm in left_raw):
        return False
    # Strip obvious contact/footer items (phone, email, URL) before matching.
    section_items = [pm for pm in left_raw if not _FOOTER_ITEM_RE.search(pm.text)]
    if len(section_items) < 2:
        return False
    # Known-name path
    matches = sum(
        1 for pm in section_items
        if _normalize_heading_text(pm.text) in _ALL_HEADING_NAMES_NOSPACE
    )
    if matches >= 2 and matches >= len(section_items) * 0.6:
        return True
    # Structural fallback: all-caps + bold + short (non-standard label names)
    structural = sum(
        1 for pm in section_items
        if (pm.paragraph_profile and pm.paragraph_profile.bold
            and pm.text.strip()
            and pm.text.strip().upper() == pm.text.strip()
            and len(pm.text.split()) <= 4)
    )
    return structural >= 2 and structural >= len(section_items) * 0.6


def _interleave_section_label_column(
    left_raw: "list[ParaModel]",
    right_raw: "list[ParaModel]",
) -> "list[ParaModel]":
    """Merge a section-label left column with right-column body content.

    Forces section_heading semantic on each left label, then inserts it just
    before the right-column paras that fall within its Y-range.  A 20 pt
    tolerance handles table-cell top-padding where right content starts
    slightly above the left label.

    Any right paras that fall outside all section Y-ranges (should not occur
    in practice) are appended to the last section to avoid data loss.
    """
    _TOLERANCE = 20  # pt: right content may start slightly above the label

    for pm in left_raw:
        pm.semantic = "section_heading"

    # In a section-label table the right column contains NO real section
    # headings — all section labels live in the left column.  Any right-column
    # para that _infer_semantic classified as section_heading (e.g. a bold
    # role title like "BACK-END DEVELOPER") is actually a role_header.
    # Reclassifying them before interleaving prevents _group_sections from
    # breaking the experience section into spurious extra sections.
    for pm in right_raw:
        if (
            pm.semantic == "section_heading"
            and _normalize_heading_text(pm.text) not in _ALL_HEADING_NAMES_NOSPACE
        ):
            pm.semantic = "role_header"

    def _y(pm: "ParaModel") -> float:
        pp = pm.paragraph_profile
        return pp.y_top_pt if pp is not None else 0.0

    label_ys = [_y(lbl) for lbl in left_raw]
    result: list[ParaModel] = []
    assigned_ids: set[int] = set()

    for i, label in enumerate(left_raw):
        y_start = label_ys[i] - _TOLERANCE
        y_end = (label_ys[i + 1] - _TOLERANCE) if i + 1 < len(left_raw) else float("inf")
        section_right = [pm for pm in right_raw if y_start <= _y(pm) < y_end]
        result.append(label)
        result.extend(section_right)
        assigned_ids.update(id(pm) for pm in section_right)

    # Append any right paras that fell outside all Y-ranges (stragglers)
    stragglers = [pm for pm in right_raw if id(pm) not in assigned_ids]
    result.extend(stragglers)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdf(pdf_bytes: bytes) -> ResumeDocument:
    """Parse a PDF file into a ResumeDocument IR.

    Parameters
    ----------
    pdf_bytes:
        Raw PDF file contents.

    Returns
    -------
    ResumeDocument
        With source_kind='pdf'.  All ParaModel.paragraph_profile fields are
        populated; xml_proto is always None.

    Raises
    ------
    RuntimeError
        If the PDF appears to be scanned (no selectable text).
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required for PDF parsing. "
            "Install it with: pip install pymupdf"
        ) from exc

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Strip the PDF structure/accessibility tree from the in-memory document.
    # LibreOffice sometimes emits a malformed StructTreeRoot ("No common ancestor
    # in structure tree").  PyMuPDF logs this to stderr on every get_pixmap() call,
    # flooding output with noise.  The structure tree is not used for text
    # extraction or rendering; removing it is harmless.
    try:
        _cat = doc.pdf_catalog()
        if _cat and doc.xref_get_key(_cat, "StructTreeRoot")[0] != "null":
            doc.xref_set_key(_cat, "StructTreeRoot", "null")
            doc.xref_set_key(_cat, "MarkInfo", "null")
    except Exception:
        pass

    # Scanned document detection
    total_chars = sum(len(page.get_text()) for page in doc)
    if total_chars < _SCANNED_CHAR_THRESHOLD:
        raise RuntimeError(
            "This PDF cannot be parsed (likely scanned). "
            "Please use a non-scanned PDF or upload a .docx file."
        )

    layout = _extract_layout(doc)
    skip_pages = _deduplicate_page_indices(doc)
    hf_texts = _detect_header_footer_texts(doc, skip_pages=skip_pages)
    raw_paras = _extract_paragraphs(doc, hf_texts, layout.margin_left_pt, layout, skip_pages)

    # For raster-background two-column PDFs (where get_drawings() returns nothing and
    # section_bg_rects stays empty), section headings may sit on colored raster bands
    # that can only be detected via pixel sampling at the exact paragraph y-position.
    # Apply this fallback only for left-column paras with no background_color detected.
    if layout.column_split_x is not None and layout.left_col_bg_color:
        try:
            import fitz as _fitz
            _page0 = doc[0]
            _mat_1 = _fitz.Matrix(1, 1)
            _sample_x = layout.column_split_x * 0.5
            _c_lum = sum(
                int(layout.left_col_bg_color[i * 2: i * 2 + 2], 16) for i in range(3)
            ) / 3
            for _pm in raw_paras:
                _pp = _pm.paragraph_profile
                if (
                    _pp is None
                    or _pp.column_id != "left"
                    or _pp.background_color is not None
                    or _pp.y_top_pt is None
                ):
                    continue
                _y = _pp.y_top_pt
                _fs = _pp.font_size_pt or 12.0
                _clip = _fitz.Rect(_sample_x - 1, _y, _sample_x + 1, _y + _fs * 0.6)
                _pix = _page0.get_pixmap(matrix=_mat_1, clip=_clip, alpha=False)
                if _pix.samples and _pix.n >= 3:
                    _r, _g, _b = _pix.samples[0], _pix.samples[1], _pix.samples[2]
                    _lum = (_r + _g + _b) / 3
                    if abs(_lum - _c_lum) > 40 and _lum < 245:
                        _pp.background_color = f"{_r:02x}{_g:02x}{_b:02x}"
        except Exception:
            pass

    # Two-column documents: run section grouping independently for each column
    # so that left-column section headings (e.g. "CONTACT") do not trigger
    # found_section=True and absorb right-column content into the wrong section.
    # Single-column documents use the normal flat grouping path.
    if layout.column_split_x is not None:
        def _col_id(pm: "ParaModel") -> "str | None":
            return pm.paragraph_profile.column_id if pm.paragraph_profile else None

        above_raw = [pm for pm in raw_paras if _col_id(pm) not in ("left", "right")]
        left_raw  = [pm for pm in raw_paras if _col_id(pm) == "left"]
        right_raw = [pm for pm in raw_paras if _col_id(pm) == "right"]

        # Merge word-wrapped section labels before detection.  Some templates
        # split two-word labels across two lines (e.g. 'GENERAL'/'INFO',
        # 'WORK'/'HISTORY') so each word appears as a separate paragraph.
        merged_left_raw = _merge_left_col_wraps(left_raw)
        if _is_section_label_column(merged_left_raw):
            # Section-row table layout: left column holds only section labels,
            # right column holds all body content.  Merge them into a flat list
            # (section label followed by its right-column content) and run a
            # single _group_sections pass so all existing logic (_group_roles,
            # bullet merging, etc.) works correctly.
            # Filter contact/footer items (phone, email) from the label list;
            # they are not section headings and would create spurious sections.
            layout.section_row_table = True
            layout.table_layout_mode = "synchronized_rows"
            label_left = [pm for pm in merged_left_raw if not _FOOTER_ITEM_RE.search(pm.text)]
            merged = _interleave_section_label_column(label_left, right_raw)
            above_hdrs, above_secs = _group_sections(above_raw)
            main_hdrs,  main_secs  = _group_sections(merged)
            header_paras = above_hdrs + main_hdrs
            sections     = above_secs + main_secs
        else:
            above_hdrs, above_secs = _group_sections(above_raw)
            left_hdrs,  left_secs  = _group_sections(left_raw)
            right_hdrs, right_secs = _group_sections(right_raw)
            # Cross-column ownership rescue: PDFs that store blocks in
            # column-cell order (not y-order) may place the right sub-column
            # of an Education Summary table *after* the Contact Info heading
            # in the block sequence.  Detect body paragraphs whose y_top_pt is
            # significantly above their section heading's y and re-associate
            # them with the contextual left-column section.
            _rescue_cross_col_paras(left_secs, right_secs)
            # Parallel-body redistribution: when the right column has no
            # section headings (parallel-body layout where labels appear only
            # on the left), redistribute right-column paragraphs into the left
            # sections by y-range so they render as body content, not headers.
            if not right_secs and right_hdrs and left_secs:
                _redistribute_parallel_body(right_hdrs, left_secs)
                right_hdrs = []
            header_paras = above_hdrs + left_hdrs + right_hdrs
            sections     = above_secs + left_secs + right_secs
    else:
        header_paras, sections = _group_sections(raw_paras)

    # Detect full-width dark header rectangle on page 0 and apply its background
    # color to header_paras that fall within the rectangle's y-range.  This
    # restores the dark-header appearance (e.g. sample 3: dark bar with white
    # text "CHARLES MCTURLAND") which is drawn as a vector rectangle rather than
    # a paragraph fill, so the PDF parser would otherwise not capture it.
    _hdr_bg_color, _hdr_y1 = _extract_header_bg(doc[0])
    if _hdr_bg_color:
        # Store on layout so the renderer can create a full-width header band
        # even for single-column PDFs (column_split_x=None).
        layout.header_bg_color = _hdr_bg_color
        # A full-width dark header above independent body columns → header_body_split.
        if layout.column_split_x is not None:
            layout.table_layout_mode = "header_body_split"
        for _pm in header_paras:
            _pp = _pm.paragraph_profile
            if _pp is None:
                continue
            # Apply to paragraphs whose y-position falls within the header band.
            # y_top_pt is the raw y0 from the PDF block; it's runtime-only but still
            # present after _extract_paragraphs.  If y_top_pt is None, apply to all
            # header_paras (they are all in the header area by definition).
            if _pp.y_top_pt is None or _pp.y_top_pt < _hdr_y1:
                _pp.background_color = _hdr_bg_color
                # Text on dark background must be white for visibility.
                # If no explicit text_color was captured from the PDF, default to white.
                if not _pp.text_color:
                    _pp.text_color = "ffffff"

    # Detect full-width dark footer band (same idea as header, but at bottom).
    # Sample 25 has a thin dark strip at y≈780 with white contact info text.
    # The bottom margin area (y > 810) is white, so we probe at y≈95% of page
    # height where the dark footer band lives, not at the very bottom edge.
    if layout.column_split_x is None:
        try:
            _page0 = doc[0]
            _ph = _page0.rect.height
            _pw = _page0.rect.width
            import fitz as _fitz
            _mat = _fitz.Matrix(1, 1)
            _cx = _pw / 2.0
            # Probe at 95% of page height (inside any footer band, above page-bottom margin)
            _probe_y = _ph * 0.95
            _pix_b = _page0.get_pixmap(matrix=_mat, clip=_fitz.Rect(_cx - 1, _probe_y, _cx + 1, _probe_y + 4))
            _bot_px = _pix_b.pixel(0, 0)
            _bot_lum = (_bot_px[0] + _bot_px[1] + _bot_px[2]) / 3.0
            if _bot_lum < 100:  # dark footer found
                # Scan upward from the probe point to find where the band starts
                _ftr_y0 = _probe_y
                for _y in range(int(_probe_y), max(0, int(_probe_y) - 120), -2):
                    _clip = _fitz.Rect(_cx - 1, _y - 2, _cx + 1, _y)
                    _px = _page0.get_pixmap(matrix=_mat, clip=_clip).pixel(0, 0)
                    if (_px[0] + _px[1] + _px[2]) / 3.0 > 220:
                        _ftr_y0 = float(_y)
                        break
                r, g, b = _bot_px[0], _bot_px[1], _bot_px[2]
                layout.footer_bg_color = f"{r:02x}{g:02x}{b:02x}"
                # Apply footer background to any paragraph whose top y falls
                # within the detected footer band.  text_color is already cleared
                # by _extract_paragraphs for non-heading paragraphs so we cannot
                # use it as a signal; use y_top_pt alone.
                _all_p = list(header_paras)
                for _sec in sections:
                    _all_p.extend(_sec.body_paras)
                    for _role in _sec.roles:
                        _all_p.extend(_role.bullets)
                for _pm in _all_p:
                    _pp = _pm.paragraph_profile
                    if _pp and not _pp.background_color:
                        if _pp.y_top_pt is not None and _pp.y_top_pt >= _ftr_y0:
                            _pp.background_color = layout.footer_bg_color
                            if not _pp.text_color:
                                _pp.text_color = "ffffff"
        except Exception:
            pass

    # Set header spacing to reproduce the dark header band's exact height from
    # the source PDF.  space_before on the first dark-bg header para controls the
    # gap from the band top to the name; space_after on the last dark-bg header
    # para controls the gap from the title to the band bottom (_hdr_y1).
    # y_top_pt is available here (set by _extract_paragraphs) but is runtime-only
    # and not persisted to the IR JSON.
    if _hdr_bg_color and header_paras:
        _dark_hdrs = [
            _pm for _pm in header_paras
            if _pm.paragraph_profile and _pm.paragraph_profile.background_color == _hdr_bg_color
        ]
        if _dark_hdrs:
            _first_dhdr = _dark_hdrs[0]
            _first_pp = _first_dhdr.paragraph_profile
            if _first_pp and _first_pp.y_top_pt is not None and _first_pp.space_before_pt == 0:
                _first_pp.space_before_pt = max(0.0, _first_pp.y_top_pt)
            _last_dhdr = _dark_hdrs[-1]
            _last_pp = _last_dhdr.paragraph_profile
            if _last_pp and _last_pp.y_top_pt is not None:
                _approx_bottom = _last_pp.y_top_pt + (_last_pp.font_size_pt or 12.0)
                # Cap space_after to avoid overflowing a single page when the body
                # content is taller than the original (e.g. fitz merges two-column
                # body into single-column, doubling line count).
                _last_pp.space_after_pt = min(30.0, max(0.0, _hdr_y1 - _approx_bottom))

    # Final color cleanup: _group_sections may reclassify paragraphs (e.g.
    # section_heading → role_header) after _extract_paragraphs already ran its
    # per-semantic clearing.  Sweep all paragraphs one more time so that any
    # reclassified paragraph does not retain a PDF-extracted text_color that
    # would bleed onto LLM-generated replacement content via clone_as.
    # Exception: paragraphs on a dark background (background_color set to a
    # non-white value) keep their text_color so white-on-dark text remains
    # visible after rendering.
    def _clear_content_colors(paras: "list[ParaModel]") -> None:
        for pm in paras:
            if pm.semantic in ("section_heading", "role_header") and pm.paragraph_profile:
                continue  # keep accent colors on structural headings
            if pm.paragraph_profile:
                # Keep text_color on dark-background paragraphs (e.g. white text
                # on the header band) so the text is visible after rendering.
                bg = pm.paragraph_profile.background_color
                if bg and bg not in ("ffffff", "fefefe", "f8f8f8"):
                    continue
                pm.paragraph_profile.text_color = None

    _clear_content_colors(header_paras)
    for _sec in sections:
        _clear_content_colors(_sec.body_paras)
        for _role in _sec.roles:
            _clear_content_colors([_role.header] + list(_role.header_extra) + _role.meta_lines + _role.bullets)

    # Rebuild all_paras from the structured IR so it reflects any post-processing
    # done by _finalise (e.g. bullet continuation line merging).  The raw flat
    # list is stale after merging; using the structured list keeps all_paras
    # consistent with what _doc_to_llm_text serialises and the renderer produces.
    all_paras: list[ParaModel] = list(header_paras)
    for section in sections:
        all_paras.append(section.heading)
        if section.semantic_type == "experience" and section.roles:
            # Emit pre-role orphan body_paras only when body_paras contains
            # an actual role_header paragraph (pipe-format resumes).  For
            # separate-line format resumes there is no role_header in
            # body_paras; skipping the orphan loop avoids duplicating all
            # body content that was already consumed into section.roles.
            # Paras already absorbed into a role's meta_lines (via pre_header_meta
            # buffering in _group_roles) are excluded by object-identity check to
            # prevent them from appearing twice in all_paras.
            if any(bp.semantic == "role_header" for bp in section.body_paras):
                _consumed_meta_ids = {
                    id(pm)
                    for role in section.roles
                    for pm in role.meta_lines
                }
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip() and id(bp) not in _consumed_meta_ids:
                        all_paras.append(bp)
            for role in section.roles:
                if role.header_extra and "|" not in role.header.text:
                    # PDF separate-line format: combine role title + company
                    # (header_extra) so all_paras matches what _doc_to_llm_text
                    # emits and the renderer writes to the DOCX.
                    _combined = role.header.text.strip() + " | " + " | ".join(
                        he.text.strip() for he in role.header_extra if he.text.strip()
                    )
                    all_paras.append(role.header.with_text(_combined))
                else:
                    all_paras.append(role.header)
                all_paras.extend(role.meta_lines)
                all_paras.extend(role.bullets)
        else:
            all_paras.extend(section.body_paras)

    # For two-column documents, reorder all_paras to match the rendered DOCX
    # paragraph order: above (col=None) → left → right.  The renderer writes the
    # left table cell before the right cell, so roundtrip tests must see the same
    # order in the source IR.  Python's sort is stable, so relative order within
    # each column is preserved.
    # Section-row table layouts keep the natural section-interleaved order
    # (heading followed by its body) so the grader and LLM text serialiser
    # see sections in the correct reading sequence.
    if layout.column_split_x is not None and not layout.section_row_table:
        def _col_order(pm: "ParaModel") -> int:
            col = pm.paragraph_profile.column_id if pm.paragraph_profile else None
            return 1 if col == "left" else (2 if col == "right" else 0)
        all_paras.sort(key=_col_order)

    resume_doc = ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=all_paras,
        source_kind="pdf",
    )
    # Raster images (profile photos, footer bars, etc.)
    raster_images = _extract_page_images(doc, page_index=0)
    # Solid-color overlays from vector drawing regions (sidebars, header/footer bands).
    # Prepended so they render behind raster images and text.
    vector_images = _extract_decorative_vector_images(doc[0])

    # Fix A: When a two-column layout with left column background color is detected,
    # filter out vector images representing that column background.  The two-column
    # table renderer (_render_pdf_two_col) already applies the color as w:shd cell
    # shading; re-inserting the same region as a floating image creates a z-order
    # conflict where a second dark shape appears over the content (sample 32).
    # "body_decor" in the left-column area is also filtered — in templates like
    # sample 32 the sidebar background spans two separate vector rects (a header
    # portion and a body portion), and the body portion gets classified as body_decor.
    if resume_doc.layout.column_split_x is not None:
        _split_x = resume_doc.layout.column_split_x
        _page_h = doc[0].rect.height
        _before = len(vector_images)
        # Always filter tall body_decor images in the column area — these are
        # decorative background fills (e.g. sample 32's black lower-left rect) that
        # belong to the original template design but should not appear as floating
        # images over rendered content, regardless of whether cell shading is active.
        vector_images = [
            img for img in vector_images
            if not (
                img.category == "body_decor"
                and img.x_pt + img.width_pt <= _split_x * 1.2
                and img.height_pt >= _page_h * 0.30
            )
        ]
        # Additional filtering when cell shading is active: the table renderer
        # (_render_pdf_two_col) applies left_col_bg_color as w:shd cell shading,
        # so floating images covering the same area add no value and create z-order
        # conflicts (double-rendering the column background).
        if resume_doc.layout.left_col_bg_color:
            vector_images = [
                img for img in vector_images
                if not (img.category == "full_page_bg")
                and not (
                    img.category in ("sidebar_bg", "body_decor")
                    and img.x_pt + img.width_pt <= _split_x * 1.2
                    and img.height_pt >= _page_h * 0.30
                ) and not (
                    img.category in ("header_band", "footer_band")
                    and img.height_pt >= _page_h * 0.30
                )
            ]
        if len(vector_images) < _before:
            log.debug(
                "VECTOR_BG_FILTERED: removed %d column-bg images",
                _before - len(vector_images),
            )

    resume_doc.page_images = vector_images + raster_images

    # Fix 15: Infer column_split_x from a tall narrow sidebar image when text-gap
    # detection failed.  Templates with sparse sidebar content (a few contact lines)
    # produce insufficient text density for gap detection, but a sidebar background
    # vector shape is still extracted.  Use its dimensions to establish the column split.
    # Handles both left-side sidebars (x_pt near 0) and right-side sidebars (x_pt+w near pw).
    if resume_doc.layout.column_split_x is None:
        _page0 = doc[0]
        _pw = _page0.rect.width
        _ph = _page0.rect.height
        _sidebar_img = None
        _sidebar_side: str = "left"
        for _img in resume_doc.page_images:
            if _img.category != "sidebar_bg":
                continue
            if _img.height_pt < _ph * 0.70:   # must cover most of page height
                continue
            if _img.width_pt > _pw * 0.35:    # must be narrow
                continue
            if _img.x_pt < _pw * 0.08:        # left-side sidebar
                _sidebar_img = _img
                _sidebar_side = "left"
                break
            if _img.x_pt + _img.width_pt > _pw * 0.92:  # right-side sidebar
                _sidebar_img = _img
                _sidebar_side = "right"
                break

        if _sidebar_img is not None:
            # Content-verification guard: count paragraphs that would land in the
            # sidebar zone.  A decorative edge-stripe (sample 15) has 0 content
            # paragraphs in its zone; a true sidebar has ≥3.  Suppress the inference
            # when fewer than 3 paragraphs are found in the sidebar zone so decorative
            # background accents don't create false two-column layouts.
            _all_doc_paras_check = list(resume_doc.header_paras) + list(resume_doc.all_paras)
            if _sidebar_side == "left":
                _check_split = _sidebar_img.x_pt + _sidebar_img.width_pt
                _in_zone = sum(
                    1 for _pm in _all_doc_paras_check
                    if _pm.paragraph_profile and 0 < _pm.paragraph_profile.body_text_x0_pt < _check_split
                )
            else:
                _check_split = _sidebar_img.x_pt
                _in_zone = sum(
                    1 for _pm in _all_doc_paras_check
                    if _pm.paragraph_profile and _pm.paragraph_profile.body_text_x0_pt >= _check_split
                )
            if _in_zone < 3:
                log.debug(
                    "SIDEBAR_INFERENCE_SUPPRESSED: only %d para(s) in sidebar zone — likely decorative",
                    _in_zone,
                )
                _sidebar_img = None

        if _sidebar_img is not None:
            # For a left sidebar: split = right edge; left cell = sidebar, right cell = body.
            # For a right sidebar: split = left edge; left cell = body, right cell = sidebar.
            if _sidebar_side == "left":
                _inferred_split = _sidebar_img.x_pt + _sidebar_img.width_pt
                _left_col_w = _inferred_split
                _right_col_w = _pw - _inferred_split
                _bg_attr = "left_col_bg_color"
            else:
                _inferred_split = _sidebar_img.x_pt
                _left_col_w = _inferred_split
                _right_col_w = _pw - _inferred_split
                _bg_attr = "right_col_bg_color"

            resume_doc.layout.column_split_x = _inferred_split
            resume_doc.layout.left_col_width_twips = int(_left_col_w * 20)
            resume_doc.layout.right_col_width_twips = int(_right_col_w * 20)
            if resume_doc.layout.table_layout_mode is None:
                resume_doc.layout.table_layout_mode = "sidebar_layout"
            # Extract sidebar color from center pixel of the solid-color PNG
            try:
                import fitz as _fitz_local
                import io as _io_local
                _spix = _fitz_local.Pixmap(_io_local.BytesIO(_sidebar_img.image_bytes))
                _spx = _spix.pixel(_spix.width // 2, _spix.height // 2)
                _sidebar_hex = f"{_spx[0]:02x}{_spx[1]:02x}{_spx[2]:02x}"
                if (_spx[0] + _spx[1] + _spx[2]) / 3 < 240:
                    setattr(resume_doc.layout, _bg_attr, _sidebar_hex)
            except Exception:
                pass
            # Re-assign column_ids for all paragraphs using body_text_x0_pt vs split.
            # Right-column indent is adjusted to be column-relative.
            # Paragraphs without a valid body_text_x0_pt default to the BODY column
            # ("left" for right-sidebar layouts, "left" for left-sidebar layouts
            # since the body content is always "left" in the renderer's view).
            # This prevents them from falling into the sidebar column via the
            # renderer's column_id=None → right fallback.
            _body_col = "left"   # the wide body cell is always the "left" column
            _all_doc_paras = list(resume_doc.header_paras) + list(resume_doc.all_paras)
            for _pm in _all_doc_paras:
                _pp = _pm.paragraph_profile
                if _pp is None:
                    continue
                if _pp.body_text_x0_pt <= 0.0:
                    # No valid x position — default to body column
                    _pp.column_id = _body_col
                    continue
                _x = _pp.body_text_x0_pt
                if _x < _inferred_split:
                    _pp.column_id = "left"
                else:
                    _pp.column_id = "right"
                    _pp.indent_left_pt = max(0.0, _x - _inferred_split)
            # Re-sort all_paras: left column sections before right column sections
            resume_doc.all_paras.sort(
                key=lambda _pm: (
                    1 if (_pm.paragraph_profile and _pm.paragraph_profile.column_id == "left")
                    else 2 if (_pm.paragraph_profile and _pm.paragraph_profile.column_id == "right")
                    else 0
                )
            )
            # Remove the sidebar image — column cell shading renders the background
            resume_doc.page_images = [
                img for img in resume_doc.page_images if img is not _sidebar_img
            ]
            log.debug(
                "SIDEBAR_COLUMN_INFERRED: side=%s split_x=%.1f from sidebar_bg %.0fx%.0f %s=%r",
                _sidebar_side, _inferred_split,
                _sidebar_img.width_pt, _sidebar_img.height_pt,
                _bg_attr, getattr(resume_doc.layout, _bg_attr),
            )

    from tailor.compiler.models import assign_stable_ids
    assign_stable_ids(resume_doc)

    # --- PDF asset preservation diagnostics ---
    from collections import Counter as _Counter
    _img_cats = dict(_Counter(img.category for img in resume_doc.page_images))
    _sidebar_inferred = (
        resume_doc.layout.column_split_x is not None
        and not any(img.category == "sidebar_bg" for img in resume_doc.page_images)
        # sidebar image was removed after inference
    )
    resume_doc.pdf_diagnostics = {
        "raster_images_raw": len(raster_images),
        "vector_images_raw": len(vector_images),
        "page_images_final": len(resume_doc.page_images),
        "image_categories": _img_cats,
        "column_split_x": resume_doc.layout.column_split_x,
        "table_layout_mode": resume_doc.layout.table_layout_mode,
        "header_bg_color": resume_doc.layout.header_bg_color,
        "footer_bg_color": resume_doc.layout.footer_bg_color,
        "left_col_bg_color": resume_doc.layout.left_col_bg_color,
        "right_col_bg_color": resume_doc.layout.right_col_bg_color,
        "sidebar_detected": resume_doc.layout.column_split_x is not None,
        "sidebar_inferred": _sidebar_inferred,
        "page_bg_detected": "full_page_bg" in _img_cats,
    }
    log.info(
        "PDF_ASSET_DIAGNOSTICS: raster=%d vector=%d final=%d cats=%s "
        "col_split=%.1f mode=%r hdr_bg=%r left_bg=%r sidebar=%s bg=%s",
        resume_doc.pdf_diagnostics["raster_images_raw"],
        resume_doc.pdf_diagnostics["vector_images_raw"],
        resume_doc.pdf_diagnostics["page_images_final"],
        resume_doc.pdf_diagnostics["image_categories"],
        resume_doc.layout.column_split_x or 0.0,
        resume_doc.pdf_diagnostics["table_layout_mode"],
        resume_doc.pdf_diagnostics["header_bg_color"],
        resume_doc.pdf_diagnostics["left_col_bg_color"],
        "inferred" if _sidebar_inferred else ("yes" if resume_doc.layout.column_split_x else "no"),
        "yes" if resume_doc.pdf_diagnostics["page_bg_detected"] else "no",
    )

    return resume_doc
