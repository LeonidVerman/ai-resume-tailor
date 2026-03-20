"""CLI entry point for the PDF round-trip evaluator.

Usage
-----
    # Run on a directory of PDFs:
    python -m tailor.eval --dir tests/samples/resume/pfd

    # Run on specific files:
    python -m tailor.eval --files a.pdf b.pdf

    # Promote a run as the new baseline:
    python -m tailor.eval promote --run-id 2026-03-19_2200

    # Promote even if regressions exist:
    python -m tailor.eval promote --run-id 2026-03-19_2200 --allow-regressions

Options
-------
    --output-dir   Root directory for run output (default: eval/)
    --dpi          Render DPI for page images (default: 200)
    --lo-method    LibreOffice conversion method: subprocess|docker|local
                   (default: subprocess)
    --run-id       Override auto-generated run ID (timestamp)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Default excluded filenames (Phase 1: skip two complex samples)
# ---------------------------------------------------------------------------
_DEFAULT_EXCLUDES: frozenset[str] = frozenset({
    "backend-developer-1606703830.pdf",
    "2-Leonid_Verman_Resume_2.pdf",
})

# Project root: src/tailor/eval/__main__.py -> ../../.. -> project root
_PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eval_root(output_dir: str) -> str:
    """Return the eval root directory (resolved from project root), creating it if needed."""
    resolved = str(_resolve_path(output_dir))
    os.makedirs(resolved, exist_ok=True)
    return resolved


def _resolve_path(raw: str) -> Path:
    """Resolve *raw* relative to CWD first, then project root as fallback.

    This lets users pass paths like ``tests/samples/resume/pfd`` from any
    working directory and have them resolved against the project root when
    they don't exist relative to CWD.
    """
    p = Path(raw)
    if p.is_absolute() or p.exists():
        return p
    from_root = _PROJECT_ROOT / p
    if from_root.exists():
        return from_root
    return p   # return as-is; error will be reported by the caller


def _collect_pdfs(
    directory: str | None,
    files: list[str] | None,
    excludes: frozenset[str],
) -> list[str]:
    """Return sorted list of PDF paths to evaluate."""
    paths: list[str] = []
    if directory:
        p = _resolve_path(directory)
        if not p.is_dir():
            print(
                f"ERROR: --dir '{directory}' is not a directory "
                f"(tried CWD and project root {_PROJECT_ROOT}).",
                file=sys.stderr,
            )
            sys.exit(1)
        paths = sorted(str(f) for f in p.glob("*.pdf") if f.name not in excludes)
    if files:
        paths = [str(_resolve_path(f)) for f in files if Path(f).name not in excludes]
    if not paths:
        print("ERROR: No PDF files found (check --dir or --files).", file=sys.stderr)
        sys.exit(1)
    return paths


def _make_run_id(override: str | None = None) -> str:
    if override:
        return override
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def _sample_id(pdf_path: str) -> str:
    return Path(pdf_path).stem


# ---------------------------------------------------------------------------
# Single-sample evaluation
# ---------------------------------------------------------------------------

def _run_one_sample(
    source_pdf: str,
    run_sample_dir: str,
    lo_method: str,
    dpi: int,
) -> dict:
    """Run the full pipeline + comparison for one sample.

    Returns a report dict.
    """
    from tailor.eval.pipeline import run_pipeline
    from tailor.eval.extractor import extract
    from tailor.eval.comparator import compare
    from tailor.eval.visualizer import render_page_artifacts
    from tailor.eval.reporter import build_sample_report, write_sample_report
    from tailor.eval.models import ComparisonResult, TextMetrics, LayoutMetrics

    os.makedirs(run_sample_dir, exist_ok=True)
    sample_id = _sample_id(source_pdf)

    print(f"  [{sample_id}] Running pipeline...")
    docx_path = ""
    output_pdf = ""
    error_msg = ""
    result: ComparisonResult | None = None
    artifacts: list[str] = []

    try:
        docx_path, output_pdf = run_pipeline(source_pdf, run_sample_dir, lo_method=lo_method)
        print(f"  [{sample_id}] Extracting documents...")
        src_doc = extract(source_pdf)
        out_doc = extract(output_pdf)

        print(f"  [{sample_id}] Comparing...")
        result = compare(src_doc, out_doc)

        print(f"  [{sample_id}] Rendering page artifacts...")
        artifacts = render_page_artifacts(source_pdf, output_pdf, run_sample_dir, dpi=dpi)

    except Exception as exc:
        error_msg = str(exc)
        tb = traceback.format_exc()
        print(f"  [{sample_id}] ERROR: {error_msg}")
        with open(os.path.join(run_sample_dir, "error.txt"), "w") as f:
            f.write(tb)

        # Build a minimal failed result so the report is still written
        result = ComparisonResult(
            text_metrics=TextMetrics(0, 0, 0, 0, [], [], 0, 0, 0),
            layout_metrics=LayoutMetrics(
                False, 0, 0, False, None, None, None,
                None, None, None, None, None, None, 0, 0, "none"
            ),
            issues=[],
            text_score=0.0,
            structure_score=0.0,
            layout_score=0.0,
            overall_score=0.0,
            status="error",
            error_message=error_msg,
        )

    report = build_sample_report(
        sample_id=sample_id,
        source_pdf=source_pdf,
        output_pdf=output_pdf or "",
        result=result,
        artifacts=artifacts,
        error=error_msg,
    )
    write_sample_report(report, run_sample_dir)
    status = report.get("status", "?").upper()
    score = report.get("scores", {}).get("overall_score", "?")
    print(f"  [{sample_id}] {status}  overall={score}")
    return report


# ---------------------------------------------------------------------------
# Run command
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    from tailor.eval.baseline import load_baseline, load_run_summary
    from tailor.eval.reporter import build_run_summary, write_run_summary

    eval_root = _eval_root(args.output_dir)
    runs_dir = os.path.join(eval_root, "runs")
    run_id = _make_run_id(args.run_id)
    run_dir = os.path.join(runs_dir, run_id)

    if os.path.exists(run_dir):
        print(f"WARNING: Run directory already exists: {run_dir}", file=sys.stderr)

    os.makedirs(run_dir, exist_ok=True)

    pdfs = _collect_pdfs(
        directory=args.dir,
        files=args.files,
        excludes=_DEFAULT_EXCLUDES,
    )

    print(f"\npdf-eval  run_id={run_id}  samples={len(pdfs)}  dpi={args.dpi}")
    print(f"Output: {run_dir}\n")

    sample_reports: list[dict] = []
    for pdf_path in pdfs:
        sid = _sample_id(pdf_path)
        sample_dir = os.path.join(run_dir, sid)
        report = _run_one_sample(pdf_path, sample_dir, lo_method=args.lo_method, dpi=args.dpi)
        sample_reports.append(report)

    # Load baseline for regression comparison
    baseline = load_baseline(eval_root)

    summary = build_run_summary(run_id, sample_reports, baseline_summary=baseline)
    write_run_summary(summary, run_dir)

    # Print aggregate summary
    print(f"\n{'='*60}")
    print(f"Run {run_id}: {summary['passed']}/{summary['total_samples']} passed")
    if summary.get("regressions"):
        print(f"REGRESSIONS: {summary['regressions']}")
    if summary.get("improvements"):
        print(f"Improvements: {summary['improvements']}")
    if not baseline:
        print("(No baseline — run 'promote' to set one)")

    failed_samples = [r["sample_id"] for r in sample_reports if r.get("status") != "pass"]
    if failed_samples:
        print(f"Failed: {failed_samples}")
        return 1
    return 0


# ---------------------------------------------------------------------------
# Promote command
# ---------------------------------------------------------------------------

def cmd_promote(args: argparse.Namespace) -> int:
    from tailor.eval.baseline import promote

    eval_root = _eval_root(args.output_dir)
    try:
        promote(eval_root, args.run_id, allow_regressions=args.allow_regressions)
        return 0
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-eval",
        description="PDF round-trip evaluator for the resume pipeline.",
    )
    parser.add_argument(
        "--output-dir", default="tmp/artefacts/pdf/eval",
        help="Root directory for run output (default: tmp/artefacts/pdf/eval)",
    )

    sub = parser.add_subparsers(dest="command")

    # -- run (default) -------------------------------------------------------
    run_parser = sub.add_parser("run", help="Run the evaluator (default command)")
    _add_run_args(run_parser)

    # -- promote -------------------------------------------------------------
    promote_parser = sub.add_parser("promote", help="Promote a run as the new baseline")
    promote_parser.add_argument("--run-id", required=True, help="Run ID to promote")
    promote_parser.add_argument(
        "--allow-regressions", action="store_true",
        help="Promote even if regressions exist vs current baseline",
    )

    # Add run args to top-level parser too (so 'python -m tailor.eval --dir ...' works)
    _add_run_args(parser)

    return parser


def _add_run_args(p: argparse.ArgumentParser) -> None:
    src = p.add_mutually_exclusive_group()
    src.add_argument("--dir", metavar="DIRECTORY",
                     help="Directory of source PDFs to evaluate")
    src.add_argument("--files", nargs="+", metavar="PDF",
                     help="Specific source PDF files to evaluate")
    p.add_argument("--run-id", metavar="ID",
                   help="Override auto-generated run ID (default: timestamp)")
    p.add_argument("--dpi", type=int, default=200,
                   help="DPI for rendered page images (default: 200)")
    p.add_argument(
        "--lo-method", default="subprocess",
        choices=["subprocess", "docker", "local"],
        help="LibreOffice DOCX→PDF method (default: subprocess)",
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "promote":
        return cmd_promote(args)

    # Default: run command (even without explicit 'run' subcommand)
    if not args.dir and not args.files:
        parser.print_help()
        return 1

    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
