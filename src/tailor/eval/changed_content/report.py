"""Report generation for changed-content layout evaluation.

Produces:
- Per-case JSON report + summary.txt
- Suite-level JSON report + suite_summary.md (Markdown table)
"""
from __future__ import annotations

import json
import os
from typing import Any

from tailor.eval.changed_content.taxonomy import CLASS_LABELS


def _serialize_result_list(results) -> list[dict]:
    """Convert a list of dataclasses or dicts to plain dicts."""
    out = []
    for r in results or []:
        if isinstance(r, dict):
            out.append(r)
        else:
            from dataclasses import asdict
            out.append(asdict(r))
    return out


def _serialize_placement_results(results) -> list[dict]:
    """Convert placement results (dataclass list or dict list) to plain dicts."""
    out = []
    for r in results or []:
        if isinstance(r, dict):
            out.append(r)
        else:
            from dataclasses import asdict
            out.append(asdict(r))
    return out


# ---------------------------------------------------------------------------
# Per-case report
# ---------------------------------------------------------------------------

def build_case_report(result: "CaseResult") -> dict[str, Any]:  # noqa: F821
    """Build a JSON-serializable report dict for one case."""
    from tailor.eval.changed_content.benchmark import CaseResult  # local import

    mb = result.metric_breakdown

    return {
        "case_id": result.case_id,
        "template_class": result.template_class,
        "severity": result.severity,
        "status": result.status,
        "layout_score": result.layout_score,
        "metric_breakdown": {
            "topology_preservation":   mb.get("topology_preservation"),
            "section_placement":       mb.get("section_placement"),
            "overflow_penalty":        mb.get("overflow_penalty"),
            "duplication_penalty":     mb.get("duplication_penalty"),
            "leakage_penalty":         mb.get("leakage_penalty"),
            "style_score":             mb.get("style_score"),
            "section_coherence":       mb.get("overall_section_coherence"),
            "container_stress":        mb.get("overall_container_stress_score"),
        },
        "raw_metrics": {
            "page_count_delta":          mb.get("page_count_delta"),
            "src_page_count":            mb.get("src_page_count"),
            "out_page_count":            mb.get("out_page_count"),
            "src_column_count":          mb.get("src_column_count"),
            "out_column_count":          mb.get("out_column_count"),
            "column_confidence":         mb.get("column_confidence"),
            "section_headings_found":    mb.get("section_headings_found"),
            "section_headings_expected": mb.get("section_headings_expected"),
            "stale_token_ratio":         mb.get("stale_token_ratio"),
            "src_bullet_count":          mb.get("src_bullet_count"),
            "out_bullet_count":          mb.get("out_bullet_count"),
            "gen_bullet_count":          mb.get("gen_bullet_count"),
            "src_topology":              mb.get("src_topology"),
            "out_topology":              mb.get("out_topology"),
            "topology_confidence":       mb.get("topology_confidence"),
        },
        "failure_classes": result.failure_classes,
        "failure_labels":  [CLASS_LABELS.get(c, c) for c in result.failure_classes],
        "evidence":        result.evidence,
        "section_placement": _serialize_placement_results(
            mb.get("section_placement_results", [])
        ),
        "section_coherence": _serialize_result_list(
            mb.get("section_coherence_results", [])
        ),
        "section_stress": _serialize_result_list(
            mb.get("section_stress_results", [])
        ),
        "notes":           result.notes,
        "artifacts":       result.artifacts,
        "error":           result.error,
    }


