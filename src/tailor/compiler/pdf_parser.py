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

import re
from collections import Counter
from dataclasses import dataclass, field

from tailor.compiler.models import (
    LayoutProfile,
    ParagraphProfile,
    ParaModel,
    ParaStyle,
    ResumeDocument,
    ResumeSection,
    RoleEntry,
)

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


def _detect_header_footer_texts(doc) -> set[str]:
    """Return text strings that appear as page headers/footers.

    Two strategies:
    1. Exact text match: any string in the top/bottom 8% zone on ≥ half the
       pages is a H/F.
    2. Position bucket match: blocks at the same normalised Y (±2%) on ≥ half
       the pages are H/F, even when text changes (e.g. "Page 1 of 2" vs "Page
       2 of 2").
    3. Pages 2+ absolute: any block in the top/bottom 8% zone on page 2 or
       later is unconditionally excluded (page numbers, running heads).
    """
    n_pages = len(doc)
    if n_pages < 2:
        return set()

    hf_texts: set[str] = set()
    min_appearances = max(2, n_pages // 2)

    # Maps: y-bucket → list of (page_idx, text) tuples
    bucket_entries: dict[float, list[tuple[int, str]]] = {}
    text_page_count: Counter = Counter()
    page_zone_texts: list[set[str]] = []  # one set per page, zone texts only

    for page_idx, page in enumerate(doc):
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
    """
    try:
        drawings = page.get_drawings()
    except Exception:
        return None, None, None

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
        left_col_width_twips = int(col_boundary * 20)
        right_col_width_twips = int((rect.width - col_boundary) * 20)
    else:
        left_bg = right_bg = None
        col_boundary = None

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
    x0s = sorted({round(blk["bbox"][0]) for blk in blocks if blk.get("type") == 0})
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
    top_cutoff = page_height * 0.15 if page_height > 0 else 0.0
    # Full-width elements that span ≥ 50 % of the page width are cross-column
    # design elements (e.g. name banner, summary paragraph, section heading
    # that overflows visually) and should not veto the column split.
    wide_block_min = page_width * 0.50

    for i in range(len(x0s) - 1):
        gap = x0s[i + 1] - x0s[i]
        right_edge = x0s[i + 1]
        if gap >= min_gap and page_width * 0.20 <= right_edge <= page_width * 0.70:
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
            # in the top 15 % (header banner) are excluded.  Full-width blocks
            # (≥ 50 % page width) are also excluded — they are intentional
            # cross-column design elements, not evidence of a single column.
            bridge_x1_threshold = right_edge * 1.05
            bridging = any(
                b["bbox"][0] <= x0s[i]
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
                x0_mid = (x0s[i] + x0s[i + 1]) / 2.0
                left_body_x1s = [
                    b["bbox"][2] for b in blocks
                    if b.get("type") == 0
                    and b["bbox"][0] <= x0s[i]
                    and b["bbox"][1] >= top_cutoff
                ]
                if left_body_x1s:
                    max_left_x1 = max(left_body_x1s)
                    if max_left_x1 < right_edge:
                        content_mid = (max_left_x1 + right_edge) / 2.0
                        return max(x0_mid, content_mid)
                return x0_mid
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
        split_x = _detect_column_split(blocks, page.rect.width, page.rect.height)
        # Prefer the layout's split_x (computed on page 1) for consistency.
        if split_x is None and layout_split_x is not None:
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
            line_entries: list[tuple[str, list[dict], float, float, float]] = []  # (text, spans, line_y0, line_x0, line_y1)

            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if line_text:
                    # Collect per-line y0/y1 for accurate _has_bullet_dot matching
                    # and per-line space_before computation.
                    _line_bbox = line.get("bbox")
                    _line_y0 = float(_line_bbox[1]) if _line_bbox else 0.0
                    _line_y1 = float(_line_bbox[3]) if _line_bbox else _line_y0 + 12.0
                    _line_x0 = float(_line_bbox[0]) if _line_bbox else float(blk["bbox"][0])
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
                        prev_text, prev_spans, prev_y0, prev_x0, prev_y1 = line_entries[-1]
                        if _line_x0 >= prev_x0:
                            merged_text = prev_text + " " + line_text
                            merged_x0 = prev_x0
                        else:
                            merged_text = line_text + " " + prev_text
                            merged_x0 = _line_x0
                        line_entries[-1] = (merged_text.strip(), prev_spans + spans, prev_y0, merged_x0, prev_y1)
                        all_spans.extend(spans)
                    else:
                        line_entries.append((line_text, spans, _line_y0, _line_x0, _line_y1))
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
            if split_x is not None and x0 >= split_x:
                col_id: str | None = "right"
                col_origin = right_col_origin if right_col_origin is not None else split_x
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
            for line_idx, (line_text, line_spans, line_y0, line_x0, line_y1) in enumerate(line_entries):
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
                # Attach icon image to first line only (line_idx == 0) for
                # left-column paragraphs that coincide with a sidebar icon.
                icon_png: bytes | None = None
                icon_size_pt: float = 0.0
                if line_idx == 0 and col_id == "left" and icon_map:
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
                    indent_left_pt=max(0.0, _indent_x - col_origin),
                    body_text_x0_pt=line_x0,
                    space_before_pt=space_before if line_idx == 0 else 0.0,
                    text_color=line_text_color,
                    background_color=block_bg_color,
                    column_id=col_id,
                    inline_image_bytes=icon_png,
                    inline_image_size_pt=icon_size_pt,
                )
                pm = ParaModel(
                    text=line_text,
                    style=ParaStyle(),
                    semantic="",
                    paragraph_profile=profile,
                )
                pm.semantic = _infer_semantic(pm)
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
                if pm.semantic == "section_heading" and pm.paragraph_profile:
                    _heading_sb = min(_per_line_sb, 6.0)
                    if _heading_sb > pm.paragraph_profile.space_before_pt:
                        pm.paragraph_profile.space_before_pt = _heading_sb
                    elif pm.paragraph_profile.space_before_pt > 6.0:
                        pm.paragraph_profile.space_before_pt = 6.0
                    # Left-column headings often overflow the visual column
                    # boundary in the source PDF (PDF allows overflow; DOCX
                    # table cells enforce width and wrap the text).  Zeroing
                    # the indent gives the heading the full cell width so it
                    # renders on a single line.
                    if col_id == "left" and pm.paragraph_profile.indent_left_pt > 0:
                        pm.paragraph_profile.indent_left_pt = 0.0
                # Global cap: PDF absolute-position inter-block gaps inflate
                # DOCX flow-layout height.  Apply per-type limits:
                #   role_header: 4 pt max (block-level gap, needs some spacing)
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
                        if sb > 4.0:
                            pm.paragraph_profile.space_before_pt = 4.0
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
    if (
        bold
        and len(text) <= 60
        and "|" not in text
        and "," not in text
        and not text.startswith(("-", "•", "·", "–", "*"))
    ):
        words = text.split()
        if len(words) >= 2:
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
    "experience", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "work history", "professional background",
})
_SUMMARY_NAMES: frozenset[str] = frozenset({
    "professional summary", "summary", "objective", "career objective",
    "profile", "professional profile", "about me", "career summary",
    "executive summary",
})
_SKILLS_NAMES: frozenset[str] = frozenset({
    "technical skills", "skills", "core competencies", "competencies",
    "technical expertise", "expertise", "key skills", "areas of expertise",
    "technologies", "tech stack", "relevant skills", "skills & abilities",
    "skill summary",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees", "educational history",
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
        #   indentation in the single-column output.
        # • Large space_before values come from inter-bullet group gaps in the
        #   original PDF; they were capped at 3 pt for bullet paragraphs but
        #   must also be capped here to prevent page-count regressions.
        for _pm in meta:
            if _pm.paragraph_profile:
                if _pm.paragraph_profile.indent_left_pt > 4.0:
                    _pm.paragraph_profile.indent_left_pt = 0.0
                if _pm.paragraph_profile.space_before_pt > 3.0:
                    _pm.paragraph_profile.space_before_pt = 3.0
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
            if s == "paragraph" and not has_pipe_role_headers:
                _txt_init = pm.text.strip()
                _can_promote = (
                    has_explicit_bullets
                    # Pattern B: a date line was already buffered — this paragraph
                    # is the role title that follows the date.
                    or bool(pre_header_meta)
                    # Pattern A: paragraph immediately followed by a date line AND
                    # starts with a capital letter (job titles start with capitals;
                    # preamble/description fragments start with lowercase).
                    or (
                        has_role_meta
                        and _peek(idx + 1) == "role_meta"
                        and bool(_txt_init) and _txt_init[0].isupper()
                    )
                )
                if _can_promote:
                    # Separate-line format: this paragraph is the role title.
                    header = pm
                    if pre_header_meta:
                        # Pattern B: adopt buffered date(s) into this role's meta.
                        meta.extend(pre_header_meta)
                        pre_header_meta.clear()
                        used_pattern_b = True
                    state = "header"
            elif s == "role_meta" and not has_pipe_role_headers:
                # Pattern B: date appears before the title — buffer it.
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
                # Short line with no year and no sentence-end → continuation of
                # the role header (e.g. wrapped company name).  Otherwise,
                # check list geometry before buffering as potential bullet.
                _txt = pm.text.strip()
                _is_continuation = (
                    len(_txt) <= 60
                    and not _YEAR_RE.search(_txt)
                    and not _SENTENCE_END_RE.search(_txt)
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


def _group_sections(
    paras: list[ParaModel],
) -> tuple[list[ParaModel], list[ResumeSection]]:
    """Split flat paragraph list into header_paras + sections."""
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

    # Scanned document detection
    total_chars = sum(len(page.get_text()) for page in doc)
    if total_chars < _SCANNED_CHAR_THRESHOLD:
        raise RuntimeError(
            "This PDF cannot be parsed (likely scanned). "
            "Please use a non-scanned PDF or upload a .docx file."
        )

    layout = _extract_layout(doc)
    skip_pages = _deduplicate_page_indices(doc)
    hf_texts = _detect_header_footer_texts(doc)
    raw_paras = _extract_paragraphs(doc, hf_texts, layout.margin_left_pt, layout, skip_pages)
    header_paras, sections = _group_sections(raw_paras)

    # Final color cleanup: _group_sections may reclassify paragraphs (e.g.
    # section_heading → role_header) after _extract_paragraphs already ran its
    # per-semantic clearing.  Sweep all paragraphs one more time so that any
    # reclassified paragraph does not retain a PDF-extracted text_color that
    # would bleed onto LLM-generated replacement content via clone_as.
    # Only section_heading paragraphs may keep their accent color (template design).
    def _clear_content_colors(paras: "list[ParaModel]") -> None:
        for pm in paras:
            if pm.semantic in ("section_heading", "role_header") and pm.paragraph_profile:
                continue  # keep accent colors on structural headings
            if pm.paragraph_profile:
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
            if any(bp.semantic == "role_header" for bp in section.body_paras):
                for bp in section.body_paras:
                    if bp.semantic == "role_header":
                        break
                    if bp.text.strip():
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

    doc = ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=all_paras,
        source_kind="pdf",
    )
    from tailor.compiler.models import assign_stable_ids
    assign_stable_ids(doc)
    return doc
