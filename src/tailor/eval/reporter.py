"""Generate per-sample JSON reports and aggregate run summaries."""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

from tailor.eval.models import ComparisonResult


# ---------------------------------------------------------------------------
# Per-sample report
# ---------------------------------------------------------------------------

def build_sample_report(
    sample_id: str,
    source_pdf: str,
    output_pdf: str,
    result: ComparisonResult,
    artifacts: list[str],
    error: str = "",
) -> dict[str, Any]:
    """Build a JSON-serializable dict for one sample."""
    tm = result.text_metrics
    lm = result.layout_metrics

    report: dict[str, Any] = {
        "sample_id": sample_id,
        "source_pdf": source_pdf,
        "output_pdf": output_pdf,
        "status": result.status if not error else "error",
        "scores": {
            "text_score": result.text_score,
            "structure_score": result.structure_score,
            "layout_score": result.layout_score,
            "overall_score": result.overall_score,
        },
        "text_metrics": {
            "token_precision": tm.token_precision,
            "token_recall": tm.token_recall,
            "token_f1": tm.token_f1,
            "sequence_similarity": tm.sequence_similarity,
            "missing_headings": tm.missing_headings,
            "extra_headings": tm.extra_headings,
            "bullet_count_source": tm.bullet_count_source,
            "bullet_count_output": tm.bullet_count_output,
            "bullet_count_delta": tm.bullet_count_delta,
        },
        "layout_metrics": {
            "page_count_match": lm.page_count_match,
            "page_count_source": lm.page_count_source,
            "page_count_output": lm.page_count_output,
            "page_size_match": lm.page_size_match,
            "content_bbox_shift_norm": lm.content_bbox_shift_norm,
            "bullet_indent_delta_norm": lm.bullet_indent_delta_norm,
            "bullet_indent_source_pt": lm.bullet_indent_source_pt,
            "bullet_indent_output_pt": lm.bullet_indent_output_pt,
            "section_gap_delta_norm": lm.section_gap_delta_norm,
            "section_gap_source_pt": lm.section_gap_source_pt,
            "section_gap_output_pt": lm.section_gap_output_pt,
            "column_count_source": lm.column_count_source,
            "column_count_output": lm.column_count_output,
            "column_confidence": lm.column_confidence,
        },
        "issues": [i.to_dict() for i in result.issues],
        "artifacts": {
            "side_by_side": [a for a in artifacts if "side_by_side" in a],
            "diff_images": [a for a in artifacts if a.startswith("diff_")],
            "source_pages": [a for a in artifacts if a.startswith("source_page_")],
            "output_pages": [a for a in artifacts if a.startswith("output_page_")],
        },
    }

    if error:
        report["error_message"] = error

    return report


