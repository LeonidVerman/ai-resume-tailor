"""
scripts/generate_classification_report.py

Generate aggregated classification report from final output artefacts.

Usage:
    python scripts/generate_classification_report.py [--docx-dir D] [--pdf-dir P] [--out-dir O]

Defaults:
    --docx-dir  tmp/artefacts/classification/docx
    --pdf-dir   tmp/artefacts/classification/pdf
    --out-dir   tmp/artefacts/classification

Outputs:
    <out-dir>/classification_report.json
    <out-dir>/classification_report_summary.txt
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path


def _load_artefacts(docx_dir: Path, pdf_dir: Path) -> list[dict]:
    docs: list[dict] = []
    for folder, source_kind in [(docx_dir, "docx"), (pdf_dir, "pdf")]:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            data["_filename"] = path.name
            data["_source_kind"] = source_kind
            docs.append(data)
    return docs


def _count_errors(docs: list[dict], validation_key: str) -> Counter:
    counter: Counter = Counter()
    for doc in docs:
        validation = doc.get(validation_key)
        if not isinstance(validation, dict):
            continue
        for err in validation.get("errors", []):
            code = err.get("code")
            if code:
                counter[code] += 1
    return counter


def _top_errors(counter: Counter, n: int = 10) -> list[dict]:
    return [{"code": code, "count": cnt} for code, cnt in counter.most_common(n)]


def _pct(count: int, total: int) -> float:
    return round(count / total * 100, 1) if total else 0.0


def build_report(docs: list[dict]) -> dict:
    total = len(docs)
    by_kind: Counter = Counter(d["_source_kind"] for d in docs)

    status_counts: Counter = Counter(d.get("status", "unknown") for d in docs)

    initial_valid = sum(1 for d in docs if d.get("invalid_section_count_before_repair", 0) == 0)
    repaired = status_counts.get("repaired", 0)
    downgraded = status_counts.get("downgraded", 0) + status_counts.get("repaired_and_downgraded", 0)
    total_valid_final = status_counts.get("valid", 0) + status_counts.get("repaired", 0)

    problem_categories = {
        "initial_classification_weakness": sum(
            1 for d in docs if d.get("invalid_section_count_before_repair", 0) > 0
        ),
        "repair_success": repaired,
        "repair_partial": status_counts.get("repaired_and_downgraded", 0),
        "downgrade_only": status_counts.get("downgraded", 0),
    }

    errors_before = _count_errors(docs, "validation")
    errors_after = _count_errors(
        [d for d in docs if d.get("validation_after_repair")], "validation_after_repair"
    )

    def _examples(status: str, limit: int = 8) -> list[str]:
        return [d["_filename"] for d in docs if d.get("status") == status][:limit]

    return {
        "scanned_documents": total,
        "by_source_kind": dict(by_kind),
        "metrics": {
            "initial_valid": {"count": initial_valid, "pct": _pct(initial_valid, total)},
            "repaired": {"count": repaired, "pct": _pct(repaired, total)},
            "downgraded": {"count": downgraded, "pct": _pct(downgraded, total)},
            "total_valid_final": {"count": total_valid_final, "pct": _pct(total_valid_final, total)},
        },
        "status_counts": dict(status_counts),
        "problem_categories": problem_categories,
        "top_validation_errors_before_repair": _top_errors(errors_before),
        "top_validation_errors_after_repair": _top_errors(errors_after),
        "examples": {
            "repaired": _examples("repaired"),
            "repaired_and_downgraded": _examples("repaired_and_downgraded"),
            "downgraded": _examples("downgraded"),
        },
    }


def _format_text(report: dict) -> str:
    lines: list[str] = []
    total = report["scanned_documents"]
    bk = report["by_source_kind"]
    m = report["metrics"]
    sc = report["status_counts"]
    pc = report["problem_categories"]

    lines.append("=" * 60)
    lines.append("CLASSIFICATION REPORT")
    lines.append("=" * 60)
    lines.append(f"Total documents : {total}")
    lines.append(f"  docx          : {bk.get('docx', 0)}")
    lines.append(f"  pdf           : {bk.get('pdf', 0)}")
    lines.append("")

    lines.append("--- Main Metrics ---")
    lines.append(f"Initial valid   : {m['initial_valid']['count']:>4}  ({m['initial_valid']['pct']}%)")
    lines.append(f"Repaired        : {m['repaired']['count']:>4}  ({m['repaired']['pct']}%)")
    lines.append(f"Downgraded      : {m['downgraded']['count']:>4}  ({m['downgraded']['pct']}%)")
    lines.append(f"Total valid (final): {m['total_valid_final']['count']:>4}  ({m['total_valid_final']['pct']}%)")
    lines.append("")

    lines.append("--- Status Counts ---")
    for status, cnt in sorted(sc.items()):
        lines.append(f"  {status:<28}: {cnt}")
    lines.append("")

    lines.append("--- Problem Categories ---")
    lines.append(f"  Initial weakness (repair needed): {pc['initial_classification_weakness']}")
    lines.append(f"  Repair success                 : {pc['repair_success']}")
    lines.append(f"  Repair partial (still downgraded): {pc['repair_partial']}")
    lines.append(f"  Downgrade only (no repair)     : {pc['downgrade_only']}")
    lines.append("")

    lines.append("--- Top Validation Errors Before Repair ---")
    for e in report["top_validation_errors_before_repair"]:
        lines.append(f"  {e['code']:<45}: {e['count']}")
    lines.append("")

    after = report["top_validation_errors_after_repair"]
    if after:
        lines.append("--- Top Validation Errors After Repair ---")
        for e in after:
            lines.append(f"  {e['code']:<45}: {e['count']}")
        lines.append("")

    ex = report["examples"]
    for label, key in [("Repaired", "repaired"), ("Repaired+Downgraded", "repaired_and_downgraded"), ("Downgraded", "downgraded")]:
        files = ex.get(key, [])
        if files:
            lines.append(f"--- Examples: {label} ---")
            for f in files:
                lines.append(f"  {f}")
            lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> None:
    repo_root = Path(__file__).parent.parent
    default_base = repo_root / "tmp" / "artefacts" / "classification"

    parser = argparse.ArgumentParser(description="Generate classification report from artefacts.")
    parser.add_argument("--docx-dir", default=str(default_base / "docx"))
    parser.add_argument("--pdf-dir", default=str(default_base / "pdf"))
    parser.add_argument("--out-dir", default=str(default_base))
    args = parser.parse_args()

    docx_dir = Path(args.docx_dir)
    pdf_dir = Path(args.pdf_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = _load_artefacts(docx_dir, pdf_dir)
    if not docs:
        print("No classification artefacts found.")
        return

    report = build_report(docs)
    summary = _format_text(report)

    json_path = out_dir / "classification_report.json"
    txt_path = out_dir / "classification_report_summary.txt"

    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    txt_path.write_text(summary, encoding="utf-8")

    print(summary)
    print(f"JSON  : {json_path}")
    print(f"Text  : {txt_path}")


if __name__ == "__main__":
    main()