def write_case_report(report: dict, case_dir: str) -> None:
    """Write report.json and summary.txt into case_dir."""
    json_path = os.path.join(case_dir, "report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    txt_path = os.path.join(case_dir, "summary.txt")
    with open(txt_path, "w", encoding="utf-8") as fh:
        fh.write(_case_summary_text(report))


def _case_summary_text(r: dict) -> str:
    lines: list[str] = [
        f"Case:       {r['case_id']}",
        f"Template:   {r['template_class']}",
        f"Severity:   {r['severity']}",
        f"Status:     {r['status'].upper()}",
        f"Score:      {r['layout_score']:.3f}",
        f"Topology:   {r['raw_metrics'].get('src_topology') or '?'} → "
        f"{r['raw_metrics'].get('out_topology') or '?'} "
        f"(confidence={r['raw_metrics'].get('topology_confidence') or 0.0:.2f})",
        "",
        "Metric breakdown:",
        f"  topology_preservation : {r['metric_breakdown'].get('topology_preservation')}",
        f"  section_placement     : {r['metric_breakdown'].get('section_placement')}",
        f"  overflow_penalty      : {r['metric_breakdown'].get('overflow_penalty')}",
        f"  duplication_penalty   : {r['metric_breakdown'].get('duplication_penalty')}",
        f"  leakage_penalty       : {r['metric_breakdown'].get('leakage_penalty')}",
        f"  style_score           : {r['metric_breakdown'].get('style_score')}",
        f"  section_coherence     : {r['metric_breakdown'].get('section_coherence')}",
        f"  container_stress      : {r['metric_breakdown'].get('container_stress')}",
        "",
    ]
    if r["failure_classes"]:
        lines.append("Failure classes:")
        for cls in r["failure_classes"]:
            lines.append(f"  {CLASS_LABELS.get(cls, cls)}")
            for ev in (r["evidence"] or []):
                if ev.startswith(f"[{cls}]"):
                    lines.append(f"    → {ev[len(cls)+3:].strip()}")
        lines.append("")
    sp = r.get("section_placement") or []
    if sp:
        lines.append("Section placement:")
        for pr in sp:
            status = "OK" if pr.get("placement_score", 0) >= 0.70 else "WARN"
            inp = f"p{pr['input_page']}@{pr['input_y']:.2f}/{pr['input_region']}" if pr.get("input_found") else "absent"
            out = f"p{pr['output_page']}@{pr['output_y']:.2f}/{pr['output_region']}" if pr.get("output_found") else "absent"
            lines.append(
                f"  [{status}] {pr['canonical_type']:16s} "
                f"src={inp}  out={out}  score={pr.get('placement_score', 0):.2f}"
            )
        lines.append("")
    sc = r.get("section_coherence") or []
    if sc:
        stale_sections = [c["canonical_type"] for c in sc if c.get("stale_signal")]
        lines.append("Section coherence:")
        for cr in sc:
            status = "STALE" if cr.get("stale_signal") else (
                "WARN" if cr.get("coherence_score", 1.0) < 0.50 else "OK"
            )
            olap = f"  overlap={cr.get('stale_overlap', 0.0):.0%}" if cr.get("stale_signal") else ""
            lines.append(
                f"  [{status}] {cr['canonical_type']:16s} "
                f"coherence={cr.get('coherence_score', 0.0):.2f}{olap}"
            )
        if stale_sections:
            lines.append(f"  ⚠ Stale content detected: {', '.join(stale_sections)}")
        lines.append("")
    ss = r.get("section_stress") or []
    if ss:
        high_stress = [s["canonical_type"] for s in ss if s.get("stress_level") == "high"]
        lines.append("Container stress:")
        for sr in ss:
            lvl    = sr.get("stress_level", "low").upper()
            region = sr.get("region", "?")
            cg     = sr.get("char_growth_ratio", 1.0)
            hg     = sr.get("height_growth_ratio", 1.0)
            sc     = sr.get("stress_score", 0.0)
            lines.append(
                f"  [{lvl}] {sr['canonical_type']:16s} "
                f"region={region}  char×{cg:.1f}  height×{hg:.1f}  score={sc:.2f}"
            )
        if high_stress:
            lines.append(f"  ⚠ High-stress sections: {', '.join(high_stress)}")
        lines.append("")
    if r.get("error"):
        lines += [f"ERROR: {r['error']}", ""]
    if r["notes"]:
        lines += [f"Notes: {r['notes']}", ""]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Suite-level report
# ---------------------------------------------------------------------------

def build_suite_report(results: list["CaseResult"]) -> dict[str, Any]:  # noqa: F821
    """Build an aggregate report dict for a completed suite run."""
    total = len(results)
    passed = sum(1 for r in results if r.status == "pass")
    failed = sum(1 for r in results if r.status == "fail")
    errors = sum(1 for r in results if r.status == "error")

    scores = [r.layout_score for r in results if not r.error]
    avg_score = round(sum(scores) / len(scores), 3) if scores else 0.0

    # Failure class frequency
    fc_freq: dict[str, int] = {}
    for r in results:
        for cls in r.failure_classes:
            fc_freq[cls] = fc_freq.get(cls, 0) + 1

    # Sort by frequency descending
    fc_ranked = sorted(fc_freq.items(), key=lambda x: x[1], reverse=True)

    # Per-template-class averages
    by_class: dict[str, list[float]] = {}
    for r in results:
        if not r.error:
            by_class.setdefault(r.template_class, []).append(r.layout_score)
    avg_by_class = {
        cls: round(sum(sc) / len(sc), 3)
        for cls, sc in by_class.items()
    }

    # Per-severity averages
    by_sev: dict[str, list[float]] = {}
    for r in results:
        if not r.error:
            by_sev.setdefault(r.severity, []).append(r.layout_score)
    avg_by_severity = {
        sev: round(sum(sc) / len(sc), 3)
        for sev, sc in by_sev.items()
    }

    # Worst offenders (lowest scores)
    worst = sorted(
        [r for r in results if not r.error],
        key=lambda r: r.layout_score,
    )[:5]

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "average_layout_score": avg_score,
        "failure_class_frequency": dict(fc_ranked),
        "failure_class_labels": {cls: CLASS_LABELS.get(cls, cls) for cls, _ in fc_ranked},
        "average_score_by_template_class": avg_by_class,
        "average_score_by_severity": avg_by_severity,
        "worst_cases": [
            {
                "case_id":       r.case_id,
                "layout_score":  r.layout_score,
                "template_class": r.template_class,
                "severity":      r.severity,
                "failure_classes": r.failure_classes,
            }
            for r in worst
        ],
        "case_summaries": [
            {
                "case_id":       r.case_id,
                "template_class": r.template_class,
                "severity":      r.severity,
                "status":        r.status,
                "layout_score":  r.layout_score,
                "failure_classes": r.failure_classes,
            }
            for r in results
        ],
    }