def write_sample_report(report: dict, run_sample_dir: str) -> None:
    """Write report.json and summary.txt into *run_sample_dir*."""
    with open(os.path.join(run_sample_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    lines = _build_sample_summary_text(report)
    with open(os.path.join(run_sample_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(lines)


def _build_sample_summary_text(report: dict) -> str:
    """Build a human-readable summary string for one sample."""
    sid = report["sample_id"]
    status = report["status"].upper()
    scores = report.get("scores", {})
    tm = report.get("text_metrics", {})
    lm = report.get("layout_metrics", {})
    issues = report.get("issues", [])

    lines = [
        f"Sample: {sid}",
        f"Status: {status}",
        "",
        "Scores:",
        f"  text={scores.get('text_score', '?'):.3f}  "
        f"structure={scores.get('structure_score', '?'):.3f}  "
        f"layout={scores.get('layout_score', '?'):.3f}  "
        f"overall={scores.get('overall_score', '?'):.3f}",
        "",
        "Text metrics:",
        f"  token_f1={tm.get('token_f1', '?'):.4f}  "
        f"precision={tm.get('token_precision', '?'):.4f}  "
        f"recall={tm.get('token_recall', '?'):.4f}",
        f"  seq_similarity={tm.get('sequence_similarity', '?'):.4f}",
        f"  bullets: source={tm.get('bullet_count_source', '?')}  "
        f"output={tm.get('bullet_count_output', '?')}  "
        f"delta={tm.get('bullet_count_delta', '?')}",
    ]

    if tm.get("missing_headings"):
        lines.append(f"  MISSING headings: {tm['missing_headings']}")
    if tm.get("extra_headings"):
        lines.append(f"  Extra headings: {tm['extra_headings']}")

    lines += [
        "",
        "Layout metrics:",
        f"  pages: source={lm.get('page_count_source', '?')}  "
        f"output={lm.get('page_count_output', '?')}  "
        f"match={lm.get('page_count_match', '?')}",
        f"  page_size_match={lm.get('page_size_match', '?')}",
        f"  bullet_indent_delta_norm={lm.get('bullet_indent_delta_norm', 'n/a')}",
        f"  section_gap_delta_norm={lm.get('section_gap_delta_norm', 'n/a')}",
        f"  content_bbox_shift_norm={lm.get('content_bbox_shift_norm', 'n/a')}",
        f"  columns: source={lm.get('column_count_source', '?')}  "
        f"output={lm.get('column_count_output', '?')}  "
        f"confidence={lm.get('column_confidence', '?')}",
    ]

    if issues:
        lines += ["", "Issues:"]
        for issue in issues:
            sev = issue.get("severity", "?").upper()
            itype = issue.get("type", "?")
            desc = issue.get("description", "")
            lines.append(f"  [{sev}] {itype}: {desc}")

    if report.get("error_message"):
        lines += ["", f"ERROR: {report['error_message']}"]

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Aggregate run summary
# ---------------------------------------------------------------------------

def build_run_summary(
    run_id: str,
    sample_reports: list[dict],
    baseline_summary: dict | None = None,
) -> dict[str, Any]:
    """Build the aggregate run_summary.json dict."""
    total = len(sample_reports)
    passed = sum(1 for r in sample_reports if r.get("status") == "pass")
    failed = sum(1 for r in sample_reports if r.get("status") == "fail")
    errors = sum(1 for r in sample_reports if r.get("status") == "error")

    # Issue frequency
    issue_counter: dict[str, int] = {}
    for r in sample_reports:
        for issue in r.get("issues", []):
            itype = issue.get("type", "unknown")
            issue_counter[itype] = issue_counter.get(itype, 0) + 1
    common_issues = sorted(
        [{"type": k, "count": v} for k, v in issue_counter.items()],
        key=lambda x: x["count"],
        reverse=True,
    )

    # Regression / improvement classification vs baseline
    regressions: list[str] = []
    improvements: list[str] = []
    still_passing: list[str] = []
    still_failing: list[str] = []

    if baseline_summary:
        baseline_by_id: dict[str, str] = {
            r.get("sample_id", ""): r.get("status", "")
            for r in baseline_summary.get("sample_results", [])
        }
        for r in sample_reports:
            sid = r.get("sample_id", "")
            cur = r.get("status", "")
            prev = baseline_by_id.get(sid, "")
            if prev == "pass" and cur in ("fail", "error"):
                regressions.append(sid)
            elif prev in ("fail", "error") and cur == "pass":
                improvements.append(sid)
            elif prev == "pass" and cur == "pass":
                still_passing.append(sid)
            elif prev in ("fail", "error") and cur in ("fail", "error"):
                still_failing.append(sid)

    summary: dict[str, Any] = {
        "run_id": run_id,
        "total_samples": total,
        "passed": passed,
        "failed": failed,
        "errors": errors,
        "common_issues": common_issues,
        "sample_results": [
            {
                "sample_id": r.get("sample_id"),
                "status": r.get("status"),
                "overall_score": r.get("scores", {}).get("overall_score"),
            }
            for r in sample_reports
        ],
    }

    if baseline_summary:
        summary["baseline_run_id"] = baseline_summary.get("run_id", "")
        summary["regressions"] = regressions
        summary["improvements"] = improvements
        summary["still_passing"] = still_passing
        summary["still_failing"] = still_failing

    return summary


def write_run_summary(summary: dict, run_dir: str) -> None:
    """Write run_summary.json and run_summary.md to *run_dir*."""
    with open(os.path.join(run_dir, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    md = _build_run_summary_md(summary)
    with open(os.path.join(run_dir, "run_summary.md"), "w", encoding="utf-8") as f:
        f.write(md)


def _build_run_summary_md(summary: dict) -> str:
    run_id = summary.get("run_id", "")
    total = summary.get("total_samples", 0)
    passed = summary.get("passed", 0)
    failed = summary.get("failed", 0)
    errors = summary.get("errors", 0)

    lines = [
        f"# PDF Eval Run: {run_id}",
        "",
        f"**Samples:** {total}  |  **Passed:** {passed}  |  "
        f"**Failed:** {failed}  |  **Errors:** {errors}",
        "",
        "## Sample Results",
        "",
        "| Sample | Status | Overall Score |",
        "|--------|--------|--------------|",
    ]
    for r in summary.get("sample_results", []):
        sid = r.get("sample_id", "")
        st = r.get("status", "").upper()
        sc = r.get("overall_score")
        sc_str = f"{sc:.3f}" if sc is not None else "—"
        icon = "✅" if st == "PASS" else ("❌" if st == "FAIL" else "⚠️")
        lines.append(f"| {sid} | {icon} {st} | {sc_str} |")

    if summary.get("common_issues"):
        lines += [
            "",
            "## Most Common Issues",
            "",
            "| Issue Type | Count |",
            "|------------|-------|",
        ]
        for issue in summary["common_issues"]:
            lines.append(f"| {issue['type']} | {issue['count']} |")

    baseline_id = summary.get("baseline_run_id")
    if baseline_id:
        lines += ["", f"## Regression Analysis (vs. baseline `{baseline_id}`)"]
        regressions = summary.get("regressions", [])
        improvements = summary.get("improvements", [])
        if regressions:
            lines += ["", f"**Regressions ({len(regressions)}):** " + ", ".join(f"`{s}`" for s in regressions)]
        if improvements:
            lines += ["", f"**Improvements ({len(improvements)}):** " + ", ".join(f"`{s}`" for s in improvements)]
        if not regressions and not improvements:
            lines += ["", "No regressions or improvements relative to baseline."]

    return "\n".join(lines) + "\n"
