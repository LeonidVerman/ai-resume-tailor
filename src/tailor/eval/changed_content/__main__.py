"""CLI entry point for the changed-content layout evaluator.

Usage
-----
    # Run a suite file (recommended):
    python -m tailor.eval.changed_content run --suite cc_suite.json

    # Run a single case file:
    python -m tailor.eval.changed_content run --case cc_case.json

    # Override output directory and LibreOffice method:
    python -m tailor.eval.changed_content run --suite cc_suite.json \\
        --output-dir tmp/cc_eval --lo-method docker

Suite JSON format
-----------------
    {
      "suite_id": "cc_eval_v1",
      "cases": [
        {
          "case_id": "linear_01",
          "source_docx": "tests/samples/resume/docx/1-Leonid_Verman_Resume_Template.docx",
          "generated_debug_json": "tmp/generation samples/Veeva_Systems-...-debug.json",
          "template_class_hint": "linear",
          "severity": "S3",
          "notes": "pathological mismatch test"
        }
      ]
    }

Case JSON format (single case)
-------------------------------
    {
      "case_id": "sidebar_01",
      "source_docx": "path/to/template.docx",
      "generated_resume_text_file": "path/to/llm_output.txt",
      "template_class_hint": "table_sidebar",
      "severity": "S2"
    }
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_PROJECT_ROOT: Path = Path(__file__).parent.parent.parent.parent.parent


def _resolve(raw: str) -> Path:
    p = Path(raw)
    if p.is_absolute() or p.exists():
        return p
    from_root = _PROJECT_ROOT / p
    if from_root.exists():
        return from_root
    return p


def _make_run_id() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def cmd_run(args: argparse.Namespace) -> int:
    from tailor.eval.changed_content.benchmark import (
        BenchmarkCase,
        load_suite_from_file,
        run_suite,
    )

    output_dir = str(_resolve(args.output_dir))
    lo_method  = args.lo_method
    dpi        = args.dpi

    # Collect cases
    if args.suite:
        suite_path = str(_resolve(args.suite))
        suite_id, cases = load_suite_from_file(suite_path)
        run_dir = str(Path(output_dir) / suite_id / _make_run_id())
    elif args.case:
        case_path = str(_resolve(args.case))
        data = json.loads(Path(case_path).read_bytes())
        cases = [BenchmarkCase.from_dict(data)]
        suite_id = cases[0].case_id
        run_dir = str(Path(output_dir) / suite_id / _make_run_id())
    else:
        print("ERROR: provide --suite or --case", file=sys.stderr)
        return 1

    if not cases:
        print("ERROR: no cases found in suite", file=sys.stderr)
        return 1

    print(
        f"\ncc-eval  suite={suite_id}  cases={len(cases)}"
        f"  lo={lo_method}  dpi={dpi}"
    )
    print(f"Output: {run_dir}\n")

    results = run_suite(cases, run_dir, lo_method=lo_method, dpi=dpi)

    # Print summary
    passed  = sum(1 for r in results if r.status == "pass")
    failed  = sum(1 for r in results if r.status == "fail")
    errors  = sum(1 for r in results if r.status == "error")
    scores  = [r.layout_score for r in results if not r.error]
    avg     = sum(scores) / len(scores) if scores else 0.0

    print(f"\n{'='*60}")
    print(f"cc-eval {suite_id}: {passed}/{len(results)} passed  avg={avg:.3f}")
    if failed:
        print(f"Failed:  {[r.case_id for r in results if r.status == 'fail']}")
    if errors:
        print(f"Errors:  {[r.case_id for r in results if r.status == 'error']}")

    print(f"Report:  {run_dir}/suite_summary.md")
    return 0 if errors == 0 and failed == 0 else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cc-eval",
        description="Changed-content DOCX layout evaluator.",
    )
    parser.add_argument(
        "--output-dir", default="tmp/artefacts/cc_eval",
        help="Root directory for run output (default: tmp/artefacts/cc_eval)",
    )

    sub = parser.add_subparsers(dest="command")
    run_p = sub.add_parser("run", help="Run changed-content evaluation")
    _add_run_args(run_p)
    _add_run_args(parser)  # allow top-level usage without 'run' subcommand

    return parser


def _add_run_args(p: argparse.ArgumentParser) -> None:
    src = p.add_mutually_exclusive_group()
    src.add_argument("--suite", metavar="SUITE_JSON",
                     help="Path to suite JSON file (multi-case)")
    src.add_argument("--case", metavar="CASE_JSON",
                     help="Path to single case JSON file")
    p.add_argument("--lo-method", default="subprocess",
                   choices=["subprocess", "docker", "local"],
                   help="LibreOffice DOCX→PDF method (default: subprocess)")
    p.add_argument("--dpi", type=int, default=200,
                   help="DPI for page image artifacts (default: 200)")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.suite and not getattr(args, "case", None):
        parser.print_help()
        return 1

    return cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
