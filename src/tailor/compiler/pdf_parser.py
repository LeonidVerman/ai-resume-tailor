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
_SCANNED_CHAR_THRESHOLD = 50  # fewer chars → likely scanned
_CONT_RE = re.compile(r"\s*\(cont\.?\)\s*$", re.IGNORECASE)

# Zone fractions for header/footer detection (top/bottom % of page height)
_HF_TOP_ZONE = 0.08
_HF_BOT_ZONE = 0.92
# Wider zones used for pages 2+ running-header detection (Strategy 4)
_HF_PAGE1_TOP_SCAN = 0.20   # top 20% of page 1 — collect candidate header lines
_HF_PAGE2_PLUS_ZONE = 0.25  # top 25% of pages 2+ — match against page 1 lines


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
    xs0 = [blk["bbox"][0] for blk in blocks if blk.get("type") == 0]
    if xs0:
        margin_left = max(0.0, min(xs0))

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

    default_font = font_counter.most_common(1)[0][0] if font_counter else "Calibri"
    default_size = size_counter.most_common(1)[0][0] if size_counter else 11.0

    return LayoutProfile(
        page_width_pt=rect.width,
        page_height_pt=rect.height,
        margin_top_pt=margin_top,
        margin_bottom_pt=margin_bottom_from_bottom,
        margin_left_pt=margin_left,
        margin_right_pt=margin_right_from_right,
        default_font_name=default_font,
        default_font_size_pt=default_size,
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

    font_name = font_counter.most_common(1)[0][0] if font_counter else None
    font_size_pt = size_counter.most_common(1)[0][0] if size_counter else None
    bold = bold_votes > total / 2 if total else False
    italic = italic_votes > total / 2 if total else False

    return {
        "font_name": font_name,
        "font_size_pt": font_size_pt,
        "bold": bold,
        "italic": italic,
    }


def _detect_column_split(blocks: list, page_width: float) -> float | None:
    """Return the x-split between two columns, or None for single-column pages.

    Collects the distinct x0 (left-edge) positions of all text blocks, sorts
    them, and looks for a gap that is ≥ 15 % of the page width.  To qualify as
    a column boundary the right edge of the gap must fall between 20 % and
    70 % of the page width — this excludes spurious gaps from a narrow left
    margin or a right-aligned page number.

    Returns the midpoint of the detected gap as the column split x-coordinate.
    """
    x0s = sorted({round(blk["bbox"][0]) for blk in blocks if blk.get("type") == 0})
    if len(x0s) < 4:
        return None

    min_gap = page_width * 0.15
    for i in range(len(x0s) - 1):
        gap = x0s[i + 1] - x0s[i]
        right_edge = x0s[i + 1]
        if gap >= min_gap and page_width * 0.20 <= right_edge <= page_width * 0.70:
            return (x0s[i] + x0s[i + 1]) / 2.0
    return None


def _extract_paragraphs(doc, hf_texts: set[str], margin_left: float) -> list[ParaModel]:
    """Extract all content paragraphs from the document, skipping H/F text."""
    paras: list[ParaModel] = []
    prev_block_y1: float | None = None
    hf_texts_lower = {t.lower() for t in hf_texts}

    for page in doc:
        page_margin_left = margin_left
        blocks = page.get_text("dict")["blocks"]

        # Re-estimate margin_left per page for accuracy
        xs0 = [blk["bbox"][0] for blk in blocks if blk.get("type") == 0]
        if xs0:
            page_margin_left = max(0.0, min(xs0))

        # Two-column detection: reorder blocks so the left column is read
        # completely before the right column.  PyMuPDF's default ordering is
        # top-to-bottom across ALL columns, interleaving sidebar content with
        # main content.  Column-aware reordering restores correct reading order.
        split_x = _detect_column_split(blocks, page.rect.width)
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
            line_entries: list[tuple[str, list[dict]]] = []  # (text, spans)

            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                line_text = "".join(s.get("text", "") for s in spans).strip()
                if line_text:
                    line_entries.append((line_text, spans))
                    all_spans.extend(spans)

            if not line_entries:
                prev_block_y1 = blk["bbox"][3]
                continue

            bbox = blk["bbox"]
            x0, y0, y1 = bbox[0], bbox[1], bbox[3]

            # Skip headers/footers (check full block text and single-line join,
            # both exact and case-insensitive)
            full_text = "\n".join(t for t, _ in line_entries)
            single_line = " ".join(t for t, _ in line_entries)
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

            # Emit one ParaModel per line.
            # Each line within a block inherits the block's font profile;
            # space_before is applied to the first line only.
            for line_idx, (line_text, line_spans) in enumerate(line_entries):
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
                profile = ParagraphProfile(
                    font_name=line_fi["font_name"],
                    font_size_pt=line_fi["font_size_pt"],
                    bold=line_fi["bold"],
                    italic=line_fi["italic"],
                    indent_left_pt=max(0.0, x0 - page_margin_left),
                    space_before_pt=space_before if line_idx == 0 else 0.0,
                )
                pm = ParaModel(
                    text=line_text,
                    style=ParaStyle(),
                    semantic="",
                    paragraph_profile=profile,
                )
                pm.semantic = _infer_semantic(pm)
                # Normalize bullet text: strip leading bullet prefix so the IR
                # stores bare content, consistent with DOCX-parsed paragraphs.
                if pm.semantic == "bullet":
                    for _pfx in ("- ", "• ", "· ", "– ", "* "):
                        if pm.text.startswith(_pfx):
                            pm.text = pm.text[len(_pfx):]
                            break
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

    bold = pp.bold if pp else False
    font_size = pp.font_size_pt if pp else None
    space_before = pp.space_before_pt if pp else 0.0
    indent = pp.indent_left_pt if pp else 0.0

    # Section heading: bold, short, title-case, 2+ words, larger or spaced
    if (
        bold
        and len(text) <= 60
        and "|" not in text
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

    # Bullet: starts with bullet character or is indented list item
    if text.startswith(("- ", "• ", "· ", "– ", "* ")):
        return "bullet"

    # Date/location meta line: contains a year, short, no pipe
    if _YEAR_RE.search(text) and len(text) <= 80 and "|" not in text:
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
    "technologies", "tech stack",
})
_EDUCATION_NAMES: frozenset[str] = frozenset({
    "education", "academic background", "academic credentials",
    "educational background", "degrees",
})