def write_suite_report(report: dict, output_dir: str) -> None:
    """Write suite_report.json and suite_summary.md into output_dir."""
    json_path = os.path.join(output_dir, "suite_report.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    md_path = os.path.join(output_dir, "suite_summary.md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(_suite_summary_md(report))


def _suite_summary_md(r: dict) -> str:
    lines: list[str] = [
        "# Changed-Content Layout Evaluation — Suite Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total cases | {r['total']} |",
        f"| Passed (≥ 0.70) | {r['passed']} |",
        f"| Failed | {r['failed']} |",
        f"| Errors | {r['errors']} |",
        f"| Average layout score | {r['average_layout_score']} |",
        "",
        "## Failure class frequency",
        "",
        "| Class | Label | Count |",
        "|-------|-------|-------|",
    ]
    for cls, count in r["failure_class_frequency"].items():
        label = CLASS_LABELS.get(cls, cls)
        lines.append(f"| `{cls}` | {label} | {count} |")

    lines += [
        "",
        "## Per-case results",
        "",
        "| Case | Template | Severity | Score | Status | Failures |",
        "|------|----------|----------|-------|--------|----------|",
    ]
    for cs in r["case_summaries"]:
        fc = ", ".join(cs["failure_classes"]) if cs["failure_classes"] else "—"
        lines.append(
            f"| {cs['case_id']} | {cs['template_class']} | {cs['severity']} "
            f"| {cs['layout_score']:.3f} | {cs['status'].upper()} | {fc} |"
        )

    if r.get("average_score_by_template_class"):
        lines += [
            "",
            "## Average score by template class",
            "",
            "| Template class | Avg score |",
            "|----------------|-----------|",
        ]
        for cls, avg in r["average_score_by_template_class"].items():
            lines.append(f"| {cls} | {avg:.3f} |")

    if r.get("average_score_by_severity"):
        lines += [
            "",
            "## Average score by severity",
            "",
            "| Severity | Avg score |",
            "|----------|-----------|",
        ]
        for sev, avg in r["average_score_by_severity"].items():
            lines.append(f"| {sev} | {avg:.3f} |")

    if r.get("worst_cases"):
        lines += [
            "",
            "## Worst cases",
            "",
            "| Case | Score | Template | Severity | Failures |",
            "|------|-------|----------|----------|----------|",
        ]
        for wc in r["worst_cases"]:
            fc = ", ".join(wc["failure_classes"]) if wc["failure_classes"] else "—"
            lines.append(
                f"| {wc['case_id']} | {wc['layout_score']:.3f} "
                f"| {wc['template_class']} | {wc['severity']} | {fc} |"
            )

    lines.append("")
    return "\n".join(lines)
