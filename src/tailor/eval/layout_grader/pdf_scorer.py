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
_SPARSE_FILL_THRESHOLD = 0.60         # content fills < 60% of page height
_SPARSE_PREV_PAGE_MIN_FILL = 0.75     # previous page must be ≥ 75% full
_SPARSE_MIN_LINES = 18                # page must have ≥ 18 content lines

# Hard fail escalation within detected sparse pages.
# Uses effective_area_ratio (sum of block bounding-box areas / page area) which
# captures actual text density independent of how spread-out the blocks are.
# A fill_fraction (vertical span) of 60% can hide a page that is visually 75-80%
# empty when the blocks are small or spaced far apart.
_SPARSE_HARD_FAIL_AREA_RATIO = 0.25   # block areas < 25% of page area → hard fail

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


def _find_blank_pages(extracted) -> list[int]:
    blank = []
    for page in extracted.pages:
        total_text = " ".join(line.text for line in page.lines).strip()
        if len(page.lines) < _BLANK_LINE_THRESHOLD and len(total_text) < _BLANK_CHAR_THRESHOLD:
            blank.append(page.page_number)
    return blank


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
) -> tuple[float, bool, list[str], list[int]]:
    blank = _find_blank_pages(gen_extracted)
    evidence: list[str] = []
    if not blank:
        return 100.0, False, evidence, blank

    last_page = gen_extracted.pages[-1].page_number if gen_extracted.pages else 0
    middle_blank = [p for p in blank if p < last_page]

    if middle_blank:
        evidence.append(f"Blank middle page(s) {middle_blank} -- HARD FAIL")
        return 0.0, True, evidence, blank

    evidence.append(f"Blank trailing page(s) {blank}")
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

def _compute_density_score_from_ir(ir: dict) -> tuple[float, list[str]]:
    """Estimate content density from the generated IR."""
    evidence: list[str] = []
    sections = ir.get("sections", [])
    if not sections:
        return 80.0, evidence

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

    if total_roles > 0:
        empty_ratio = empty_roles / total_roles
        if empty_ratio > 0.50:
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

    return max(0.0, score), evidence


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

    bp_score, bp_fail, bp_ev, blank_pages = _compute_blank_page_score(gen)
    if bp_fail:
        hard_fail = True
        hard_fail_reasons.append("BLANK_MIDDLE_PAGE")
    evidence.extend(bp_ev)

    region_s, region_fail, region_ev = _score_region_layout(orig, gen)
    if region_fail:
        hard_fail = True
        hard_fail_reasons.append("COLUMN_LAYOUT_LOST")
    evidence.extend(region_ev)

    container_s, container_ev = _score_container(orig, gen)
    evidence.extend(container_ev)

    if ir_dict:
        d_score, d_ev = _compute_density_score_from_ir(ir_dict)
        evidence.extend(d_ev)
    else:
        d_score = 80.0

    sparse_s, sparse_fail, sparse_ev = _compute_sparse_page_score(gen)
    if sparse_fail:
        hard_fail = True
        hard_fail_reasons.append("SPARSE_CONTINUATION_PAGE")
    evidence.extend(sparse_ev)

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
