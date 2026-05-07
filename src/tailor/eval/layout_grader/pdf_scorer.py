"""PDF Visual Scoring — primary layout quality signal.

Extracts structured geometry from template and generated PDFs via the
existing tailor.eval.extractor, then computes five independent sub-scores.

Sub-scores (each 0-100):

  page_count_score   page growth; > +2 -> HARD FAIL
  blank_page_score   blank middle page -> HARD FAIL; trailing blank -> 60
  region_score       section presence + vertical region + column consistency
  container_score    content area expansion per page (overflow heuristic)
  density_score      IR-derived bullet/paragraph density
  sparse_page_score  non-first page that is substantially underfilled after a
                     well-packed previous page (forced-break spill artifact)

HARD FAIL triggers:
  PAGE_COUNT_OVERFLOW   generated_pages > original_pages + 2
  BLANK_MIDDLE_PAGE     a non-last page has near-zero text content
  COLUMN_LAYOUT_LOST    original was multi-column, generated is single-column
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_BLANK_LINE_THRESHOLD = 4
_BLANK_CHAR_THRESHOLD = 150

# Sparse continuation page detection thresholds.
# A non-first page triggers C_SPARSE_CONTINUATION_PAGE when ALL of:
#   fill_fraction   < _SPARSE_FILL_THRESHOLD        (large empty area at bottom)
#   prev_page_fill  ≥ _SPARSE_PREV_PAGE_MIN_FILL    (previous page was densely used)
#   line_count      ≥ _SPARSE_MIN_LINES             (substantial content on page,
#                                                     not a trivial tail overflow)
#
# Threshold calibration note:
# 65% fill means the content span covers < 65% of the page height, leaving a
# visible blank region at the bottom.  A page where the content ends at 64% of
# the page height (36% blank below the last block) is perceptually incomplete —
# a human reading it sees an unfinished page.  The threshold must be ABOVE the
# current fill of any page that a renderer fix should target; we do not lower it
# to accommodate pages we have not yet properly fixed.
_SPARSE_FILL_THRESHOLD = 0.65         # content fills < 65% of page height
_SPARSE_PREV_PAGE_MIN_FILL = 0.75     # previous page must be ≥ 75% full
_SPARSE_MIN_LINES = 18                # page must have ≥ 18 content lines

# Hard fail escalation within detected sparse pages.
# Uses effective_area_ratio (sum of block bounding-box areas / page area) which
# captures actual text density independent of how spread-out the blocks are.
# A fill_fraction (vertical span) of 65% can hide a page that is visually 70-80%
# empty when the blocks are small or widely spaced.
# Threshold is 0.30 (not 0.25) because a page where text covers only 27-29% of
# the total page area is still predominantly empty (73-80% whitespace) regardless
# of whether it just cleared the 25% mark via minor layout changes like keepNext.
_SPARSE_HARD_FAIL_AREA_RATIO = 0.30   # block areas < 30% of page area → hard fail

# Minimum chars for a block to be considered 'meaningful' in area calculations.
_SPARSE_MIN_BLOCK_CHARS = 3

_SECTION_ALIASES: dict[str, list[str]] = {
    "experience": [
        "experience", "work experience", "professional experience",
        "employment history", "work history", "career history", "employment",
        "experiences",
    ],
    "education": ["education", "academic", "qualifications", "degree"],
    "skills": [
        "skills", "competencies", "expertise", "technologies",
        "tech stack", "technical skills", "core competencies",
    ],
    "summary": ["summary", "profile", "objective", "about", "overview", "career summary"],
    "certifications": ["certif", "certification", "license"],
    "projects": ["project"],
}


def _canonical_section(heading: str) -> str:
    h = heading.lower().strip()
    for canonical, aliases in _SECTION_ALIASES.items():
        if any(a in h for a in aliases):
            return canonical
    return re.sub(r"[^a-z0-9]", "", h)


# Character threshold below which a page is considered "effectively empty" for the
# purpose of content-loss hard-fail escalation.  The existing _BLANK_CHAR_THRESHOLD
# (150 chars) catches pages that are visually blank but may still contain some
# extractable text (e.g. xhtml2pdf encoding artefacts or non-breaking spaces).
# For hard-fail escalation we require near-zero content so that PDF-extraction
# artefacts on otherwise correct renders do not become false hard-fails.
_BLANK_EFFECTIVELY_EMPTY_CHARS = 50


def _find_blank_pages(extracted) -> list[int]:
    blank = []
    for page in extracted.pages:
        total_text = " ".join(line.text for line in page.lines).strip()
        if len(page.lines) < _BLANK_LINE_THRESHOLD and len(total_text) < _BLANK_CHAR_THRESHOLD:
            blank.append(page.page_number)
    return blank


def _page_total_chars(page) -> int:
    """Raw character count across all lines on a page (no threshold applied)."""
    return sum(len(ln.text) for ln in page.lines)


def _heading_region_map(extracted, page_height: float) -> dict[str, str]:
    """Map canonical heading names to vertical region (top/middle/bottom)."""
    _regions = ("top", "middle", "bottom")
    result: dict[str, str] = {}
    for page in extracted.pages:
        for block in page.blocks:
            if block.block_type not in ("heading", "header"):
                continue
            if not block.lines:
                continue
            text = _canonical_section(block.lines[0].text)
            if text and text not in result:
                y_center = (block.bbox[1] + block.bbox[3]) / 2.0
                rel = y_center / page_height if page_height > 0 else 0.5
                if rel < 0.35:
                    region = "top"
                elif rel < 0.70:
                    region = "middle"
                else:
                    region = "bottom"
                result[text] = region
    return result


# ---------------------------------------------------------------------------
# A. Page count
# ---------------------------------------------------------------------------

def _compute_page_count_score(
    orig_pages: int, gen_pages: int
) -> tuple[float, bool, list[str]]:
    delta = gen_pages - orig_pages
    evidence: list[str] = []
    if delta <= 1:
        return 100.0, False, evidence
    if delta == 2:
        evidence.append(f"Page count +2 ({orig_pages}->{gen_pages})")
        return 70.0, False, evidence
    evidence.append(f"Page count +{delta} ({orig_pages}->{gen_pages}) -- HARD FAIL")
    return 0.0, True, evidence


# ---------------------------------------------------------------------------
# B. Blank page
# ---------------------------------------------------------------------------

def _compute_blank_page_score(
    gen_extracted,
    orig_pages: int = 0,
    orig_extracted=None,
) -> tuple[float, bool, list[str], list[int]]:
    """Score blank-page presence.

    Hard-fail conditions (BLANK_MIDDLE_PAGE / BLANK_PAGE_CONTENT_LOSS):
    - Any blank page that is NOT the trailing page → forced hard fail (always).
    - Trailing blank when generated_pages == original_pages AND:
      - the template page at the same position is NOT blank in the same converter
        (distinguishes genuine content loss from systematic xhtml2pdf limitations
        on complex templates where BOTH template and generated produce blank PDFs),
      - AND the generated page is effectively empty (< 50 total chars).

    The template-comparison gate is critical: many complex DOCX templates render
    with xhtml2pdf as blank pages regardless of content (font/encoding issues).
    For those templates, the GENERATED document is also blank — not because the
    renderer failed, but because xhtml2pdf can't extract text from either.  Only
    when the template PDF has extractable content but the generated PDF does not
    is there genuine content loss.
    """
    blank = _find_blank_pages(gen_extracted)
    gen_pages = len(gen_extracted.pages)
    evidence: list[str] = []
    if not blank:
        return 100.0, False, evidence, blank

    last_page = gen_extracted.pages[-1].page_number if gen_extracted.pages else 0
    middle_blank = [p for p in blank if p < last_page]
    trailing_blank = [p for p in blank if p >= last_page]

    if middle_blank:
        evidence.append(f"Blank middle page(s) {middle_blank} -- HARD FAIL")
        return 0.0, True, evidence, blank

    if not trailing_blank:
        return 100.0, False, evidence, blank

    # Determine which trailing blank pages represent genuine content loss vs
    # systematic PDF-extraction limitations.
    #
    # Genuine content loss: the template PDF has extractable content on that page
    # (template is NOT blank there) but the generated PDF is blank AND near-zero
    # chars (< 50 chars — not just sparse, but truly empty).
    #
    # Extraction artifact: both template and generated are blank for the same
    # page — xhtml2pdf cannot extract from this template type regardless.
    template_blank_pages: set[int] = set()
    if orig_extracted is not None:
        template_blank_pages = set(_find_blank_pages(orig_extracted))

    content_loss_pages = []
    for pg_num in trailing_blank:
        # Skip if template page at the same position is also blank
        if pg_num in template_blank_pages:
            continue
        # Skip if page count differs from template (tail overflow — expected)
        page_count_preserved = (orig_pages > 0 and gen_pages == orig_pages) or gen_pages == 1
        if not page_count_preserved:
            continue
        # Require near-zero content (not just < 150-char extraction artifact)
        gen_page = next(
            (p for p in gen_extracted.pages if p.page_number == pg_num), None
        )
        if gen_page is None:
            continue
        if _page_total_chars(gen_page) < _BLANK_EFFECTIVELY_EMPTY_CHARS:
            content_loss_pages.append(pg_num)

    if content_loss_pages:
        evidence.append(
            f"Blank trailing page(s) {content_loss_pages} -- HARD FAIL: "
            f"page count matches template ({orig_pages}→{gen_pages}) but page is empty"
        )
        return 0.0, True, evidence, blank

    evidence.append(f"Blank trailing page(s) {trailing_blank}")
    return 60.0, False, evidence, blank


# ---------------------------------------------------------------------------
# C. Region layout (section order + vertical region + column consistency)
# ---------------------------------------------------------------------------

def _score_region_layout(
    orig_extracted, gen_extracted
) -> tuple[float, bool, list[str]]:
    """Combined section-order / vertical-region / column-consistency score.

    Hard fail when original had multi-column layout but generated is single-column.
    """
    evidence: list[str] = []
    hard_fail = False
    col_penalty = 0.0

    # Column consistency
    orig_cols = orig_extracted.features.column_count_estimate
    gen_cols = gen_extracted.features.column_count_estimate
    if orig_cols >= 2 and gen_cols < 2:
        hard_fail = True
        col_penalty = 35.0
        evidence.append(
            f"Column layout lost: {orig_cols}-col template became 1-col output -- HARD FAIL"
        )

    expected_raw: list[str] = orig_extracted.headings
    actual_raw: list[str] = gen_extracted.headings

    if not expected_raw:
        # Nothing to compare — give benefit of the doubt
        base_score = 100.0
    else:
        expected = [_canonical_section(h) for h in expected_raw]
        actual = [_canonical_section(h) for h in actual_raw]

        # Presence + order
        found_positions: list[int] = []
        found_count = 0
        for exp in expected:
            matched_idx = next(
                (i for i, act in enumerate(actual) if exp == act or exp in act or act in exp),
                None,
            )
            if matched_idx is not None:
                found_count += 1
                found_positions.append(matched_idx)

        presence = found_count / len(expected)

        order_score = 1.0
        if len(found_positions) > 1:
            inversions = sum(
                1
                for i in range(len(found_positions) - 1)
                if found_positions[i] > found_positions[i + 1]
            )
            order_score = 1.0 - inversions / (len(found_positions) - 1)

        presence_order = 0.65 * presence + 0.35 * order_score

        # Vertical region consistency
        orig_height = orig_extracted.pages[0].height if orig_extracted.pages else 792.0
        gen_height = gen_extracted.pages[0].height if gen_extracted.pages else 792.0
        orig_map = _heading_region_map(orig_extracted, orig_height)
        gen_map = _heading_region_map(gen_extracted, gen_height)

        _region_order = ["top", "middle", "bottom"]
        region_matched = 0.0
        region_total = 0

        for heading, orig_region in orig_map.items():
            best_gen = next(
                (gh for gh in gen_map if heading == gh or heading in gh or gh in heading),
                None,
            )
            if best_gen is None:
                continue
            region_total += 1
            gen_region = gen_map[best_gen]
            if orig_region == gen_region:
                region_matched += 1.0
            else:
                dist = abs(_region_order.index(orig_region) - _region_order.index(gen_region))
                if dist == 1:
                    region_matched += 0.5
                    evidence.append(f"'{heading}' region: {orig_region}->{gen_region} (adjacent)")
                else:
                    evidence.append(f"'{heading}' region: {orig_region}->{gen_region} (major shift)")

        region_frac = (region_matched / region_total) if region_total > 0 else 0.85

        # 55% section order/presence, 45% vertical region
        base_score = (0.55 * presence_order + 0.45 * region_frac) * 100.0

        if base_score < 65 and expected_raw:
            evidence.append(
                f"Section layout {base_score:.0f}: expected {expected_raw[:4]}, "
                f"found {found_count}/{len(expected)}"
            )

    score = max(0.0, base_score - col_penalty)
    return score, hard_fail, evidence


# ---------------------------------------------------------------------------
# D. Container / overflow
# ---------------------------------------------------------------------------

def _score_container(
    orig_extracted, gen_extracted
) -> tuple[float, list[str]]:
    """Detect content area expansion that indicates overflow into wrong containers.

    Compares per-page content bounding-box heights.  A page where the generated
    content area is > 2.5x the original's is a probable overflow.
    """
    evidence: list[str] = []
    score = 100.0

    n_pages = min(len(orig_extracted.pages), len(gen_extracted.pages))
    if n_pages == 0:
        return 80.0, evidence

    for i in range(n_pages):
        orig_page = orig_extracted.pages[i]
        gen_page = gen_extracted.pages[i]

        if not orig_page.content_bbox or not gen_page.content_bbox:
            continue

        orig_h = max(1.0, orig_page.content_bbox[3] - orig_page.content_bbox[1])
        gen_h = max(1.0, gen_page.content_bbox[3] - gen_page.content_bbox[1])

        ratio = gen_h / orig_h
        if ratio > 2.5:
            penalty = min(30.0, (ratio - 2.5) * 10.0 + 15.0)
            score -= penalty
            evidence.append(
                f"Page {i + 1}: content expanded {ratio:.1f}x (possible container overflow)"
            )
        elif ratio > 1.8:
            score -= 10.0
            evidence.append(
                f"Page {i + 1}: content expanded {ratio:.1f}x (moderate expansion)"
            )

    return max(0.0, score), evidence


# ---------------------------------------------------------------------------
# E. Density from IR
# ---------------------------------------------------------------------------

def _compute_density_score_from_ir(ir: dict) -> "tuple[float, bool, list[str]]":
    """Estimate content density from the generated IR.

    Returns (score, hard_fail, evidence_list).

    hard_fail is True when ALL experience roles have no bullets and there are
    at least 2 roles — this indicates the experience section content was entirely
    lost during rendering (every role is an empty shell).
    """
    evidence: list[str] = []
    sections = ir.get("sections", [])
    if not sections:
        return 80.0, False, evidence

    exp_sections = [s for s in sections if s.get("semantic_type") == "experience"]

    total_roles = sum(len(s.get("roles", [])) for s in exp_sections)
    empty_roles = sum(
        1
        for s in exp_sections
        for role in s.get("roles", [])
        if not role.get("bullets")
    )
    dense_roles = sum(
        1
        for s in exp_sections
        for role in s.get("roles", [])
        if len(role.get("bullets", [])) > 12
    )

    score = 100.0
    hard_fail = False

    if total_roles > 0:
        empty_ratio = empty_roles / total_roles
        # HARD FAIL when every experience role has no bullets (≥2 roles) — all
        # experience content was lost during rendering.
        if empty_ratio == 1.0 and total_roles >= 2:
            hard_fail = True
            evidence.append(
                f"Density: {empty_roles}/{total_roles} roles have no bullets -- HARD FAIL: "
                f"all experience roles are empty (complete content loss)"
            )
            score -= 35.0
        elif empty_ratio > 0.50:
            score -= 35.0
            evidence.append(f"Density: {empty_roles}/{total_roles} roles have no bullets")
        elif empty_ratio > 0.25:
            score -= 15.0
            evidence.append(f"Density: {empty_roles}/{total_roles} roles have no bullets")

        if dense_roles > 0:
            score -= min(20.0, dense_roles * 7.0)
            evidence.append(
                f"Density: {dense_roles} role(s) with >12 bullets (possible overflow)"
            )

    total_content_paras = sum(
        len(s.get("body_paras", []))
        + sum(
            len(r.get("bullets", [])) + len(r.get("meta_lines", []))
            for r in s.get("roles", [])
        )
        for s in sections
    )
    avg = total_content_paras / len(sections)
    if avg < 2.0:
        score -= 20.0
        evidence.append(f"Low content density: {avg:.1f} paras/section average")

    return max(0.0, score), hard_fail, evidence


# ---------------------------------------------------------------------------
# F. Sparse continuation page detection
# ---------------------------------------------------------------------------

def _compute_effective_area_metrics(page) -> "tuple[float, float, float, int, int]":
    """Compute effective visual-utilisation metrics for a single page.

    Returns (area_ratio, vert_fill, bottom_empty, meaningful_block_count,
             meaningful_line_count).

    area_ratio
        Sum of meaningful block bounding-box areas divided by total page area.
        Unlike fill_fraction (first-to-last block span), this captures how much
        of the page is actually covered by text — blocks that are small or
        widely spaced can span 60% of the vertical height while covering only
        20% of the page area.

    vert_fill
        (last_block_bottom − first_block_top) / page_height.  Matches the
        intuitive "content reaches this far down the page" measure.

    bottom_empty
        (page_height − last_block_bottom) / page_height.  Fraction of the page
        below the last content block — the blank region the user sees at the
        bottom.

    meaningful_block_count / meaningful_line_count
        Count of blocks / lines with at least _SPARSE_MIN_BLOCK_CHARS chars.
    """
    page_area = page.height * page.width
    if page_area <= 0:
        return 0.0, 0.0, 1.0, 0, 0

    meaningful = [
        b for b in page.blocks
        if sum(len(ln.text) for ln in b.lines) >= _SPARSE_MIN_BLOCK_CHARS
    ]
    if not meaningful:
        return 0.0, 0.0, 1.0, 0, 0

    total_area = sum(
        (b.bbox[3] - b.bbox[1]) * (b.bbox[2] - b.bbox[0])
        for b in meaningful
    )
    top_y = min(b.bbox[1] for b in meaningful)
    bot_y = max(b.bbox[3] for b in meaningful)

    area_ratio = total_area / page_area
    vert_fill = (bot_y - top_y) / page.height if page.height > 0 else 0.0
    bottom_empty = (page.height - bot_y) / page.height if page.height > 0 else 1.0
    m_lines = sum(len(b.lines) for b in meaningful)

    return area_ratio, vert_fill, bottom_empty, len(meaningful), m_lines


def _find_sparse_continuation_pages(
    gen_extracted,
) -> "list[tuple[int, float, float, float, int, int, str]]":
    """Return non-first pages that are sparsely filled after a well-packed predecessor.

    Each entry: (page_number, fill_fraction, bottom_empty_fraction,
                 area_ratio, line_count, block_count, nearest_section_name).

    fill_fraction   — vertical span of content / page height (coarse screen)
    area_ratio      — sum of block areas / page area (accurate sparseness signal)

    Detection criteria (all must hold):
    - page_number > 1  (not the first page)
    - line_count ≥ _SPARSE_MIN_LINES  (page has real content, not a trivial tail)
    - not already caught as blank  (standard blank-page thresholds apply first)
    - fill_fraction < _SPARSE_FILL_THRESHOLD  (large empty area at bottom)
    - previous page fill ≥ _SPARSE_PREV_PAGE_MIN_FILL  (content was pushed here
      by a forced break; short resumes with naturally sparse last pages are OK)
    """
    results = []
    pages = gen_extracted.pages

    for i, page in enumerate(pages):
        if page.page_number == 1:
            continue

        n_lines = len(page.lines)
        if n_lines < _SPARSE_MIN_LINES:
            continue  # trivial tail overflow — not a forced-break artifact

        # Skip pages already classified as blank
        total_text = " ".join(ln.text for ln in page.lines).strip()
        if n_lines < _BLANK_LINE_THRESHOLD and len(total_text) < _BLANK_CHAR_THRESHOLD:
            continue

        if not page.content_bbox:
            continue

        page_h = page.height
        if page_h <= 0:
            continue

        content_top = page.content_bbox[1]
        content_bottom = page.content_bbox[3]
        fill_frac = (content_bottom - content_top) / page_h
        if fill_frac >= _SPARSE_FILL_THRESHOLD:
            continue  # page is adequately filled (coarse gate)

        # Require the previous page to be well-filled so that we only flag
        # pages where content was genuinely pushed by a forced break.
        if i == 0:
            continue
        prev_page = pages[i - 1]
        if not prev_page.content_bbox or prev_page.height <= 0:
            continue
        prev_h = prev_page.height
        prev_fill = (prev_page.content_bbox[3] - prev_page.content_bbox[1]) / prev_h
        if prev_fill < _SPARSE_PREV_PAGE_MIN_FILL:
            continue  # previous page wasn't full — short document; sparse is OK

        # Compute accurate effective-area metrics for reporting and hard-fail.
        area_ratio, _, bottom_empty, m_blocks, m_lines = _compute_effective_area_metrics(page)

        # Identify the nearest section heading on this sparse page for the report.
        nearest_section = "unknown"
        for blk in page.blocks:
            if blk.block_type in ("heading", "header"):
                for ln in blk.lines:
                    text = ln.text.strip()
                    if text:
                        nearest_section = _canonical_section(text) or text[:40]
                        break
                if nearest_section != "unknown":
                    break

        results.append((
            page.page_number,
            fill_frac,
            bottom_empty,
            area_ratio,
            n_lines,
            len(page.blocks),
            nearest_section,
        ))

    return results


def _compute_sparse_page_score(
    gen_extracted,
) -> "tuple[float, bool, list[str]]":
    """Compute a 0–100 sparse-continuation-page score for the generated document.

    Returns (score, hard_fail, evidence_list).

    score     = 100 when no qualifying sparse page is found; 0 otherwise.
    hard_fail = True when at least one sparse page has effective_area_ratio
                below _SPARSE_HARD_FAIL_AREA_RATIO (< 25% of page area is
                covered by content blocks) — the page is visually mostly empty.

    The area_ratio threshold is stricter than the vertical fill threshold:
    a page can span 60% of the page height (fill_fraction) but cover only
    22% of the page area when the text blocks are small and widely spaced.

    Evidence wording uses ``1 − area_ratio`` as "visual emptiness" so that the
    two numbers in the evidence string are complementary (sum ≈ 100%) and
    unambiguous to the reader.  The raw ``bottom_empty`` fraction (trailing gap
    below the last block / page height) is NOT included in evidence because it
    measures a geometrically different quantity and would appear contradictory
    alongside area_ratio (e.g. "23% covered, but only 33% empty at bottom").
    """
    sparse_pages = _find_sparse_continuation_pages(gen_extracted)
    evidence: list[str] = []
    hard_fail = False

    if not sparse_pages:
        return 100.0, False, evidence

    for page_num, fill_frac, bottom_empty, area_ratio, n_lines, n_blocks, nearest_sec in sparse_pages:
        area_pct = area_ratio * 100
        # visual_empty = complement of area_ratio: fraction of page NOT covered by
        # text blocks (margins + inter-block gaps + trailing whitespace combined).
        # This is internally consistent with area_pct: area_pct + visual_empty ≈ 100%.
        # NOTE: bottom_empty (trailing gap below last block / page height) is a
        # geometrically different metric and is NOT reported here to avoid the
        # contradictory appearance of "23% occupied vs. only 33% empty".
        visual_empty_pct = (1.0 - area_ratio) * 100
        is_hard = area_ratio < _SPARSE_HARD_FAIL_AREA_RATIO

        if is_hard:
            hard_fail = True
            sev = " -- HARD FAIL: page is mostly empty after content reflow"
        else:
            sev = ""

        ev = (
            f"Sparse continuation page{' (HARD FAIL)' if is_hard else ''}: "
            f"page {page_num} — text covers {area_pct:.0f}% of page "
            f"(~{visual_empty_pct:.0f}% visually empty), {n_lines} lines, {n_blocks} blocks"
        )
        if nearest_sec and nearest_sec != "unknown":
            ev += f"; nearest section '{nearest_sec}'"
        if sev:
            ev += sev
        evidence.append(ev)

    return 0.0, hard_fail, evidence


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# G. Sparse first page (content pushed entirely to page 2+)
# ---------------------------------------------------------------------------

# Area-ratio threshold below which page 1 is considered "critically sparse."
# When page 1 is this empty while page 2 has real content, the rendering engine
# placed virtually nothing on page 1 — almost always a table-overflow or section-
# break artefact that produces an essentially blank first page followed by a dense
# continuation.
_SPARSE_FIRST_PAGE_AREA_THRESHOLD = 0.08   # page 1 text blocks cover < 8% of area
_SPARSE_FIRST_PAGE_P2_MIN_AREA   = 0.10   # page 2 must have ≥ 10% area (real content)


def _detect_sparse_first_page(
    gen_extracted,
) -> "tuple[float, bool, list[str]]":
    """Detect when page 1 is critically sparse while subsequent pages carry content.

    Returns (score, hard_fail, evidence_list).

    score = 0 when triggered, 100 otherwise.
    hard_fail = True when page 1 has < 8% area coverage and ≥ 1 subsequent page
    exists with ≥ 10% area (demonstrating that content was pushed past page 1).

    This catches rendering artefacts such as:
    - A table cell expanding enormously on page 1, leaving it mostly empty while
      the remaining resume content flows to page 2.
    - A section/page-break injected too early, creating an almost-blank first page.
    """
    if len(gen_extracted.pages) < 2:
        return 100.0, False, []

    page1 = gen_extracted.pages[0]
    ar1, _, _, _, ml1 = _compute_effective_area_metrics(page1)

    if ar1 >= _SPARSE_FIRST_PAGE_AREA_THRESHOLD:
        return 100.0, False, []

    # Confirm that subsequent pages carry real content (not all blank)
    max_p2_area = max(
        (_compute_effective_area_metrics(p)[0] for p in gen_extracted.pages[1:]),
        default=0.0,
    )
    if max_p2_area < _SPARSE_FIRST_PAGE_P2_MIN_AREA:
        return 100.0, False, []

    visual_empty_pct = (1.0 - ar1) * 100
    ev = (
        f"Page 1 critically sparse (HARD FAIL): text covers {ar1*100:.0f}% of page "
        f"(~{visual_empty_pct:.0f}% visually empty), {ml1} lines — "
        f"content displaced to continuation page(s)"
    )
    return 0.0, True, [ev]


# ---------------------------------------------------------------------------
# H. Cross-page column-continuity break
# ---------------------------------------------------------------------------

# Minimum x-shift (as fraction of page width) that indicates a column jump.
_COL_JUMP_X_SHIFT_THRESHOLD = 0.25


def _detect_column_jump(
    gen_extracted,
) -> "tuple[list[str], bool]":
    """Detect when content continues on page 2 in a different column lane than page 1.

    Returns (evidence_list, hard_fail).

    Examines blocks in the lower half of page 1 (the likely end of column content)
    and blocks in the upper half of page 2 (the continuation).  If the median
    x-centre of page-1 tail blocks differs from the median x-centre of page-2 head
    blocks by ≥ 25% of the page width, a column-continuity break is flagged.

    Only fires when both page 1 and page 2 have at least 3 qualifying blocks.
    """
    if len(gen_extracted.pages) < 2:
        return [], False

    page1, page2 = gen_extracted.pages[0], gen_extracted.pages[1]
    page_w = page1.width or 595.0
    page_h1 = page1.height or 842.0
    page_h2 = page2.height or 842.0

    def _x_centers(page, y_min_frac, y_max_frac) -> list[float]:
        h = page.height or 842.0
        return [
            (b.bbox[0] + b.bbox[2]) / 2.0
            for b in page.blocks
            if (y_min_frac * h <= b.bbox[1] <= y_max_frac * h
                and sum(len(ln.text) for ln in b.lines) >= _SPARSE_MIN_BLOCK_CHARS)
        ]

    # Page 1 lower half; page 2 upper half
    p1_centers = _x_centers(page1, 0.5, 1.0)
    p2_centers = _x_centers(page2, 0.0, 0.5)

    if len(p1_centers) < 3 or len(p2_centers) < 3:
        return [], False

    p1_centers.sort()
    p2_centers.sort()

    def _median(lst):
        n = len(lst)
        return lst[n // 2] if n % 2 else (lst[n // 2 - 1] + lst[n // 2]) / 2

    med1 = _median(p1_centers)
    med2 = _median(p2_centers)
    shift_frac = abs(med2 - med1) / page_w

    if shift_frac < _COL_JUMP_X_SHIFT_THRESHOLD:
        return [], False

    # Require a DEFINITIVE side change: content must move from one side of the
    # page to the other (left↔right).  Use inner margins of 40/60% to avoid
    # flagging content that merely drifts within the same general column area.
    def _definitive_side(x: float) -> str:
        if x < page_w * 0.40:
            return "left"
        if x > page_w * 0.60:
            return "right"
        return "center"   # ambiguous — not a clear column

    side1 = _definitive_side(med1)
    side2 = _definitive_side(med2)

    if side1 == "center" or side2 == "center" or side1 == side2:
        return [], False   # no definitive column change

    ev = (
        f"Column-continuity break (HARD FAIL): page 1 body content centred at "
        f"x={med1:.0f} ({side1} column), page 2 continuation at x={med2:.0f} "
        f"({side2} column) — x-shift={shift_frac:.0%} of page width"
    )
    return [ev], True


# ---------------------------------------------------------------------------
# I. Per-page column-count drop on overflow pages
# ---------------------------------------------------------------------------

# Fraction of page width used as the inner margin of the "definitive left/right
# column" zones when estimating per-page column count.  Blocks whose x-centre
# is < page_w * _COL_ZONE_LEFT are definitively in the left column; those with
# x-centre > page_w * _COL_ZONE_RIGHT are definitively in the right column.
_COL_ZONE_LEFT  = 0.35   # x-centre < 35% → left column
_COL_ZONE_RIGHT = 0.65   # x-centre > 65% → right column
_COL_MIN_BLOCKS = 2      # need ≥2 blocks in each zone to confirm 2-col layout


def _estimate_page_column_count(page) -> int:
    """Estimate the number of distinct content columns on a single PDF page.

    Returns 2 when there are ≥ _COL_MIN_BLOCKS meaningful text blocks clearly
    in both the left zone (x-centre < 35% of page width) AND the right zone
    (x-centre > 65% of page width).  Returns 1 otherwise.

    This is intentionally conservative: only flag 2 columns when both sides
    have definitive representation, to avoid calling a centered or full-width
    layout "2-column."
    """
    page_w = page.width or 595.0
    left_threshold  = page_w * _COL_ZONE_LEFT
    right_threshold = page_w * _COL_ZONE_RIGHT

    left_count = 0
    right_count = 0
    for b in page.blocks:
        if sum(len(ln.text) for ln in b.lines) < _SPARSE_MIN_BLOCK_CHARS:
            continue
        x_centre = (b.bbox[0] + b.bbox[2]) / 2.0
        if x_centre < left_threshold:
            left_count += 1
        elif x_centre > right_threshold:
            right_count += 1

    return 2 if (left_count >= _COL_MIN_BLOCKS and right_count >= _COL_MIN_BLOCKS) else 1


def _detect_overflow_column_loss(
    gen_extracted,
    orig_extracted,
) -> "tuple[list[str], bool]":
    """Detect when page 2+ overflow pages lose the multi-column layout of page 1.

    Returns (evidence_list, hard_fail).

    Fires when:
    - The original template has ≥ 2 columns (PDF-level estimate), AND
    - Generated page 1 also shows ≥ 2 column zones (confirming multi-column
      content was placed correctly), AND
    - Any subsequent page drops to a single-column layout.

    This captures the common template defect where a 2-column first page
    (sidebar + main content) overflows into a second page that has no sidebar
    — the continuation page uses a single-column layout, breaking the reading
    topology that the template established on page 1.
    """
    if len(gen_extracted.pages) < 2:
        return [], False

    orig_cols_est = orig_extracted.features.column_count_estimate if orig_extracted else 1
    if orig_cols_est < 2:
        return [], False   # single-column template — no column topology to break

    gen_page1_cols = _estimate_page_column_count(gen_extracted.pages[0])
    if gen_page1_cols < 2:
        return [], False   # page 1 itself is single-column → no loss

    evidence: list[str] = []
    hard_fail = False
    for page in gen_extracted.pages[1:]:
        pg_cols = _estimate_page_column_count(page)
        if pg_cols < 2:
            hard_fail = True
            evidence.append(
                f"Column topology break on page {page.page_number} (HARD FAIL): "
                f"template and page 1 use {gen_page1_cols}-column layout but "
                f"page {page.page_number} has {pg_cols}-column layout — "
                f"Experience/content section changed column lane on overflow page"
            )
            break   # report first occurrence only

    return evidence, hard_fail


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class PDFVisualResult:
    page_count_score: float
    blank_page_score: float
    region_score: float
    container_score: float
    density_score: float
    sparse_page_score: float = 100.0  # 0 when sparse continuation page detected

    original_pages: int = 0
    generated_pages: int = 0
    orig_columns: int = 1
    gen_columns: int = 1
    blank_pages: list[int] = field(default_factory=list)

    hard_fail: bool = False
    hard_fail_reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


def score_pdf_visual(
    original_pdf_path: str,
    generated_pdf_path: str,
    ir_dict: dict | None = None,
) -> PDFVisualResult:
    """Score visual layout preservation between a template PDF and generated PDF."""
    from tailor.eval.extractor import extract

    orig = extract(original_pdf_path)
    gen = extract(generated_pdf_path)

    hard_fail = False
    hard_fail_reasons: list[str] = []
    evidence: list[str] = []

    orig_pages = len(orig.pages)
    gen_pages = len(gen.pages)

    pc_score, pc_fail, pc_ev = _compute_page_count_score(orig_pages, gen_pages)
    if pc_fail:
        hard_fail = True
        hard_fail_reasons.append("PAGE_COUNT_OVERFLOW")
    evidence.extend(pc_ev)

    bp_score, bp_fail, bp_ev, blank_pages = _compute_blank_page_score(
        gen, orig_pages=orig_pages, orig_extracted=orig
    )
    if bp_fail:
        hard_fail = True
        hard_fail_reasons.append(
            "BLANK_PAGE_CONTENT_LOSS"
            if any("HARD FAIL" in e and "page count" in e for e in bp_ev)
            else "BLANK_MIDDLE_PAGE"
        )
    evidence.extend(bp_ev)

    region_s, region_fail, region_ev = _score_region_layout(orig, gen)
    if region_fail:
        hard_fail = True
        hard_fail_reasons.append("COLUMN_LAYOUT_LOST")
    evidence.extend(region_ev)

    container_s, container_ev = _score_container(orig, gen)
    evidence.extend(container_ev)

    if ir_dict:
        d_score, d_fail, d_ev = _compute_density_score_from_ir(ir_dict)
        if d_fail:
            hard_fail = True
            hard_fail_reasons.append("ALL_EXPERIENCE_ROLES_EMPTY")
        evidence.extend(d_ev)
    else:
        d_score = 80.0

    sparse_s, sparse_fail, sparse_ev = _compute_sparse_page_score(gen)
    if sparse_fail:
        hard_fail = True
        hard_fail_reasons.append("SPARSE_CONTINUATION_PAGE")
    evidence.extend(sparse_ev)

    # G. Sparse first page (content pushed to continuation pages)
    sfp_score, sfp_fail, sfp_ev = _detect_sparse_first_page(gen)
    if sfp_fail:
        hard_fail = True
        hard_fail_reasons.append("SPARSE_FIRST_PAGE")
    evidence.extend(sfp_ev)

    # H. Cross-page column-continuity break (block x-centre shift)
    col_jump_ev, col_jump_fail = _detect_column_jump(gen)
    if col_jump_fail:
        hard_fail = True
        hard_fail_reasons.append("COLUMN_CONTINUITY_BREAK")
    evidence.extend(col_jump_ev)

    # I. Overflow page column loss (page 1 multi-col → page 2+ single-col)
    ocl_ev, ocl_fail = _detect_overflow_column_loss(gen, orig)
    if ocl_fail:
        hard_fail = True
        hard_fail_reasons.append("OVERFLOW_COLUMN_LOSS")
    evidence.extend(ocl_ev)

    return PDFVisualResult(
        page_count_score=pc_score,
        blank_page_score=bp_score,
        region_score=region_s,
        container_score=container_s,
        density_score=d_score,
        sparse_page_score=sparse_s,
        original_pages=orig_pages,
        generated_pages=gen_pages,
        orig_columns=orig.features.column_count_estimate,
        gen_columns=gen.features.column_count_estimate,
        blank_pages=blank_pages,
        hard_fail=hard_fail,
        hard_fail_reasons=hard_fail_reasons,
        evidence=evidence,
    )
