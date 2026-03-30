"""Compare two ExtractedDoc instances and produce ComparisonResult.

Comparison levels:
  A — Text fidelity (token F1 primary, sequence similarity secondary)
  B — Structural fidelity (heading count/order, bullet count)
  C — Geometric/layout fidelity (indent, spacing, column, bbox)

All layout metrics are reported both as absolute point values (for
debugging) and as content-relative normalized values (for scoring).
"""
from __future__ import annotations

import difflib
import re
from collections import Counter
from statistics import median

from tailor.eval.models import (
    ComparisonResult,
    ExtractedDoc,
    IssueModel,
    LayoutMetrics,
    TextMetrics,
)

# ---------------------------------------------------------------------------
# Pass/fail thresholds (configurable)
# ---------------------------------------------------------------------------
THRESHOLDS: dict[str, float] = {
    "token_f1_min": 0.90,
    "bullet_indent_delta_norm_max": 0.020,
    "section_gap_delta_norm_max": 0.020,
    # content_bbox_shift is informational only (low severity, never fails).
    # Threshold is deliberately loose — systematic page-size differences
    # between source PDF and DOCX template will always produce some shift.
    "content_bbox_shift_norm_informational": 0.060,
    # Wrap divergence alone doesn't fail; needs structural consequence too.
    "wrap_divergence_escalation_min": 3,
}

# Scoring weights for overall_score
_SCORE_WEIGHTS = {
    "text": 0.40,
    "structure": 0.30,
    "layout": 0.30,
}


# ---------------------------------------------------------------------------
# Text comparison helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _token_f1(src: str, out: str) -> tuple[float, float, float]:
    """Return (precision, recall, F1) for token sets."""
    src_counter = Counter(_tokenize(src))
    out_counter = Counter(_tokenize(out))
    common = src_counter & out_counter
    common_count = sum(common.values())
    src_total = sum(src_counter.values())
    out_total = sum(out_counter.values())
    precision = common_count / out_total if out_total else 0.0
    recall = common_count / src_total if src_total else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def _sequence_similarity(src: str, out: str) -> float:
    return difflib.SequenceMatcher(None, src, out).ratio()


# ---------------------------------------------------------------------------
# Heading comparison helpers
# ---------------------------------------------------------------------------

# Known section names (no spaces, lowercase) for comparison filtering.
# Mirrors the set in extractor.py but lives here to keep comparator self-contained.
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


def _heading_key(text: str) -> str:
    """Normalize a heading to a canonical comparison key.

    - Lowercase
    - Strip leading number prefix (e.g. "1.", "2. ", "(3)")
    - Remove all non-alphanumeric characters
    """
    t = text.lower().strip()
    t = re.sub(r"^[\d\.\s\(\)]+", "", t)   # strip leading "1. " / "(2)" etc.
    return re.sub(r"[^a-z0-9]", "", t)


def _is_known_section(text: str) -> bool:
    """Return True when *text* normalises to a known resume section name."""
    return _heading_key(text) in _KNOWN_SECTIONS_NOSPACE


def _match_headings(
    src_headings: list[str],
    out_headings: list[str],
) -> tuple[list[str], list[str]]:
    """Return (missing_from_output, extra_in_output) for KNOWN section headings only.

    Filters both lists to headings that normalise to a recognised resume
    section name before comparing.  This eliminates false positives from
    name lines, role titles, and other bold-but-not-section text that the
    extractor may have detected as heading candidates.
    """
    src_sections = [h for h in src_headings if _is_known_section(h)]
    out_sections = [h for h in out_headings if _is_known_section(h)]

    src_keys = {_heading_key(h) for h in src_sections}
    out_keys = {_heading_key(h) for h in out_sections}

    missing = [h for h in src_sections if _heading_key(h) not in out_keys]
    extra = [h for h in out_sections if _heading_key(h) not in src_keys]
    return missing, extra


# ---------------------------------------------------------------------------
# Layout metric helpers
# ---------------------------------------------------------------------------

def _content_rel_indent(
    abs_left_x: float,
    body_left_x: float,
    page_width: float,
) -> float:
    """Content-relative indent = (line_left - body_left) / page_width."""
    if page_width <= 0:
        return 0.0
    return (abs_left_x - body_left_x) / page_width


