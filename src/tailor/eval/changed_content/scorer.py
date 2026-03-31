"""Layout preservation scorer for changed-content evaluation.

Unlike the same-text evaluator (which uses token F1 as a primary signal),
this scorer focuses entirely on **layout** metrics because changed content
will naturally produce lower text similarity — that is expected and correct.

Composite score dimensions
--------------------------
topology_preservation  (0-1) — column structure matches source
section_placement      (0-1) — expected headings appear in output
overflow_penalty       (0-1) — page growth penalty
duplication_penalty    (0-1) — stale source content surviving in output
leakage_penalty        (0-1) — internal markers / wrong-section content
style_score            (0-1) — bullet / heading count quality

composite = weighted sum of above (see _WEIGHTS).
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from tailor.eval.models import ExtractedDoc

# Private helpers reused from comparator (owned codebase).
from tailor.eval.comparator import _heading_key, _is_known_section  # noqa: PLC2701

# Section placement module (new, additive — does not touch comparator/extractor).
from tailor.eval.changed_content.placement import (
    SectionPlacementResult,
    score_placement,
)

# ---------------------------------------------------------------------------
# Composite weights
# ---------------------------------------------------------------------------

_WEIGHTS = {
    "topology":    0.25,
    "sections":    0.25,
    "no_overflow": 0.15,
    "no_dup":      0.20,
    "no_leak":     0.05,
    "style":       0.10,
}

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_INTERNAL_MARKER_RE = re.compile(
    r"\{\{|\}\}|CURRENT_DATE|\[TODO\]|\[PLACEHOLDER\]|\[YOUR\s",
    re.IGNORECASE,
)

_SIGNIFICANT_TOKEN_RE = re.compile(r"\b[a-zA-Z]{6,}\b")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LayoutScore:
    # Component scores / penalties (all 0.0 – 1.0)
    topology_preservation: float    # column structure preserved
    section_placement: float        # expected headings found
    overflow_penalty: float         # page growth
    duplication_penalty: float      # stale content in output
    leakage_penalty: float          # internal markers / wrong content
    style_score: float              # bullet / heading rendering quality

    # Raw metrics (for transparency / debugging)
    page_count_delta: int           # out_pages - src_pages
    src_page_count: int
    out_page_count: int
    src_column_count: int
    out_column_count: int
    column_confidence: str          # "high" | "low" | "none"
    section_headings_found: int
    section_headings_expected: int
    stale_token_ratio: float        # raw fraction before penalty scaling
    src_bullet_count: int
    out_bullet_count: int
    gen_bullet_count: int

    # Per-section placement results (empty when no heading geometry available)
    section_placement_results: list  # list[SectionPlacementResult]

    # Weighted composite
    composite: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _significant_tokens(text: str) -> Counter:
    """Counter of significant tokens (length ≥ 6) for duplication detection.

    Length ≥ 6 naturally excludes most common English short words while
    retaining proper nouns (company / tech names) and domain vocabulary.
    """
    return Counter(t.lower() for t in _SIGNIFICANT_TOKEN_RE.findall(text))


def _extract_llm_headings(llm_text: str) -> list[str]:
    """Return known section headings found in LLM plain-text output.

    Headings are non-bullet, non-indented lines that match the known-section
    vocabulary (same normalisation as comparator._is_known_section).
    """
    seen: set[str] = set()
    headings: list[str] = []
    for line in llm_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-") or stripped.startswith("•"):
            continue
        if _is_known_section(stripped):
            key = _heading_key(stripped)
            if key not in seen:
                headings.append(stripped)
                seen.add(key)
    return headings


def _count_bullets_in_llm_text(llm_text: str) -> int:
    """Count '- ' bullet lines in LLM plain-text output."""
    return sum(
        1 for line in llm_text.splitlines()
        if line.strip().startswith("- ") or line.strip().startswith("• ")
    )


def _compute_duplication(
    source_text: str,
    generated_text: str,
    output_normalized: str,
) -> tuple[float, list[str]]:
    """Estimate fraction of stale source content that survived into output.

    Method:
      stale = significant tokens in source but NOT in generated text
      surviving = stale tokens that appear in output

    Returns (stale_ratio, evidence_list).
    stale_ratio 0.0 = no stale content; 1.0 = all stale content survived.

    Note: this is a heuristic signal, not a ground-truth detector.
    Common domain vocabulary naturally appears in both source and generated
    text, so it will not count as stale.  The metric is most useful for
    detecting survived proper nouns (company names, role titles).
    """
    from tailor.eval.extractor import normalize_text

    src_tok = _significant_tokens(normalize_text(source_text))
    gen_tok = _significant_tokens(normalize_text(generated_text))
    out_tok = _significant_tokens(output_normalized)

    # Tokens in source but not in generated (stale candidates)
    stale: Counter = Counter()
    for token, count in src_tok.items():
        excess = count - gen_tok.get(token, 0)
        if excess > 0:
            stale[token] = excess

    stale_total = sum(stale.values())
    if stale_total == 0:
        return 0.0, []

    surviving: Counter = Counter()
    for token, count in stale.items():
        in_out = min(count, out_tok.get(token, 0))
        if in_out > 0:
            surviving[token] = in_out

    surviving_count = sum(surviving.values())
    ratio = surviving_count / stale_total

    evidence: list[str] = []
    if ratio >= 0.35:
        top = [t for t, _ in surviving.most_common(6)]
        evidence.append(
            f"Stale-token ratio {ratio:.2f} ({surviving_count}/{stale_total} "
            f"source-only tokens in output). Top survivors: {top}"
        )
    return ratio, evidence


def _detect_leakage(output_normalized: str) -> tuple[float, list[str]]:
    """Detect internal template markers surviving into output."""
    matches = _INTERNAL_MARKER_RE.findall(output_normalized)
    if not matches:
        return 0.0, []
    penalty = min(1.0, len(matches) * 0.25)
    return penalty, [f"Internal markers found in output: {matches[:6]}"]


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_layout(
    src_extracted: ExtractedDoc,
    out_extracted: ExtractedDoc,
    source_text: str,
    generated_text: str,
) -> tuple[LayoutScore, list[str]]:
    """Compute a LayoutScore comparing source and output extracted docs.

    Parameters
    ----------
    src_extracted:
        ExtractedDoc from the source/template DOCX rendered to PDF.
    out_extracted:
        ExtractedDoc from the output DOCX (after changed-content injection)
        rendered to PDF.
    source_text:
        Plain text of the source DOCX (for duplication detection).
    generated_text:
        LLM-generated plain text that was injected (for heading extraction
        and duplication baseline).

    Returns
    -------
    (LayoutScore, evidence_list)
        evidence_list contains human-readable strings explaining penalties.
    """
    evidence: list[str] = []

    # ── 1. Topology: column structure ────────────────────────────────────
    src_cols = src_extracted.features.column_count_estimate
    out_cols = out_extracted.features.column_count_estimate
    col_conf = src_extracted.features.column_confidence

    if src_cols == out_cols:
        topology = 1.0
    elif col_conf == "high":
        topology = 0.0
        evidence.append(
            f"Column topology lost: source had {src_cols} column(s), "
            f"output has {out_cols} (high-confidence detection)"
        )
    else:
        topology = 0.5
        evidence.append(
            f"Column count differs: source={src_cols}, output={out_cols} "
            f"(confidence={col_conf})"
        )

    # ── 2. Section placement: geometric placement + heading count ────────
    expected_headings = _extract_llm_headings(generated_text)
    out_heading_keys = {_heading_key(h) for h in out_extracted.headings}
    found_n = sum(1 for h in expected_headings if _heading_key(h) in out_heading_keys)
    expected_n = len(expected_headings)

    # Geometric placement (uses block bboxes from extractor output).
    # Returns ([], None) for synthetic/empty docs — falls back to heading count.
    placement_results, placement_score = score_placement(src_extracted, out_extracted)

    if placement_score is not None:
        # Rich geometric score available
        section_placement = placement_score
        # Emit evidence for poorly placed sections
        for pr in placement_results:
            if pr.placement_score < 0.60 and (pr.input_found or pr.output_found):
                for note in pr.notes:
                    evidence.append(f"[placement:{pr.canonical_type}] {note}")
    else:
        # Fallback: heading count ratio (no block geometry in this doc)
        placement_results = []
        if expected_n == 0:
            section_placement = 1.0
        else:
            section_placement = found_n / expected_n
            if found_n < expected_n:
                missing = [h for h in expected_headings if _heading_key(h) not in out_heading_keys]
                evidence.append(
                    f"Section headings missing from output ({found_n}/{expected_n} found): "
                    f"{missing}"
                )

    # ── 3. Overflow: page count growth ───────────────────────────────────
    src_pages = src_extracted.features.page_count
    out_pages = out_extracted.features.page_count
    page_delta = out_pages - src_pages

    if page_delta <= 0:
        overflow_penalty = 0.0
    else:
        overflow_penalty = _clamp(page_delta * 0.40)
        evidence.append(
            f"Page count grew: {src_pages} → {out_pages} (+{page_delta} page(s))"
        )

    # ── 4. Duplication: stale source content surviving ───────────────────
    stale_ratio, dup_ev = _compute_duplication(
        source_text, generated_text, out_extracted.full_text_normalized
    )
    duplication_penalty = _clamp(stale_ratio * 1.5)  # ratio ≥ 0.67 → full penalty
    evidence.extend(dup_ev)

    # ── 5. Leakage: internal markers ─────────────────────────────────────
    leakage_penalty, leak_ev = _detect_leakage(out_extracted.full_text_normalized)
    evidence.extend(leak_ev)

    # ── 6. Style: bullet count quality ───────────────────────────────────
    gen_bullets = _count_bullets_in_llm_text(generated_text)
    out_bullets = out_extracted.bullet_count

    if gen_bullets == 0:
        style_score = 1.0
    else:
        ratio = out_bullets / gen_bullets
        if ratio >= 0.80:
            style_score = 1.0
        elif ratio >= 0.60:
            style_score = 0.75
            evidence.append(
                f"Bullet count below expected: generated={gen_bullets}, "
                f"output={out_bullets} (ratio={ratio:.2f})"
            )
        elif ratio >= 0.40:
            style_score = 0.50
            evidence.append(
                f"Bullet count significantly low: generated={gen_bullets}, "
                f"output={out_bullets} (ratio={ratio:.2f})"
            )
        else:
            style_score = 0.25
            evidence.append(
                f"Bullet count critically low: generated={gen_bullets}, "
                f"output={out_bullets} (ratio={ratio:.2f})"
            )

    # ── Composite ────────────────────────────────────────────────────────
    composite = _clamp(
        topology            * _WEIGHTS["topology"]
        + section_placement * _WEIGHTS["sections"]
        + (1.0 - overflow_penalty) * _WEIGHTS["no_overflow"]
        + (1.0 - duplication_penalty) * _WEIGHTS["no_dup"]
        + (1.0 - leakage_penalty) * _WEIGHTS["no_leak"]
        + style_score       * _WEIGHTS["style"]
    )

    score = LayoutScore(
        topology_preservation=round(topology, 3),
        section_placement=round(section_placement, 3),
        overflow_penalty=round(overflow_penalty, 3),
        duplication_penalty=round(duplication_penalty, 3),
        leakage_penalty=round(leakage_penalty, 3),
        style_score=round(style_score, 3),
        page_count_delta=page_delta,
        src_page_count=src_pages,
        out_page_count=out_pages,
        src_column_count=src_cols,
        out_column_count=out_cols,
        column_confidence=col_conf,
        section_headings_found=found_n,
        section_headings_expected=expected_n,
        stale_token_ratio=round(stale_ratio, 3),
        src_bullet_count=src_extracted.bullet_count,
        out_bullet_count=out_bullets,
        gen_bullet_count=gen_bullets,
        section_placement_results=placement_results,
        composite=round(composite, 3),
    )
    return score, evidence
