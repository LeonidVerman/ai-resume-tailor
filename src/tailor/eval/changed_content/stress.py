"""Container stress / overflow-fit scoring for changed-content evaluation.

Measures whether generated content physically fits the visual container
defined by the source template.  A high stress score signals that the
section is likely too large for its container — especially dangerous in
sidebar / narrow-region sections.

Reuse inventory
---------------
- extract_section_anchors()   placement.py   unchanged
- classify_region()           placement.py   unchanged
- ExtractedDoc / PageModel    tailor.eval.models  unchanged

A local _extract_section_data() is introduced (not reusing
coherence._extract_section_content) because stress analysis needs both
the text lines *and* the normalized vertical height of each section.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tailor.eval.models import ExtractedDoc
from tailor.eval.changed_content.placement import (
    SectionAnchor,
    classify_region,
    extract_section_anchors,
)


# ---------------------------------------------------------------------------
# Per-section configuration
# ---------------------------------------------------------------------------

# char_thr    — char_growth_ratio above this starts incurring a penalty
# height_thr  — height_growth_ratio above this starts incurring a penalty
# sensitivity — "high" | "medium" | "low" — affects weight distribution

_SECTION_CONFIG: dict[str, dict] = {
    "summary":        {"char_thr": 1.5,  "height_thr": 1.3,  "sensitivity": "high"},
    "skills":         {"char_thr": 1.8,  "height_thr": 1.8,  "sensitivity": "high"},
    "languages":      {"char_thr": 1.4,  "height_thr": 1.4,  "sensitivity": "high"},
    "certifications": {"char_thr": 1.5,  "height_thr": 1.5,  "sensitivity": "medium"},
    "education":      {"char_thr": 2.0,  "height_thr": 2.0,  "sensitivity": "medium"},
    "experience":     {"char_thr": 2.5,  "height_thr": 2.5,  "sensitivity": "low"},
}

_DEFAULT_CONFIG = {"char_thr": 2.0, "height_thr": 2.0, "sensitivity": "medium"}

# Narrow containers stress more easily than wide ones
_REGION_MULTIPLIER: dict[str, float] = {
    "main":          1.0,
    "sidebar_left":  1.4,
    "sidebar_right": 1.4,
    "unknown":       1.1,
}

# Stress level boundaries
_STRESS_MEDIUM = 0.30
_STRESS_HIGH   = 0.65

# Chars-per-normalized-height density thresholds.
# _DENSITY_BASELINE — below this, no density penalty.
# _DENSITY_SCALE    — adding this many chars/unit above baseline → full penalty.
_DENSITY_BASELINE = 1500.0
_DENSITY_SCALE    = 4000.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SectionStressResult:
    """Container stress analysis for one section."""

    canonical_type: str
    region: str
    input_found: bool
    output_found: bool
    input_char_count: int
    output_char_count: int
    input_line_count: int
    output_line_count: int
    input_height: float            # normalized page-height units
    output_height: float
    char_growth_ratio: float       # output_chars / input_chars  (1.0 when no input)
    line_growth_ratio: float       # output_lines / input_lines  (1.0 when no input)
    height_growth_ratio: float     # output_height / input_height (1.0 when no input)
    text_density_score: float      # 0–1 density proxy (chars/height, normalized)
    stress_score: float            # 0–1 composite stress
    stress_level: str              # "low" | "medium" | "high"
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Section content + height extraction
# ---------------------------------------------------------------------------

def _extract_section_data(
    doc: ExtractedDoc,
    anchor: SectionAnchor,
    next_anchor: SectionAnchor | None,
) -> tuple[list[str], float]:
    """Collect text lines and compute normalized vertical height for a section.

    Height is the accumulated normalized vertical extent of blocks belonging
    to this section, summed across pages (one full page = 1.0).
    Region filtering mirrors coherence._extract_section_content.

    Returns (lines, height_in_normalized_units).
    """
    lines: list[str] = []
    per_page: dict[int, list[tuple[float, float]]] = {}  # page → [(ny0, ny1), ...]

    for page in doc.pages:
        pn = page.page_number
        if pn < anchor.page:
            continue
        if next_anchor and pn > next_anchor.page:
            break

        pw, ph = page.width, page.height
        if pw <= 0 or ph <= 0:
            continue

        for block in page.blocks:
            x0, y0, x1, y1 = block.bbox
            ny0 = y0 / ph
            nx0 = x0 / pw
            nx1 = x1 / pw
            ny1 = y1 / ph

            # Skip blocks at or above the heading on the heading's own page
            if pn == anchor.page and ny0 <= anchor.ny0:
                continue
            # Skip blocks at or beyond the next anchor on its page
            if next_anchor and pn == next_anchor.page and ny0 >= next_anchor.ny0:
                continue

            # Region filter — identical to coherence module
            block_region = classify_region(nx0, ny0, nx1, ny1)
            if anchor.region == "main":
                if block_region in ("sidebar_left", "sidebar_right"):
                    continue
            else:
                if block_region != anchor.region:
                    continue

            per_page.setdefault(pn, []).append((ny0, ny1))
            for line in block.lines:
                text = line.text.strip()
                if text:
                    lines.append(text)

    if not per_page:
        return lines, 0.0

    pages_sorted = sorted(per_page.keys())
    first_p = pages_sorted[0]
    height = 0.0

    for p in pages_sorted:
        ys = per_page[p]
        all_ny0 = [y[0] for y in ys]
        all_ny1 = [y[1] for y in ys]

        if p == first_p:
            # Start from the bottom edge of the heading block (anchor.ny1)
            start_y = anchor.ny1 if p == anchor.page else min(all_ny0)
        else:
            start_y = min(all_ny0)

        end_y = max(all_ny1)
        height += max(0.0, end_y - start_y)

    return lines, round(height, 4)


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _growth_penalty(ratio: float, threshold: float) -> float:
    """0–1 penalty for *ratio* exceeding *threshold*.

    Reaches 1.0 when ratio = threshold + (threshold − 1), i.e., the same
    distance above threshold as threshold is above the baseline of 1.0.
    """
    if ratio <= threshold:
        return 0.0
    denom = max(0.01, threshold - 1.0)
    return _clamp((ratio - threshold) / denom)


def _stress_level(score: float) -> str:
    if score < _STRESS_MEDIUM:
        return "low"
    if score < _STRESS_HIGH:
        return "medium"
    return "high"


def _compute_section_stress(
    canonical_type: str,
    region: str,
    input_chars: int,
    output_chars: int,
    input_lines: int,
    output_lines: int,
    input_height: float,
    output_height: float,
    input_found: bool,
) -> tuple[float, float, list[str]]:
    """Return (text_density_score, stress_score, notes).

    When *input_found* is False, growth ratios are held at 1.0 (no growth
    evidence) and only the output density signal is used; notes record the
    reduced confidence.
    """
    cfg = _SECTION_CONFIG.get(canonical_type, _DEFAULT_CONFIG)
    char_thr    = cfg["char_thr"]
    height_thr  = cfg["height_thr"]
    sensitivity = cfg["sensitivity"]

    notes: list[str] = []

    # ── Growth ratios ────────────────────────────────────────────────────
    if input_found and input_chars > 0:
        char_ratio = output_chars / max(1, input_chars)
        line_ratio = output_lines / max(1, input_lines)
    else:
        char_ratio = 1.0
        line_ratio = 1.0
        if not input_found:
            notes.append(
                "Input section absent — growth ratios unavailable; density only"
            )

    if input_found and input_height > 1e-6:
        height_ratio = output_height / input_height
    else:
        height_ratio = 1.0

    # ── Growth penalties ─────────────────────────────────────────────────
    char_pen   = _growth_penalty(char_ratio, char_thr)
    line_pen   = _growth_penalty(line_ratio, char_thr)   # same threshold as char
    height_pen = _growth_penalty(height_ratio, height_thr)

    # ── Density penalty (chars per normalized height unit) ───────────────
    chars_per_height = output_chars / output_height if output_height > 1e-6 else 0.0
    text_density_score = _clamp(chars_per_height / (_DENSITY_BASELINE + _DENSITY_SCALE))
    density_pen = _clamp((chars_per_height - _DENSITY_BASELINE) / _DENSITY_SCALE)

    # Long-line penalty in narrow containers (sidebar)
    avg_line_len = output_chars / max(1, output_lines)
    is_sidebar   = region in ("sidebar_left", "sidebar_right")
    line_len_thr = 65 if is_sidebar else 90
    long_line_pen = _clamp((avg_line_len - line_len_thr) / 30.0)

    # ── Weight distribution by sensitivity ───────────────────────────────
    if sensitivity == "high":
        w_char = 0.20; w_line = 0.15; w_height = 0.45; w_density = 0.20
    elif sensitivity == "medium":
        w_char = 0.22; w_line = 0.18; w_height = 0.42; w_density = 0.18
    else:  # low
        w_char = 0.25; w_line = 0.20; w_height = 0.40; w_density = 0.15

    density_combined = max(density_pen, long_line_pen * 0.5)

    stress_raw = (
        char_pen          * w_char
        + line_pen        * w_line
        + height_pen      * w_height
        + density_combined * w_density
    )

    region_mult  = _REGION_MULTIPLIER.get(region, 1.0)
    stress_score = _clamp(stress_raw * region_mult)

    # ── Human-readable notes for significant signals ─────────────────────
    if char_ratio > char_thr:
        notes.append(
            f"Character count grew {char_ratio:.1f}× "
            f"(threshold={char_thr:.1f}×)"
        )
    if height_ratio > height_thr:
        notes.append(
            f"Section height grew {height_ratio:.1f}× "
            f"(threshold={height_thr:.1f}×)"
        )
    if is_sidebar and avg_line_len > line_len_thr:
        notes.append(
            f"Long average line length in sidebar "
            f"({avg_line_len:.0f} chars, threshold={line_len_thr})"
        )

    return round(text_density_score, 3), round(stress_score, 3), notes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_stress(
    src_extracted: ExtractedDoc,
    out_extracted: ExtractedDoc,
) -> tuple[list[SectionStressResult], float]:
    """Compute per-section container stress scores.

    Parameters
    ----------
    src_extracted:  ExtractedDoc for the source/template PDF.
    out_extracted:  ExtractedDoc for the generated output PDF.

    Returns
    -------
    (results, overall_stress_score)
        results              — one SectionStressResult per detected output anchor
        overall_stress_score — mean stress_score across results; 0.0 when no
                               section anchors are detected (no stress signal)
    """
    src_anchors = extract_section_anchors(src_extracted)
    out_anchors = extract_section_anchors(out_extracted)

    if not out_anchors:
        return [], 0.0

    # Collect source section metrics (accumulate per canonical type)
    src_data: dict[str, tuple[int, int, float]] = {}  # type → (chars, lines, height)
    for i, anchor in enumerate(src_anchors):
        next_a = src_anchors[i + 1] if i + 1 < len(src_anchors) else None
        s_lines, s_height = _extract_section_data(src_extracted, anchor, next_a)
        s_chars = sum(len(ln) for ln in s_lines)
        existing = src_data.get(anchor.canonical_type)
        if existing:
            src_data[anchor.canonical_type] = (
                existing[0] + s_chars,
                existing[1] + len(s_lines),
                existing[2] + s_height,
            )
        else:
            src_data[anchor.canonical_type] = (s_chars, len(s_lines), s_height)

    # Score each output section
    results: list[SectionStressResult] = []
    for i, anchor in enumerate(out_anchors):
        next_a = out_anchors[i + 1] if i + 1 < len(out_anchors) else None
        out_lines, out_height = _extract_section_data(out_extracted, anchor, next_a)
        out_chars      = sum(len(ln) for ln in out_lines)
        out_line_count = len(out_lines)

        src = src_data.get(anchor.canonical_type)
        input_found  = src is not None
        src_chars, src_lines_n, src_height = src if src else (0, 0, 0.0)

        char_ratio   = round(out_chars       / max(1,    src_chars),   3) if input_found and src_chars   > 0    else 1.0
        line_ratio   = round(out_line_count  / max(1,    src_lines_n), 3) if input_found and src_lines_n > 0    else 1.0
        height_ratio = round(out_height      / max(1e-6, src_height),  3) if input_found and src_height  > 1e-6 else 1.0

        density_score, stress_score, notes = _compute_section_stress(
            canonical_type=anchor.canonical_type,
            region=anchor.region,
            input_chars=src_chars,
            output_chars=out_chars,
            input_lines=src_lines_n,
            output_lines=out_line_count,
            input_height=src_height,
            output_height=out_height,
            input_found=input_found,
        )

        results.append(SectionStressResult(
            canonical_type=anchor.canonical_type,
            region=anchor.region,
            input_found=input_found,
            output_found=True,
            input_char_count=src_chars,
            output_char_count=out_chars,
            input_line_count=src_lines_n,
            output_line_count=out_line_count,
            input_height=round(src_height, 4),
            output_height=round(out_height, 4),
            char_growth_ratio=char_ratio,
            line_growth_ratio=line_ratio,
            height_growth_ratio=height_ratio,
            text_density_score=density_score,
            stress_score=stress_score,
            stress_level=_stress_level(stress_score),
            notes=notes,
        ))

    overall = sum(r.stress_score for r in results) / len(results) if results else 0.0
    return results, round(overall, 3)