_ALL_KNOWN: frozenset[str] = (
    _EXPERIENCE_NAMES | _SUMMARY_NAMES | _SKILLS_NAMES | _EDUCATION_NAMES
)


def _classify_section(heading_text: str) -> str:
    t = heading_text.strip().lower()
    if t in _EXPERIENCE_NAMES:
        return "experience"
    if t in _SUMMARY_NAMES:
        return "summary"
    if t in _SKILLS_NAMES:
        return "skills"
    if t in _EDUCATION_NAMES:
        return "education"
    return "other"


def _group_roles(body_paras: list[ParaModel]) -> list[RoleEntry]:
    """Group experience body paragraphs into RoleEntry objects."""
    roles: list[RoleEntry] = []
    header: ParaModel | None = None
    header_extra: list[ParaModel] = []
    meta: list[ParaModel] = []
    bullets: list[ParaModel] = []
    state = "init"

    def _flush() -> None:
        nonlocal header
        if header is None:
            return
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

    for pm in body_paras:
        s = pm.semantic
        if s == "role_header":
            _flush()
            header = pm
            state = "header"
        elif state == "init":
            pass
        elif state == "header":
            if s == "role_meta":
                meta.append(pm)
                state = "meta"
            elif s == "bullet":
                bullets.append(pm)
                state = "bullets"
            elif s == "paragraph":
                header_extra.append(pm)
            elif s == "empty":
                pass
            else:
                meta.append(pm)
        elif state == "meta":
            if s == "role_meta":
                meta.append(pm)
            elif s in ("bullet", "paragraph"):
                bullets.append(pm)
                state = "bullets"
            elif s == "empty":
                pass
            else:
                bullets.append(pm)
                state = "bullets"
        elif state == "bullets":
            if s in ("bullet", "paragraph", "role_meta"):
                # role_meta can appear mid-bullet-list when a line contains a year
                # (e.g. "Resolved 150 bugs since June 2023 for apps post-launch to")
                # but is clearly a continuation bullet, not a date/meta line.
                bullets.append(pm)
        else:
            pass

    _flush()
    return roles


def _group_sections(
    paras: list[ParaModel],
) -> tuple[list[ParaModel], list[ResumeSection]]:
    """Split flat paragraph list into header_paras + sections."""
    header_paras: list[ParaModel] = []
    sections: list[ResumeSection] = []
    current: ResumeSection | None = None
    found_section = False

    for pm in paras:
        if pm.semantic == "section_heading":
            # Before the first known section, only accept known headings
            # to avoid treating names / job titles as sections.
            if not found_section and pm.text.strip().lower() not in _ALL_KNOWN:
                header_paras.append(pm)
                continue

            found_section = True
            if current is not None:
                _finalise(current)
                sections.append(current)
            current = ResumeSection(
                title=pm.text.strip(),
                heading=pm,
                semantic_type=_classify_section(pm.text),
            )
        elif not found_section:
            header_paras.append(pm)
        elif current is not None:
            current.body_paras.append(pm)

    if current is not None:
        _finalise(current)
        sections.append(current)

    return header_paras, sections


def _finalise(section: ResumeSection) -> None:
    if section.semantic_type == "experience":
        section.roles = _group_roles(section.body_paras)


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
    hf_texts = _detect_header_footer_texts(doc)
    all_paras = _extract_paragraphs(doc, hf_texts, layout.margin_left_pt)
    header_paras, sections = _group_sections(all_paras)

    return ResumeDocument(
        header_paras=header_paras,
        sections=sections,
        layout=layout,
        all_paras=all_paras,
        source_kind="pdf",
    )
