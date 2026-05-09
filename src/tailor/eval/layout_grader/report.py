"""Report generation for layout grading results.

Outputs:
  aggregate.json    machine-readable aggregate metrics + grade array
  summary.txt       human-readable sorted sample list with worst offenders
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path

from tailor.eval.layout_grader.grader import SampleGrade


def build_aggregate(grades: list[SampleGrade]) -> dict:
    """Build an aggregate metrics dict from a list of grades."""
    if not grades:
        return {
            "sample_count": 0,
            "pass_count": 0,
            "warning_count": 0,
            "fail_count": 0,
            "hard_fail_count": 0,
            "average_score": 0.0,
            "median_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "worst_samples": [],
            "failure_class_frequency": {},
        }

    scores = [g.composite_score for g in grades]
    pass_count = sum(1 for g in grades if g.status == "pass")
    warning_count = sum(1 for g in grades if g.status == "warning")
    fail_count = sum(1 for g in grades if g.status in ("fail", "hard_fail"))
    hard_fail_count = sum(1 for g in grades if g.hard_fail)

    sorted_grades = sorted(grades, key=lambda g: g.composite_score)
    worst = [g.sample_id for g in sorted_grades[:5]]

    fc_freq: dict[str, int] = {}
    for g in grades:
        for fc in g.failure_classes:
            fc_freq[fc] = fc_freq.get(fc, 0) + 1

    return {
        "sample_count": len(grades),
        "pass_count": pass_count,
        "warning_count": warning_count,
        "fail_count": fail_count,
        "hard_fail_count": hard_fail_count,
        "average_score": round(statistics.mean(scores), 1),
        "median_score": round(statistics.median(scores), 1),
        "min_score": round(min(scores), 1),
        "max_score": round(max(scores), 1),
        "worst_samples": worst,
        "failure_class_frequency": dict(
            sorted(fc_freq.items(), key=lambda x: -x[1])
        ),
    }


def compare_baseline(
    grades: list[SampleGrade],
    baseline_path: str,
) -> list[dict]:
    """Compare current grades against a saved baseline aggregate.

    The baseline file must be a JSON written by this module (it contains a
    top-level 'grades' array with per-sample composite_score values).

    Returns a list of regression dicts sorted by score_delta ascending.
    """
    try:
        data = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    except Exception:
        return []

    baseline_scores: dict[str, float] = {
        g.get("sample_id", ""): g.get("composite_score", 0.0)
        for g in data.get("grades", [])
        if g.get("sample_id")
    }

    regressions = []
    for g in grades:
        if g.sample_id not in baseline_scores:
            continue
        prev = baseline_scores[g.sample_id]
        delta = g.composite_score - prev
        if delta <= -5:
            severe = delta <= -15 or (
                prev >= 75 and g.status in ("fail", "hard_fail")
            )
            regressions.append({
                "sample_id": g.sample_id,
                "score_before": round(prev, 1),
                "score_after": g.composite_score,
                "score_delta": round(delta, 1),
                "regression_class": "SEVERE_REGRESSION" if severe else "REGRESSION",
                "status_before": (
                    "pass" if prev >= 75 else ("warning" if prev >= 60 else "fail")
                ),
                "status_after": g.status,
            })

    return sorted(regressions, key=lambda r: r["score_delta"])


def write_summary_txt(
    grades: list[SampleGrade],
    aggregate: dict,
    path: Path,
    regressions: list[dict] | None = None,
) -> None:
    """Write a human-readable summary sorted by composite score ascending."""
    lines: list[str] = []

    lines.append("=" * 60)
    lines.append("LAYOUT GRADING SUMMARY")
    lines.append("=" * 60)
    lines.append(f"Total samples : {aggregate['sample_count']}")
    lines.append(f"  PASS        : {aggregate['pass_count']}")
    lines.append(f"  WARNING     : {aggregate['warning_count']}")
    lines.append(f"  FAIL        : {aggregate['fail_count']}")
    lines.append(f"  HARD FAIL   : {aggregate['hard_fail_count']}")
    lines.append(f"Average score : {aggregate['average_score']:.1f}")
    lines.append(f"Median score  : {aggregate['median_score']:.1f}")
    lines.append(f"Min score     : {aggregate['min_score']:.1f}")
    lines.append(f"Max score     : {aggregate['max_score']:.1f}")
    lines.append("")

    if regressions:
        lines.append("--- Regressions vs Baseline ---")
        for r in regressions:
            lines.append(
                f"  [{r['regression_class']}] {r['sample_id']}: "
                f"{r['score_before']:.1f}→{r['score_after']:.1f} "
                f"(Δ{r['score_delta']:+.1f})"
            )
        lines.append("")

    lines.append("--- Samples (sorted by score ascending) ---")
    for g in sorted(grades, key=lambda g: g.composite_score):
        flag = " [HARD FAIL]" if g.hard_fail else ""
        lines.append(f"  {g.composite_score:5.1f}  {g.status:<10}  {g.sample_id}{flag}")
        for ev in g.evidence[:2]:
            lines.append(f"             {ev}")
    lines.append("")

    fc_freq = aggregate.get("failure_class_frequency", {})
    if fc_freq:
        lines.append("--- Failure Class Frequency ---")
        for fc, count in fc_freq.items():
            lines.append(f"  {fc:<35}: {count}")
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
