"""Failure taxonomy for changed-content layout evaluation.

Six failure classes mirror the dominant layout degradation patterns
observed when LLM-generated content is injected into a DOCX template:

  A — Section boundary failure   (wrong sections extracted / swallowed)
  B — Container assignment fail  (content routed to wrong visual region)
  C — Overflow / fit failure     (text too large, page growth)
  D — Duplication / stale        (source content survives alongside new)
  E — Style prototype failure    (bullets / indents / headings degraded)
  F — Topology collapse          (column / sidebar structure lost)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FailureClass(str, Enum):
    A_SECTION_BOUNDARY    = "A_SECTION_BOUNDARY"
    B_CONTAINER_ASSIGNMENT = "B_CONTAINER_ASSIGNMENT"
    C_OVERFLOW_FIT        = "C_OVERFLOW_FIT"
    D_DUPLICATION_STALE   = "D_DUPLICATION_STALE"
    E_STYLE_PROTOTYPE     = "E_STYLE_PROTOTYPE"
    F_TOPOLOGY_COLLAPSE   = "F_TOPOLOGY_COLLAPSE"


CLASS_LABELS: dict[str, str] = {
    FailureClass.A_SECTION_BOUNDARY:    "Class A — Section boundary failure",
    FailureClass.B_CONTAINER_ASSIGNMENT: "Class B — Container assignment failure",
    FailureClass.C_OVERFLOW_FIT:        "Class C — Overflow / fit failure",
    FailureClass.D_DUPLICATION_STALE:   "Class D — Duplication / stale survival",
    FailureClass.E_STYLE_PROTOTYPE:     "Class E — Style prototype failure",
    FailureClass.F_TOPOLOGY_COLLAPSE:   "Class F — Topology collapse",
}


@dataclass
class FailureClassification:
    """Ordered list of detected failure classes with supporting evidence."""

    classes: list[str] = field(default_factory=list)          # FailureClass values
    evidence: dict[str, list[str]] = field(default_factory=dict)  # class → reasons

    def add(self, cls: FailureClass, reason: str) -> None:
        key = cls.value
        if key not in self.classes:
            self.classes.append(key)
        self.evidence.setdefault(key, []).append(reason)

    def to_dict(self) -> dict:
        return {
            "classes": self.classes,
            "labels": [CLASS_LABELS.get(c, c) for c in self.classes],
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

def classify_failures(layout_score: "LayoutScore") -> FailureClassification:  # noqa: F821
    """Derive failure classes from a LayoutScore.

    All thresholds here are deterministic — no randomness, no ML.
    """
    from tailor.eval.changed_content.scorer import LayoutScore  # local import avoids cycle

    fc = FailureClassification()

    # ── Class F: topology collapse ───────────────────────────────────────
    if (
        layout_score.src_column_count != layout_score.out_column_count
        and layout_score.column_confidence == "high"
    ):
        fc.add(
            FailureClass.F_TOPOLOGY_COLLAPSE,
            (
                f"Column count changed: {layout_score.src_column_count} → "
                f"{layout_score.out_column_count} (high confidence detection)"
            ),
        )
    elif (
        layout_score.topology_preservation < 0.50
        and layout_score.column_confidence != "none"
    ):
        fc.add(
            FailureClass.F_TOPOLOGY_COLLAPSE,
            f"Topology preservation score low: {layout_score.topology_preservation:.2f}",
        )

    # ── Class C: overflow / fit ──────────────────────────────────────────
    if layout_score.page_count_delta > 0:
        fc.add(
            FailureClass.C_OVERFLOW_FIT,
            (
                f"Page count grew by {layout_score.page_count_delta} "
                f"({layout_score.src_page_count} → {layout_score.out_page_count})"
            ),
        )
    elif layout_score.overflow_penalty >= 0.50:
        fc.add(
            FailureClass.C_OVERFLOW_FIT,
            f"Overflow penalty high: {layout_score.overflow_penalty:.2f}",
        )

    # ── Class D: duplication / stale survival ────────────────────────────
    if layout_score.duplication_penalty >= 0.40:
        fc.add(
            FailureClass.D_DUPLICATION_STALE,
            (
                f"Stale-token ratio {layout_score.stale_token_ratio:.2f} "
                f"(duplication penalty={layout_score.duplication_penalty:.2f})"
            ),
        )

    # ── Class A / B / F from per-section placement results ──────────────
    placement_results = layout_score.section_placement_results  # list[SectionPlacementResult | dict]
    if placement_results:
        missing_out = [
            r for r in placement_results
            if _pr_get(r, "input_found") and not _pr_get(r, "output_found")
        ]
        region_mismatches = [
            r for r in placement_results
            if _pr_get(r, "input_found") and _pr_get(r, "output_found")
            and _pr_get(r, "region_score", 1.0) == 0.0
        ]
        bad_vertical = [
            r for r in placement_results
            if _pr_get(r, "input_found") and _pr_get(r, "output_found")
            and _pr_get(r, "vertical_score", 1.0) < 0.30
        ]

        # Class A: source sections absent from output
        for r in missing_out:
            fc.add(
                FailureClass.A_SECTION_BOUNDARY,
                f"'{_pr_get(r, 'canonical_type')}' found in source but missing from output",
            )

        # Class B: sections routed to wrong region
        for r in region_mismatches:
            fc.add(
                FailureClass.B_CONTAINER_ASSIGNMENT,
                (
                    f"'{_pr_get(r, 'canonical_type')}' moved from "
                    f"{_pr_get(r, 'input_region')} → {_pr_get(r, 'output_region')}"
                ),
            )

        # Class F: multiple sections with severe vertical displacement
        if len(bad_vertical) >= 2:
            types = [_pr_get(r, "canonical_type") for r in bad_vertical]
            fc.add(
                FailureClass.F_TOPOLOGY_COLLAPSE,
                f"{len(bad_vertical)} sections severely displaced vertically: {types}",
            )

    else:
        # Fallback to heading count heuristic (no geometric data)
        if layout_score.section_headings_expected > 0:
            coverage = (
                layout_score.section_headings_found / layout_score.section_headings_expected
            )
            if coverage < 0.70:
                missing = layout_score.section_headings_expected - layout_score.section_headings_found
                fc.add(
                    FailureClass.A_SECTION_BOUNDARY,
                    (
                        f"Only {layout_score.section_headings_found}/"
                        f"{layout_score.section_headings_expected} expected section "
                        f"headings found in output ({missing} missing)"
                    ),
                )

    # ── Class E: style prototype failure ────────────────────────────────
    if layout_score.style_score < 0.60:
        fc.add(
            FailureClass.E_STYLE_PROTOTYPE,
            f"Style score low ({layout_score.style_score:.2f}) — bullet/heading rendering degraded",
        )

    # ── Class B: container assignment / leakage ─────────────────────────
    if layout_score.leakage_penalty >= 0.25:
        fc.add(
            FailureClass.B_CONTAINER_ASSIGNMENT,
            f"Content leakage detected (penalty={layout_score.leakage_penalty:.2f})",
        )

    return fc


def _pr_get(pr, key: str, default=None):
    """Get a field from either a SectionPlacementResult or a dict."""
    if isinstance(pr, dict):
        return pr.get(key, default)
    return getattr(pr, key, default)