def _median_section_gap(doc: ExtractedDoc) -> float | None:
    """Median spacing_before for heading blocks across all pages."""
    gaps = [
        blk.spacing_before
        for pg in doc.pages
        for blk in pg.blocks
        if blk.block_type == "heading" and blk.spacing_before > 0
    ]
    return median(gaps) if gaps else None


def _median_line_gap(doc: ExtractedDoc) -> float | None:
    gaps = [
        blk.spacing_before
        for pg in doc.pages
        for blk in pg.blocks
        if blk.block_type != "heading" and blk.spacing_before > 0
    ]
    return median(gaps) if gaps else None


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _compute_text_score(tm: TextMetrics) -> float:
    # Token F1 is primary; penalise missing headings
    f1_score = _clamp(tm.token_f1)
    heading_penalty = len(tm.missing_headings) * 0.10
    return _clamp(f1_score - heading_penalty)


def _compute_structure_score(tm: TextMetrics) -> float:
    base = 1.0
    base -= len(tm.missing_headings) * 0.15
    bullet_delta = tm.bullet_count_delta
    if bullet_delta > 0:
        base -= min(0.30, bullet_delta * 0.05)
    return _clamp(base)


def _compute_layout_score(lm: LayoutMetrics) -> float:
    score = 1.0

    if not lm.page_count_match:
        score -= 0.30

    indent_delta = lm.bullet_indent_delta_norm
    if indent_delta is not None:
        excess = max(0.0, indent_delta - THRESHOLDS["bullet_indent_delta_norm_max"])
        score -= min(0.25, excess * 10.0)

    gap_delta = lm.section_gap_delta_norm
    if gap_delta is not None:
        excess = max(0.0, gap_delta - THRESHOLDS["section_gap_delta_norm_max"])
        score -= min(0.15, excess * 7.5)

    # content_bbox_shift: informational only — not penalised in score.
    # Systematic page-size differences (source A4 vs template Letter) will
    # always produce some shift and should not reduce the layout score.

    if lm.column_count_source != lm.column_count_output and lm.column_confidence == "high":
        score -= 0.20

    return _clamp(score)


# ---------------------------------------------------------------------------
# Issue generation
# ---------------------------------------------------------------------------

