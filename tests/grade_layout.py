#!/usr/bin/env python3
"""Deterministic layout grading CLI.

Discovers matched (template, classification, generation) triples by numeric
filename prefix, renders missing artefacts via the existing rendering pipeline,
then grades layout preservation for each sample.

Supports both DOCX-origin and PDF-origin pipelines.  By default both are graded
and output is interleaved per sample (DOCX grade then PDF grade for each prefix).
Use --docx-only or --pdf-only to scope to a single pipeline.

Usage:
    python tests/grade_layout.py                          # all matched samples (both pipelines)
    python tests/grade_layout.py --docx-only              # DOCX-origin only
    python tests/grade_layout.py --pdf-only               # PDF-origin only
    python tests/grade_layout.py 31                       # sample with prefix 31
    python tests/grade_layout.py 5 6 7 10 15             # multiple prefixes
    python tests/grade_layout.py 1-Leonid                 # match by fragment
    python tests/grade_layout.py --baseline path/to/aggregate.json
    python tests/grade_layout.py --no-render 31           # skip rendering step
    python tests/grade_layout.py --pdf-method local       # force PDF method

Output (all under tmp/artefacts/layout_grading/):
    docx/<stem>_grade.json   per-sample DOCX-origin grade JSON
    pdf/<stem>_grade.json    per-sample PDF-origin grade JSON
    aggregate.json           aggregate metrics + full grades array
    summary.txt              human-readable sorted report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TESTS = _REPO / "tests"
_SRC = _REPO / "src"

if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
if str(_TESTS) not in sys.path:
    sys.path.insert(0, str(_TESTS))

from tailor.eval.layout_grader.grader import SampleGrade, _detect_pdf_method, grade_sample
from tailor.eval.layout_grader.report import build_aggregate, compare_baseline, write_summary_txt


def _ev_severity(msg: str) -> int:
    """Sort key for evidence messages: 0=HARD FAIL, 1=WARN, 2=INFO."""
    if "HARD FAIL" in msg:
        return 0
    if "suppressed" in msg or "well-preserved" in msg or "No Summary" in msg:
        return 2
    return 1


def _ev_tag(msg: str) -> str:
    return ("[HARD FAIL]", "[WARN]", "[INFO]")[_ev_severity(msg)]

_SAMPLES = _TESTS / "samples"

# DOCX-origin paths
_CLS_DOCX_DIR = _SAMPLES / "classification" / "docx"
_GEN_DIR = _SAMPLES / "generation"
_RES_DOCX_DIR = _SAMPLES / "resume" / "docx"
_IR_DOCX_DIR = _REPO / "tmp" / "artefacts" / "ir" / "docx"
_REND_DOCX_DIR = _REPO / "tmp" / "artefacts" / "rendering" / "docx"

# PDF-origin paths
_CLS_PDF_DIR = _SAMPLES / "classification" / "pdf"
_RES_PDF_DIR = _SAMPLES / "resume" / "pfd"   # note: legacy typo preserved
_IR_PDF_DIR = _REPO / "tmp" / "artefacts" / "ir" / "pdf"
_REND_PDF_DIR = _REPO / "tmp" / "artefacts" / "rendering" / "pdf"

# Grading output paths
_GRADE_DIR = _REPO / "tmp" / "artefacts" / "layout_grading"
_GRADE_DOCX_DIR = _GRADE_DIR / "docx"    # DOCX-origin grade JSONs
_GRADE_PDF_DIR = _GRADE_DIR / "pdf"      # PDF-origin grade JSONs
_TEMPLATE_PDF_DIR = _GRADE_DIR / "template_pdf"
# Generated PDFs for grading — kept separate per pipeline so stems don't collide
_GRADE_DOCX_PDF_DIR = _GRADE_DIR / "generated_pdf"      # DOCX-origin generated PDFs
_GRADE_PDF_PDF_DIR = _GRADE_DIR / "pdf_generated_pdf"   # PDF-origin generated PDFs

_NUM_RE = re.compile(r"^(\d+)-")


def _num_prefix(name: str) -> str | None:
    m = _NUM_RE.match(name)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Discovery — DOCX-origin
# ---------------------------------------------------------------------------

class _Triple:
    __slots__ = ("prefix", "stem", "template_docx", "cls_path", "gen_path")

    def __init__(self, prefix, stem, template_docx, cls_path, gen_path):
        self.prefix = prefix
        self.stem = stem
        self.template_docx: Path = template_docx
        self.cls_path: Path = cls_path
        self.gen_path: Path = gen_path


def discover(filter_args: list[str] | None = None) -> tuple[list[_Triple], list[str]]:
    """Return (matched_triples, skip_messages) for DOCX-origin samples.

    Requirements for a match: template DOCX, classification JSON, and
    generation JSON all exist.  Rendered artefacts (DOCX + IR) are checked
    separately and generated on demand.
    """
    gen_index: dict[str, Path] = {}
    for p in sorted(_GEN_DIR.glob("*.json")):
        n = _num_prefix(p.name)
        if n and n not in gen_index:
            gen_index[n] = p

    cls_index: dict[str, Path] = {}
    if _CLS_DOCX_DIR.is_dir():
        for p in sorted(_CLS_DOCX_DIR.glob("*_input.json")):
            n = _num_prefix(p.name)
            if n and n not in cls_index:
                cls_index[n] = p

    triples: list[_Triple] = []
    skips: list[str] = []

    for n in sorted(gen_index, key=int):
        gen_path = gen_index[n]

        if n not in cls_index:
            skips.append(f"[{n}] skip — no DOCX classification file")
            continue

        cls_path = cls_index[n]
        resume_stem = re.sub(r"_input$", "", cls_path.stem)
        template_docx = _RES_DOCX_DIR / (resume_stem + ".docx")
        if not template_docx.exists():
            skips.append(f"[{n}] skip — template DOCX not found: {resume_stem}.docx")
            continue

        triples.append(_Triple(n, resume_stem, template_docx, cls_path, gen_path))

    if filter_args:
        numeric = {a for a in filter_args if a.isdigit()}
        frags = [a.lower() for a in filter_args if not a.isdigit()]

        if numeric:
            triples = [t for t in triples if t.prefix in numeric]
            skips = [s for s in skips if any(f"[{n}]" in s for n in numeric)]
        if frags:
            triples = [
                t for t in triples
                if any(f in t.stem.lower() or f in t.gen_path.name.lower() for f in frags)
            ]

    return triples, skips


# ---------------------------------------------------------------------------
# Discovery — PDF-origin
# ---------------------------------------------------------------------------

class _PDFTriple:
    __slots__ = ("prefix", "stem", "template_pdf", "cls_path", "gen_path")

    def __init__(self, prefix, stem, template_pdf, cls_path, gen_path):
        self.prefix = prefix
        self.stem = stem
        self.template_pdf: Path = template_pdf
        self.cls_path: Path = cls_path
        self.gen_path: Path = gen_path


def discover_pdf(filter_args: list[str] | None = None) -> tuple[list[_PDFTriple], list[str]]:
    """Return (matched_triples, skip_messages) for PDF-origin samples."""
    gen_index: dict[str, Path] = {}
    for p in sorted(_GEN_DIR.glob("*.json")):
        n = _num_prefix(p.name)
        if n and n not in gen_index:
            gen_index[n] = p

    cls_index: dict[str, Path] = {}
    if _CLS_PDF_DIR.is_dir():
        for p in sorted(_CLS_PDF_DIR.glob("*_input.json")):
            n = _num_prefix(p.name)
            if n and n not in cls_index:
                cls_index[n] = p

    triples: list[_PDFTriple] = []
    skips: list[str] = []

    for n in sorted(gen_index, key=int):
        gen_path = gen_index[n]

        if n not in cls_index:
            skips.append(f"[{n}] skip — no PDF classification file")
            continue

        cls_path = cls_index[n]
        resume_stem = re.sub(r"_input$", "", cls_path.stem)
        template_pdf = _RES_PDF_DIR / (resume_stem + ".pdf")
        if not template_pdf.exists():
            skips.append(f"[{n}] skip — template PDF not found: {resume_stem}.pdf")
            continue

        triples.append(_PDFTriple(n, resume_stem, template_pdf, cls_path, gen_path))

    if filter_args:
        numeric = {a for a in filter_args if a.isdigit()}
        frags = [a.lower() for a in filter_args if not a.isdigit()]

        if numeric:
            triples = [t for t in triples if t.prefix in numeric]
            skips = [s for s in skips if any(f"[{n}]" in s for n in numeric)]
        if frags:
            triples = [
                t for t in triples
                if any(f in t.stem.lower() or f in t.gen_path.name.lower() for f in frags)
            ]

    return triples, skips


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _ensure_rendering(triple: _Triple) -> bool:
    """Run the DOCX-origin rendering pipeline for this triple if artefacts are missing."""
    rend_docx = _REND_DOCX_DIR / (triple.stem + ".docx")
    ir_json = _IR_DOCX_DIR / (triple.stem + "_IR.json")

    if rend_docx.exists() and ir_json.exists():
        return True

    print(f"  [render] Rendering [DOCX/{triple.prefix}] {triple.stem} …")
    try:
        from rendering.render_samples import main as render_main
        rc = render_main(["--no-screenshots", "--docx-only", triple.prefix])
        ok = rc == 0
        if not ok:
            print(f"  [render] FAILED (exit {rc})")
        return ok
    except Exception as exc:
        print(f"  [render] ERROR: {exc}")
        traceback.print_exc()
        return False


def _ensure_pdf_rendering(triple: _PDFTriple) -> bool:
    """Run the PDF-origin rendering pipeline for this triple if artefacts are missing."""
    rend_docx = _REND_PDF_DIR / (triple.stem + ".docx")
    ir_json = _IR_PDF_DIR / (triple.stem + "_IR.json")

    if rend_docx.exists() and ir_json.exists():
        return True

    print(f"  [render] Rendering [PDF/{triple.prefix}] {triple.stem} …")
    try:
        from rendering.render_samples import main as render_main
        rc = render_main(["--no-screenshots", "--pdf-only", triple.prefix])
        ok = rc == 0
        if not ok:
            print(f"  [render] FAILED (exit {rc})")
        return ok
    except Exception as exc:
        print(f"  [render] ERROR: {exc}")
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Grade layout preservation for matched samples (DOCX and PDF pipelines)"
    )
    parser.add_argument(
        "filter", nargs="*",
        help="Numeric prefix(es) or filename fragment(s) to narrow results (e.g. 5 6 7 or Leonid)"
    )
    parser.add_argument(
        "--baseline",
        help="Path to a previous aggregate.json for regression detection"
    )
    parser.add_argument(
        "--no-render", action="store_true",
        help="Skip rendering step (grade only samples with existing artefacts)"
    )
    parser.add_argument(
        "--pdf-method", choices=["subprocess", "docker", "local"],
        help="PDF conversion method (auto-detected if omitted)"
    )
    parser.add_argument(
        "--docx-only", action="store_true",
        help="Grade only DOCX-origin samples"
    )
    parser.add_argument(
        "--pdf-only", action="store_true",
        help="Grade only PDF-origin samples"
    )
    args = parser.parse_args(argv)

    pdf_method = args.pdf_method or _detect_pdf_method()
    run_docx = not args.pdf_only
    run_pdf = not args.docx_only

    print("=" * 60)
    print("  grade_layout.py — deterministic layout grading")
    print("=" * 60)
    print(f"  PDF method: {pdf_method}")
    if args.docx_only:
        print("  Pipeline:   DOCX-origin only")
    elif args.pdf_only:
        print("  Pipeline:   PDF-origin only")
    else:
        print("  Pipeline:   DOCX + PDF (interleaved)")

    filter_args = args.filter or None

    docx_triples: list[_Triple] = []
    pdf_triples: list[_PDFTriple] = []

    if run_docx:
        docx_triples, docx_skips = discover(filter_args)
        if docx_skips:
            print(f"\n[DOCX] Skipped ({len(docx_skips)}):")
            for s in docx_skips:
                print(f"  {s}")

    if run_pdf:
        pdf_triples, pdf_skips = discover_pdf(filter_args)
        if pdf_skips:
            print(f"\n[PDF] Skipped ({len(pdf_skips)}):")
            for s in pdf_skips:
                print(f"  {s}")

    if not docx_triples and not pdf_triples:
        print("\nNo matched samples to grade.")
        return 1

    total = len(docx_triples) + len(pdf_triples)
    print(f"\nGrading {total} sample(s) …\n")

    _GRADE_DIR.mkdir(parents=True, exist_ok=True)
    _GRADE_DOCX_DIR.mkdir(parents=True, exist_ok=True)
    _GRADE_PDF_DIR.mkdir(parents=True, exist_ok=True)
    _TEMPLATE_PDF_DIR.mkdir(parents=True, exist_ok=True)
    _GRADE_DOCX_PDF_DIR.mkdir(parents=True, exist_ok=True)
    _GRADE_PDF_PDF_DIR.mkdir(parents=True, exist_ok=True)

    # Build per-prefix lookup for interleaved output
    docx_by_prefix: dict[str, _Triple] = {t.prefix: t for t in docx_triples}
    pdf_by_prefix: dict[str, _PDFTriple] = {t.prefix: t for t in pdf_triples}
    all_prefixes = sorted(
        set(docx_by_prefix) | set(pdf_by_prefix), key=int
    )

    docx_grades: list[SampleGrade] = []
    pdf_grades: list[SampleGrade] = []

    for prefix in all_prefixes:
        # ── DOCX-origin ───────────────────────────────────────────────────────
        if prefix in docx_by_prefix:
            triple = docx_by_prefix[prefix]
            n, stem = triple.prefix, triple.stem
            print(f"[DOCX] [{n}] {stem}")

            if not args.no_render:
                _ensure_rendering(triple)

            rend_docx = _REND_DOCX_DIR / (stem + ".docx")
            rend_pdf = _GRADE_DOCX_PDF_DIR / (stem + ".pdf")
            template_pdf = _TEMPLATE_PDF_DIR / (stem + ".pdf")
            ir_json = _IR_DOCX_DIR / (stem + "_IR.json")

            if not rend_docx.exists():
                print(f"  SKIP — generated DOCX not found")
            elif not ir_json.exists():
                print(f"  SKIP — IR not found")
            else:
                try:
                    grade = grade_sample(
                        sample_id=stem,
                        template_docx_path=str(triple.template_docx),
                        generated_docx_path=str(rend_docx),
                        template_pdf_path=str(template_pdf),
                        generated_pdf_path=str(rend_pdf),
                        ir_path=str(ir_json),
                        gen_json_path=str(triple.gen_path),
                        pdf_method=pdf_method,
                        origin="docx",
                    )
                except Exception as exc:
                    print(f"  ERROR: {exc}")
                    traceback.print_exc()
                    grade = None

                if grade:
                    grade_file = _GRADE_DOCX_DIR / f"{stem}_grade.json"
                    grade_file.write_text(
                        json.dumps(grade.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    flag = " [HARD FAIL]" if grade.hard_fail else ""
                    print(f"  score={grade.composite_score:.1f}  {grade.status}{flag}")
                    sorted_ev = sorted(grade.evidence, key=_ev_severity)[:4]
                    for ev in sorted_ev:
                        safe_ev = ev.encode("ascii", "replace").decode("ascii")
                        print(f"         {_ev_tag(safe_ev)} {safe_ev}")
                    docx_grades.append(grade)

        # ── PDF-origin ────────────────────────────────────────────────────────
        if prefix in pdf_by_prefix:
            triple = pdf_by_prefix[prefix]
            n, stem = triple.prefix, triple.stem
            print(f"[PDF]  [{n}] {stem}")

            if not args.no_render:
                _ensure_pdf_rendering(triple)

            rend_docx = _REND_PDF_DIR / (stem + ".docx")
            rend_pdf = _GRADE_PDF_PDF_DIR / (stem + ".pdf")
            ir_json = _IR_PDF_DIR / (stem + "_IR.json")

            if not rend_docx.exists():
                print(f"  SKIP — generated DOCX not found")
            elif not ir_json.exists():
                print(f"  SKIP — IR not found")
            else:
                try:
                    grade = grade_sample(
                        sample_id=stem,
                        template_docx_path=str(triple.template_pdf),
                        generated_docx_path=str(rend_docx),
                        template_pdf_path=str(triple.template_pdf),
                        generated_pdf_path=str(rend_pdf),
                        ir_path=str(ir_json),
                        gen_json_path=str(triple.gen_path),
                        pdf_method=pdf_method,
                        origin="pdf",
                    )
                except Exception as exc:
                    print(f"  ERROR: {exc}")
                    traceback.print_exc()
                    grade = None

                if grade:
                    grade_file = _GRADE_PDF_DIR / f"{stem}_grade.json"
                    grade_file.write_text(
                        json.dumps(grade.to_dict(), indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    flag = " [HARD FAIL]" if grade.hard_fail else ""
                    print(f"  score={grade.composite_score:.1f}  {grade.status}{flag}")
                    sorted_ev = sorted(grade.evidence, key=_ev_severity)[:4]
                    for ev in sorted_ev:
                        safe_ev = ev.encode("ascii", "replace").decode("ascii")
                        print(f"         {_ev_tag(safe_ev)} {safe_ev}")
                    pdf_grades.append(grade)

    all_grades = docx_grades + pdf_grades
    if not all_grades:
        print("\nNo samples graded.")
        return 1

    # ── Aggregate report ──────────────────────────────────────────────────────
    regressions: list[dict] = []
    if args.baseline:
        regressions = compare_baseline(all_grades, args.baseline)
        if regressions:
            print(f"\nRegressions vs baseline ({len(regressions)}):")
            for r in regressions:
                print(
                    f"  [{r['regression_class']}] {r['sample_id']}: "
                    f"{r['score_before']:.1f}→{r['score_after']:.1f} "
                    f"(Δ{r['score_delta']:+.1f})"
                )

    aggregate = build_aggregate(all_grades)
    agg_data = {**aggregate, "grades": [g.to_dict() for g in all_grades]}
    (_GRADE_DIR / "aggregate.json").write_text(
        json.dumps(agg_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    write_summary_txt(all_grades, aggregate, _GRADE_DIR / "summary.txt", regressions)

    # ── Console summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if run_docx and docx_grades:
        docx_agg = build_aggregate(docx_grades)
        print(
            f"  [DOCX] {docx_agg['pass_count']} PASS | "
            f"{docx_agg['warning_count']} WARNING | "
            f"{docx_agg['fail_count']} FAIL | "
            f"{docx_agg['hard_fail_count']} HARD FAIL"
        )
        print(
            f"         avg={docx_agg['average_score']:.1f}  "
            f"median={docx_agg['median_score']:.1f}  "
            f"min={docx_agg['min_score']:.1f}"
        )
    if run_pdf and pdf_grades:
        pdf_agg = build_aggregate(pdf_grades)
        print(
            f"  [PDF]  {pdf_agg['pass_count']} PASS | "
            f"{pdf_agg['warning_count']} WARNING | "
            f"{pdf_agg['fail_count']} FAIL | "
            f"{pdf_agg['hard_fail_count']} HARD FAIL"
        )
        print(
            f"         avg={pdf_agg['average_score']:.1f}  "
            f"median={pdf_agg['median_score']:.1f}  "
            f"min={pdf_agg['min_score']:.1f}"
        )
    if run_docx and run_pdf and docx_grades and pdf_grades:
        print(
            f"  [ALL]  {aggregate['pass_count']} PASS | "
            f"{aggregate['warning_count']} WARNING | "
            f"{aggregate['fail_count']} FAIL | "
            f"{aggregate['hard_fail_count']} HARD FAIL"
        )
    print("=" * 60)
    print(f"\nReports -> {_GRADE_DIR}/")
    if run_docx and docx_grades:
        print(f"  DOCX grades -> {_GRADE_DOCX_DIR}/")
    if run_pdf and pdf_grades:
        print(f"  PDF grades  -> {_GRADE_PDF_DIR}/")

    n_fail = aggregate["fail_count"] + aggregate["hard_fail_count"]
    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
