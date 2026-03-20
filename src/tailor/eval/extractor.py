"""Extract structured text and geometry from a PDF using PyMuPDF.

Produces an ExtractedDoc with pages, lines, blocks, heading/bullet
candidates, dominant layout metrics, and column-count estimate.
"""
from __future__ import annotations

import re
from collections import Counter
from statistics import median
from typing import TYPE_CHECKING

from tailor.eval.models import (
    BlockModel,
    DocumentFeatures,
    ExtractedDoc,
    LineModel,
    PageModel,
    SpanModel,
)

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Known resume section names (normalized, no spaces)
# ---------------------------------------------------------------------------
_KNOWN_SECTIONS_NOSPACE: frozenset[str] = frozenset({
    "summary", "professionalsummary", "objective", "careerobjective",
    "profile", "professionalprofile", "aboutme", "careersummary",
    "executivesummary",
    "experience", "workexperience", "professionalexperience",
    "employmenthistory", "employment", "careerhistory",
    "workhistory", "professionalbackground",
    "skills", "technicalskills", "corecompetencies", "competencies",
    "technicalexpertise", "expertise", "keyskills", "areasofexpertise",
    "technologies", "techstack",
    "education", "academicbackground", "academiccredentials",
    "educationalbackground", "degrees",
    "projects", "certifications", "certification", "publications",
    "awards", "honors", "languages", "references", "activities",
    "volunteer", "volunteering", "leadership", "interests",
    "additionalinformation",
})

# Bullet marker characters (text-based detection)
_BULLET_MARKER_RE = re.compile(
    r"^[•·▪▸◦→›●○■□◆◇✓✔✗✘\-\*]\s|^\uf0b7\s?|^[\ue000-\uf8ff]\s?"
)


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """Normalize PDF text for comparison.

    - Strips Private Use Area glyphs (icon fonts)
    - Normalises bullet marker variants to "• "
    - Normalises em/en dashes to " - "
    - Collapses whitespace
    """
    # Strip PUA chars (icon glyphs)
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    # Normalize common bullet prefixes
    text = re.sub(r"^[•·▪▸◦→›●○■□◆◇✓✔✗✘]\s+", "• ", text.lstrip())
    # Normalize dashes
    text = text.replace("\u2014", " - ").replace("\u2013", " - ")
    # NBSP → space
    text = text.replace("\xa0", " ")
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _normalize_heading_text(text: str) -> str:
    """Lowercase, strip non-alphanumerics and spaces for known-name matching."""
    return re.sub(r"[^a-z]", "", text.lower())


# ---------------------------------------------------------------------------
# Span → line helpers
# ---------------------------------------------------------------------------

def _is_bold(flags: int, font_name: str) -> bool:
    return bool(flags & (1 << 4)) or "bold" in font_name.lower()


def _is_italic(flags: int, font_name: str) -> bool:
    return bool(flags & (1 << 1)) or "italic" in font_name.lower()


# ---------------------------------------------------------------------------
# Column detection
# ---------------------------------------------------------------------------

def _detect_columns(
    lines: list[LineModel],
    page_width: float,
) -> tuple[int, str]:
    """Return (column_count_estimate, confidence).

    Uses x-coordinate clustering of line left edges.  Two distinct clusters
    separated by >15% of page width → 2 columns.
    """
    if not lines:
        return 1, "none"

    # Collect left_x from non-empty, non-short lines
    left_xs = [
        round(ln.left_x)
        for ln in lines
        if len(ln.text.strip()) > 5
    ]
    if len(left_xs) < 4:
        return 1, "low"

    # Sort and find the largest gap
    sorted_xs = sorted(set(left_xs))
    if len(sorted_xs) < 2:
        return 1, "low"

    gaps = [(sorted_xs[i + 1] - sorted_xs[i], i) for i in range(len(sorted_xs) - 1)]
    max_gap, max_gap_idx = max(gaps, key=lambda x: x[0])

    min_col_gap = page_width * 0.15
    if max_gap < min_col_gap:
        return 1, "low"

    # Check each cluster has enough lines
    split_x = (sorted_xs[max_gap_idx] + sorted_xs[max_gap_idx + 1]) / 2
    left_col = [x for x in left_xs if x < split_x]
    right_col = [x for x in left_xs if x >= split_x]

    if len(left_col) >= 3 and len(right_col) >= 3:
        return 2, "high"
    elif len(left_col) >= 2 and len(right_col) >= 2:
        return 2, "low"
    return 1, "low"