def _generate_issues(
    src: ExtractedDoc,
    out: ExtractedDoc,
    tm: TextMetrics,
    lm: LayoutMetrics,
) -> list[IssueModel]:
    issues: list[IssueModel] = []

    # --- Text issues ---
    if tm.token_f1 < THRESHOLDS["token_f1_min"]:
        issues.append(IssueModel(
            issue_type="missing_text",
            severity="high",
            page=1,
            source_value=round(tm.token_f1, 3),
            description=(
                f"Token F1 is {tm.token_f1:.3f} (threshold {THRESHOLDS['token_f1_min']:.2f}). "
                "Significant text is missing or corrupted in the output."
            ),
        ))

    for h in tm.missing_headings:
        issues.append(IssueModel(
            issue_type="heading_missing",
            severity="high",
            page=1,
            source_value=h,
            description=f"Section heading not found in output: '{h}'",
        ))

    for h in tm.extra_headings:
        issues.append(IssueModel(
            issue_type="heading_extra",
            severity="low",
            page=1,
            output_value=h,
            description=f"Extra heading in output not present in source: '{h}'",
        ))

    if tm.bullet_count_delta > 2:
        issues.append(IssueModel(
            issue_type="bullet_count_mismatch",
            severity="medium",
            page=1,
            source_value=tm.bullet_count_source,
            output_value=tm.bullet_count_output,
            delta=tm.bullet_count_delta,
            description=(
                f"Bullet count differs: source={tm.bullet_count_source}, "
                f"output={tm.bullet_count_output} (delta={tm.bullet_count_delta})."
            ),
        ))

    # --- Layout issues ---
    if not lm.page_count_match:
        issues.append(IssueModel(
            issue_type="page_break_change",
            severity="high",
            page=1,
            source_value=lm.page_count_source,
            output_value=lm.page_count_output,
            delta=abs(lm.page_count_output - lm.page_count_source),
            description=(
                f"Page count changed: source has {lm.page_count_source} page(s), "
                f"output has {lm.page_count_output} page(s)."
            ),
        ))

    if not lm.page_size_match:
        issues.append(IssueModel(
            issue_type="content_bbox_shift",
            severity="low",
            page=1,
            description="Page dimensions differ between source and output.",
        ))

    if lm.bullet_indent_delta_norm is not None:
        if lm.bullet_indent_delta_norm > THRESHOLDS["bullet_indent_delta_norm_max"]:
            issues.append(IssueModel(
                issue_type="bullet_indent_error",
                severity="high",
                page=1,
                source_value=round(lm.bullet_indent_source_pt or 0, 1),
                output_value=round(lm.bullet_indent_output_pt or 0, 1),
                delta=round(lm.bullet_indent_delta_norm, 4),
                description=(
                    f"Bullet indent diverged: source={lm.bullet_indent_source_pt:.1f}pt, "
                    f"output={lm.bullet_indent_output_pt:.1f}pt "
                    f"(normalized delta={lm.bullet_indent_delta_norm:.4f}, "
                    f"threshold={THRESHOLDS['bullet_indent_delta_norm_max']:.3f})."
                ),
            ))

    if lm.section_gap_delta_norm is not None:
        if lm.section_gap_delta_norm > THRESHOLDS["section_gap_delta_norm_max"]:
            issues.append(IssueModel(
                issue_type="section_spacing_error",
                severity="medium",
                page=1,
                source_value=round(lm.section_gap_source_pt or 0, 1),
                output_value=round(lm.section_gap_output_pt or 0, 1),
                delta=round(lm.section_gap_delta_norm, 4),
                description=(
                    f"Section spacing diverged: source={lm.section_gap_source_pt:.1f}pt, "
                    f"output={lm.section_gap_output_pt:.1f}pt "
                    f"(normalized delta={lm.section_gap_delta_norm:.4f})."
                ),
            ))

    if lm.content_bbox_shift_norm is not None:
        if lm.content_bbox_shift_norm > THRESHOLDS["content_bbox_shift_norm_informational"]:
            issues.append(IssueModel(
                issue_type="content_bbox_shift",
                severity="low",
                page=1,
                delta=round(lm.content_bbox_shift_norm, 4),
                description=(
                    f"Content bounding box shifted (informational). "
                    f"normalized shift={lm.content_bbox_shift_norm:.4f}, "
                    f"threshold={THRESHOLDS['content_bbox_shift_norm_informational']:.3f}."
                ),
            ))

    if (
        lm.column_count_source != lm.column_count_output
        and lm.column_confidence == "high"
    ):
        issues.append(IssueModel(
            issue_type="column_collapse",
            severity="high",
            page=1,
            source_value=lm.column_count_source,
            output_value=lm.column_count_output,
            description=(
                f"Column layout collapsed: source has {lm.column_count_source} column(s), "
                f"output has {lm.column_count_output} column(s). "
                "(High confidence detection.)"
            ),
        ))

    return issues


# ---------------------------------------------------------------------------
# Public comparison entry point
# ---------------------------------------------------------------------------

def compare(src: ExtractedDoc, out: ExtractedDoc) -> ComparisonResult:
    """Compare source and output ExtractedDocs, returning a ComparisonResult."""

    # ── Level A: Text Fidelity ────────────────────────────────────────────
    precision, recall, f1 = _token_f1(src.full_text_normalized, out.full_text_normalized)
    seq_sim = _sequence_similarity(src.full_text_normalized, out.full_text_normalized)
    missing_hdgs, extra_hdgs = _match_headings(src.headings, out.headings)
    bullet_delta = abs(src.bullet_count - out.bullet_count)

    tm = TextMetrics(
        token_precision=round(precision, 4),
        token_recall=round(recall, 4),
        token_f1=round(f1, 4),
        sequence_similarity=round(seq_sim, 4),
        missing_headings=missing_hdgs,
        extra_headings=extra_hdgs,
        bullet_count_source=src.bullet_count,
        bullet_count_output=out.bullet_count,
        bullet_count_delta=bullet_delta,
    )

    # ── Level C: Geometric/Layout Fidelity ───────────────────────────────
    src_page = src.pages[0] if src.pages else None
    out_page = out.pages[0] if out.pages else None

    src_pw = src_page.width if src_page else 612.0
    src_ph = src_page.height if src_page else 792.0
    out_pw = out_page.width if out_page else 612.0
    out_ph = out_page.height if out_page else 792.0

    page_size_match = (
        abs(src_pw - out_pw) < 10 and abs(src_ph - out_ph) < 10
    )

    # Content bbox shift (first page, normalized)
    content_bbox_shift_norm: float | None = None
    if src_page and src_page.content_bbox and out_page and out_page.content_bbox:
        sb = src_page.content_bbox
        ob = out_page.content_bbox
        dx = abs(sb[0] - ob[0]) / src_pw
        dy = abs(sb[1] - ob[1]) / src_ph
        content_bbox_shift_norm = round(max(dx, dy), 4)

    # Bullet indent (content-relative)
    src_body_left = src.features.dominant_body_left_x
    out_body_left = out.features.dominant_body_left_x
    bullet_indent_delta_norm: float | None = None
    src_bullet_pt: float | None = None
    out_bullet_pt: float | None = None

    if src.dominant_bullet_left_x is not None and out.dominant_bullet_left_x is not None:
        src_bullet_pt = src.dominant_bullet_left_x
        out_bullet_pt = out.dominant_bullet_left_x
        src_blt_norm = _content_rel_indent(src_bullet_pt, src_body_left, src_pw)
        out_blt_norm = _content_rel_indent(out_bullet_pt, out_body_left, out_pw)
        bullet_indent_delta_norm = round(abs(src_blt_norm - out_blt_norm), 4)

    # Section gap (content-relative, using page height for normalization)
    src_gap = _median_section_gap(src)
    out_gap = _median_section_gap(out)
    section_gap_delta_norm: float | None = None
    if src_gap is not None and out_gap is not None:
        src_gap_norm = src_gap / src_ph
        out_gap_norm = out_gap / out_ph
        section_gap_delta_norm = round(abs(src_gap_norm - out_gap_norm), 4)

    # Column count
    src_cols = src.features.column_count_estimate
    out_cols = out.features.column_count_estimate
    col_conf = src.features.column_confidence

    lm = LayoutMetrics(
        page_count_match=src.features.page_count == out.features.page_count,
        page_count_source=src.features.page_count,
        page_count_output=out.features.page_count,
        page_size_match=page_size_match,
        content_bbox_shift_norm=content_bbox_shift_norm,
        mean_line_x_shift_pt=None,   # Phase 2
        mean_line_y_shift_pt=None,   # Phase 2
        bullet_indent_delta_norm=bullet_indent_delta_norm,
        bullet_indent_source_pt=src_bullet_pt,
        bullet_indent_output_pt=out_bullet_pt,
        section_gap_delta_norm=section_gap_delta_norm,
        section_gap_source_pt=round(src_gap, 2) if src_gap else None,
        section_gap_output_pt=round(out_gap, 2) if out_gap else None,
        column_count_source=src_cols,
        column_count_output=out_cols,
        column_confidence=col_conf,
    )

    # ── Issues ───────────────────────────────────────────────────────────
    issues = _generate_issues(src, out, tm, lm)

    # ── Scores ───────────────────────────────────────────────────────────
    text_score = _compute_text_score(tm)
    structure_score = _compute_structure_score(tm)
    layout_score = _compute_layout_score(lm)
    overall_score = (
        text_score * _SCORE_WEIGHTS["text"]
        + structure_score * _SCORE_WEIGHTS["structure"]
        + layout_score * _SCORE_WEIGHTS["layout"]
    )

    # ── Pass/fail ─────────────────────────────────────────────────────────
    high_severity = [i for i in issues if i.severity == "high"]
    status = "fail" if high_severity else "pass"

    return ComparisonResult(
        text_metrics=tm,
        layout_metrics=lm,
        issues=issues,
        text_score=round(text_score, 3),
        structure_score=round(structure_score, 3),
        layout_score=round(layout_score, 3),
        overall_score=round(overall_score, 3),
        status=status,
    )