# ---------------------------------------------------------------------------
# Dominant layout stats
# ---------------------------------------------------------------------------

def _dominant_value(values: list[float], round_to: float = 2.0) -> float | None:
    """Return the most frequent rounded value from a list."""
    if not values:
        return None
    rounded = [round(v / round_to) * round_to for v in values]
    return Counter(rounded).most_common(1)[0][0]


def _compute_dominant_body_left(lines: list[LineModel]) -> float:
    """Find the dominant left_x among body-text lines (length > 15 chars)."""
    body_left_xs = [
        ln.left_x
        for ln in lines
        if len(ln.text.strip()) > 15 and not ln.is_heading_candidate
    ]
    result = _dominant_value(body_left_xs, round_to=2.0)
    if result is None:
        # Fall back to all lines
        all_left = [ln.left_x for ln in lines if ln.text.strip()]
        result = _dominant_value(all_left, round_to=2.0) or 0.0
    return result


# ---------------------------------------------------------------------------
# Heading and bullet classification
# ---------------------------------------------------------------------------

def _classify_line(
    line_text: str,
    is_bold: bool,
    font_size: float,
    space_before: float,
    dominant_font_size: float,
    dominant_left_x: float,
    line_left_x: float,
    page_width: float,
) -> tuple[bool, bool]:
    """Return (is_heading_candidate, is_bullet_candidate)."""
    text = line_text.strip()
    if not text:
        return False, False

    # Bullet candidate: starts with a marker OR is sufficiently indented
    # compared to the dominant body left anchor with hanging indent geometry
    is_bullet = bool(_BULLET_MARKER_RE.match(text))
    # Also: PUA-stripped text after a bullet char
    if not is_bullet and line_left_x > dominant_left_x + 4.0:
        is_bullet = True   # indent-based heuristic; may be refined by block context

    # Heading candidate: known section name (normalized)
    norm = _normalize_heading_text(text)
    if norm in _KNOWN_SECTIONS_NOSPACE:
        return True, False

    # Heading candidate: heuristic (bold + short + well-spaced + no comma).
    # Requires 2+ words (single capitalized words are typically names/locations,
    # not section headings).  Both font-size and spacing thresholds are stricter
    # than before to reduce false positives from resume header lines (name,
    # job title) which are bold but not section headings.
    if (
        is_bold
        and len(text) <= 60
        and "|" not in text
        and "," not in text
        and not text.startswith(("-", "•", "·"))
    ):
        words = text.split()
        if len(words) >= 2:
            cap_ratio = sum(1 for w in words if w and w[0].isupper()) / len(words)
            larger_font = font_size >= dominant_font_size * 1.10
            well_spaced = space_before >= 6.0
            if cap_ratio >= 0.7 and (larger_font or well_spaced):
                return True, False

    return False, is_bullet


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract(pdf_path: str) -> ExtractedDoc:
    """Extract structured geometry and text from *pdf_path*.

    Returns an :class:`ExtractedDoc` with pages, lines, blocks,
    heading/bullet candidates, and document-level features.
    """
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    pages: list[PageModel] = []
    all_lines_all_pages: list[LineModel] = []

    for page_idx, page in enumerate(doc):
        page_dict = page.get_text("dict", flags=fitz.TEXT_PRESERVE_WHITESPACE)
        pw = page_dict.get("width", page.rect.width)
        ph = page_dict.get("height", page.rect.height)

        raw_lines: list[LineModel] = []
        for blk in page_dict.get("blocks", []):
            if blk.get("type") != 0:   # skip image blocks
                continue
            for ln in blk.get("lines", []):
                spans: list[SpanModel] = []
                for sp in ln.get("spans", []):
                    sp_text = sp.get("text", "")
                    if not sp_text:
                        continue
                    flags = sp.get("flags", 0)
                    font = sp.get("font", "")
                    bbox = tuple(sp.get("bbox", [0, 0, 0, 0]))
                    spans.append(SpanModel(
                        text=sp_text,
                        bbox=bbox,
                        font_size=sp.get("size", 0),
                        font_name=font,
                        is_bold=_is_bold(flags, font),
                        is_italic=_is_italic(flags, font),
                    ))

                if not spans:
                    continue
                ln_text = "".join(s.text for s in spans)
                ln_text_stripped = ln_text.strip()
                if not ln_text_stripped:
                    continue

                ln_bbox = tuple(ln.get("bbox", [0, 0, pw, 12]))
                raw_lines.append(LineModel(
                    text=ln_text,
                    bbox=ln_bbox,
                    spans=spans,
                    left_x=ln_bbox[0],
                    right_x=ln_bbox[2],
                    baseline_y=(ln_bbox[1] + ln_bbox[3]) / 2,
                ))

        if not raw_lines:
            pages.append(PageModel(
                page_number=page_idx + 1,
                width=pw, height=ph,
                lines=[], blocks=[],
            ))
            continue

        # Dominant font size on this page
        font_sizes = [s.font_size for ln in raw_lines for s in ln.spans if s.font_size > 0]
        dominant_font_size = _dominant_value(font_sizes, 0.5) or 10.0

        # Dominant left_x (body text reference) — first pass estimate
        dom_left = _dominant_value([ln.left_x for ln in raw_lines], 2.0) or 0.0

        # Group raw_lines into blocks by vertical gap
        raw_lines.sort(key=lambda ln: ln.baseline_y)
        line_heights = [
            ln.bbox[3] - ln.bbox[1] for ln in raw_lines if (ln.bbox[3] - ln.bbox[1]) > 0
        ]
        median_lh = median(line_heights) if line_heights else 12.0
        gap_threshold = median_lh * 1.4

        blocks_raw: list[list[LineModel]] = []
        cur_block: list[LineModel] = [raw_lines[0]]
        for ln in raw_lines[1:]:
            gap = ln.baseline_y - cur_block[-1].baseline_y
            if gap > gap_threshold:
                blocks_raw.append(cur_block)
                cur_block = [ln]
            else:
                cur_block.append(ln)
        blocks_raw.append(cur_block)

        # Compute spacing between blocks
        block_spacings: list[float] = []
        for i in range(1, len(blocks_raw)):
            spacing = blocks_raw[i][0].baseline_y - blocks_raw[i - 1][-1].baseline_y
            block_spacings.append(max(0.0, spacing))

        # Classify lines (heading/bullet) and assemble BlockModels
        blocks: list[BlockModel] = []
        for b_idx, blk_lines in enumerate(blocks_raw):
            spacing_before = block_spacings[b_idx - 1] if b_idx > 0 else 0.0
            spacing_after = block_spacings[b_idx] if b_idx < len(block_spacings) else 0.0

            blk_left_xs = [ln.left_x for ln in blk_lines]
            blk_dominant_left = _dominant_value(blk_left_xs, 1.0) or dom_left

            is_bold_blk = any(
                s.is_bold
                for ln in blk_lines
                for s in ln.spans
            )
            blk_font_size = _dominant_value(
                [s.font_size for ln in blk_lines for s in ln.spans if s.font_size > 0],
                0.5,
            ) or dominant_font_size

            classified_lines: list[LineModel] = []
            for ln in blk_lines:
                is_bold_ln = any(s.is_bold for s in ln.spans)
                ln_font_size = _dominant_value(
                    [s.font_size for s in ln.spans if s.font_size > 0], 0.5
                ) or blk_font_size
                is_hdg, is_blt = _classify_line(
                    ln.text,
                    is_bold=is_bold_ln,
                    font_size=ln_font_size,
                    space_before=spacing_before,
                    dominant_font_size=dominant_font_size,
                    dominant_left_x=dom_left,
                    line_left_x=ln.left_x,
                    page_width=pw,
                )
                ln.is_heading_candidate = is_hdg
                ln.is_bullet_candidate = is_blt
                classified_lines.append(ln)

            # Block type
            has_heading = any(ln.is_heading_candidate for ln in classified_lines)
            has_bullet = any(ln.is_bullet_candidate for ln in classified_lines)
            if has_heading:
                blk_type = "heading"
            elif has_bullet:
                blk_type = "bullet_group"
            elif blk_font_size > dominant_font_size * 1.3:
                blk_type = "header"
            else:
                blk_type = "paragraph"

            blk_bbox = (
                min(ln.bbox[0] for ln in blk_lines),
                blk_lines[0].bbox[1],
                max(ln.bbox[2] for ln in blk_lines),
                blk_lines[-1].bbox[3],
            )
            blocks.append(BlockModel(
                block_id=f"p{page_idx + 1}_b{b_idx}",
                block_type=blk_type,
                bbox=blk_bbox,
                lines=classified_lines,
                dominant_left_x=blk_dominant_left,
                spacing_before=spacing_before,
                spacing_after=spacing_after,
            ))

        # Content bounding box
        all_bboxes = [blk.bbox for blk in blocks]
        content_bbox: tuple | None = None
        if all_bboxes:
            content_bbox = (
                min(b[0] for b in all_bboxes),
                min(b[1] for b in all_bboxes),
                max(b[2] for b in all_bboxes),
                max(b[3] for b in all_bboxes),
            )

        all_lines = [ln for blk in blocks for ln in blk.lines]
        pages.append(PageModel(
            page_number=page_idx + 1,
            width=pw, height=ph,
            lines=all_lines,
            blocks=blocks,
            content_bbox=content_bbox,
        ))
        all_lines_all_pages.extend(all_lines)

    doc.close()

    # Document-level features
    all_page_lines = [ln for pg in pages for ln in pg.lines]
    dom_body_left = _compute_dominant_body_left(all_page_lines)

    line_gaps_all: list[float] = []
    section_gaps_all: list[float] = []
    for pg in pages:
        for blk in pg.blocks:
            if blk.spacing_before > 0:
                if blk.block_type == "heading":
                    section_gaps_all.append(blk.spacing_before)
                else:
                    line_gaps_all.append(blk.spacing_before)

    dom_line_gap = median(line_gaps_all) if line_gaps_all else 0.0
    dom_section_gap = median(section_gaps_all) if section_gaps_all else 0.0

    # Column detection (use first page or all pages combined)
    first_page_lines = pages[0].lines if pages else []
    page_width = pages[0].width if pages else 612.0
    col_count, col_conf = _detect_columns(first_page_lines, page_width)

    # Heading list
    headings: list[str] = []
    seen: set[str] = set()
    for pg in pages:
        for blk in pg.blocks:
            if blk.block_type == "heading":
                for ln in blk.lines:
                    if ln.is_heading_candidate:
                        norm = normalize_text(ln.text)
                        if norm and norm not in seen:
                            headings.append(norm)
                            seen.add(norm)

    # Bullet count and dominant bullet left_x.
    bullet_lines = [
        ln for pg in pages for blk in pg.blocks
        for ln in blk.lines if ln.is_bullet_candidate
    ]
    dominant_bullet_left_x = _dominant_value(
        [ln.left_x for ln in bullet_lines], 2.0
    ) if bullet_lines else None

    # Full normalized text
    full_text = " ".join(
        normalize_text(ln.text)
        for pg in pages
        for ln in pg.lines
        if normalize_text(ln.text)
    )

    # Dominant left margins list (top-3 most frequent)
    left_x_counter = Counter(
        round(ln.left_x / 2.0) * 2.0
        for pg in pages for ln in pg.lines if ln.text.strip()
    )
    dom_left_margins = [x for x, _ in left_x_counter.most_common(3)]

    features = DocumentFeatures(
        page_count=len(pages),
        column_count_estimate=col_count,
        column_confidence=col_conf,
        dominant_left_margins=dom_left_margins,
        dominant_line_gap=dom_line_gap,
        dominant_section_gap=dom_section_gap,
        dominant_body_left_x=dom_body_left,
    )

    return ExtractedDoc(
        path=pdf_path,
        pages=pages,
        features=features,
        full_text_normalized=full_text,
        headings=headings,
        bullet_count=len(bullet_lines),
        dominant_bullet_left_x=dominant_bullet_left_x,
    )
