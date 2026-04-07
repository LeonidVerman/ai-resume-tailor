"""Benchmark harness for changed-content DOCX layout evaluation.

Each BenchmarkCase specifies a source DOCX template and the LLM-generated
content to inject.  run_case() orchestrates the full pipeline:

  source DOCX
    → compile_resume (inject generated content)
    → output DOCX
    → docx_to_pdf (both source and output)
    → extractor.extract (both PDFs)
    → score_layout + classify_failures
    → render_page_artifacts
    → CaseResult

run_suite() runs a list of cases and collects CaseResult objects.

Reuse inventory
---------------
- compile_resume()          tailor.compiler.pipeline        (unchanged)
- docx_to_pdf()             tailor.docx.pdf                 (unchanged)
- extractor.extract()       tailor.eval.extractor           (unchanged)
- render_page_artifacts()   tailor.eval.visualizer          (unchanged)
- parse_docx()              tailor.compiler.docx_parser     (source text)
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkCase:
    """Input specification for a single changed-content benchmark case.

    Exactly one of {generated_resume_text, generated_resume_text_file,
    generated_debug_json} must be provided.
    """

    case_id: str
    source_docx: str                        # path to source/template DOCX

    # Generated content sources (use exactly one)
    generated_resume_text: str = ""         # inline plain text
    generated_resume_text_file: str = ""    # path to .txt file
    generated_debug_json: str = ""          # path to debug JSON (extracts "resume" field)

    template_class_hint: str = "linear"     # linear | multi_section | table_sidebar
    severity: str = "S2"                    # S0 | S1 | S2 | S3 | S4
    notes: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkCase":
        return cls(
            case_id=d["case_id"],
            source_docx=d["source_docx"],
            generated_resume_text=d.get("generated_resume_text", ""),
            generated_resume_text_file=d.get("generated_resume_text_file", ""),
            generated_debug_json=d.get("generated_debug_json", ""),
            template_class_hint=d.get("template_class_hint", "linear"),
            severity=d.get("severity", "S2"),
            notes=d.get("notes", ""),
        )


@dataclass
class CaseResult:
    """Output from running one benchmark case."""

    case_id: str
    template_class: str
    severity: str
    layout_score: float                     # composite 0.0–1.0
    metric_breakdown: dict = field(default_factory=dict)
    failure_classes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    artifacts: dict = field(default_factory=dict)
    notes: str = ""
    error: str = ""

    @property
    def status(self) -> str:
        if self.error:
            return "error"
        return "pass" if self.layout_score >= 0.70 else "fail"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_generated_text(case: BenchmarkCase) -> str:
    if case.generated_resume_text:
        return case.generated_resume_text

    if case.generated_resume_text_file:
        p = Path(case.generated_resume_text_file)
        if not p.exists():
            raise FileNotFoundError(f"generated_resume_text_file not found: {p}")
        return p.read_text(encoding="utf-8")

    if case.generated_debug_json:
        p = Path(case.generated_debug_json)
        if not p.exists():
            raise FileNotFoundError(f"generated_debug_json not found: {p}")
        data = json.loads(p.read_bytes())
        # Support both top-level "resume" and nested "llm_response.resume"
        resume = data.get("resume") or (data.get("llm_response") or {}).get("resume")
        if not resume:
            raise ValueError(
                f"No 'resume' field in debug JSON: {p}\n"
                f"Available keys: {list(data.keys())}"
            )
        return resume

    raise ValueError(
        f"BenchmarkCase '{case.case_id}' has no generated text. "
        "Provide one of: generated_resume_text, generated_resume_text_file, "
        "generated_debug_json."
    )


def _read_docx_text(docx_path: str) -> str:
    """Extract plain text from a DOCX via the compiler IR (for duplication detection)."""
    from tailor.compiler.docx_parser import parse_docx

    doc = parse_docx(docx_path)
    return "\n".join(p.text for p in doc.all_paras if p.text.strip())


# Semantic types that are never modified by the LLM — their content is always
# preserved verbatim and should not count as "stale" in duplication analysis.
_LOCKED_SECTION_TYPES: frozenset[str] = frozenset({
    "education", "certifications", "languages", "websites",
})


def _read_editable_source_text(docx_path: str) -> str:
    """Extract plain text from non-locked sections of a DOCX.

    Excludes education, certifications, languages, and websites sections whose
    content is always preserved verbatim and would otherwise inflate the stale-
    content ratio in the duplication scorer.
    """
    from tailor.compiler.docx_parser import parse_docx

    doc = parse_docx(docx_path)
    lines: list[str] = []
    lines.extend(p.text for p in doc.header_paras if p.text.strip())
    for section in doc.sections:
        if section.semantic_type in _LOCKED_SECTION_TYPES:
            continue
        if section.heading.text.strip():
            lines.append(section.heading.text)
        lines.extend(p.text for p in section.body_paras if p.text.strip())
    return "\n".join(lines)


def _convert_docx_to_pdf(src_docx: str, dest_pdf: str, lo_method: str) -> None:
    """Convert *src_docx* to PDF at *dest_pdf*.

    Copies the DOCX into a temp working directory so docx_to_pdf() can write
    the PDF next to the input without polluting the source directory.
    """
    from tailor.docx.pdf import docx_to_pdf

    work_dir = os.path.dirname(dest_pdf)
    stem = Path(dest_pdf).stem
    work_docx = os.path.join(work_dir, stem + ".docx")

    shutil.copy2(src_docx, work_docx)
    try:
        docx_to_pdf(work_docx, method=lo_method)
        generated = os.path.splitext(work_docx)[0] + ".pdf"
        if not os.path.exists(generated):
            raise RuntimeError(
                f"docx_to_pdf did not produce a PDF at {generated}"
            )
        if generated != dest_pdf:
            shutil.move(generated, dest_pdf)
    finally:
        # Remove the working DOCX copy (keep only the PDF)
        if os.path.exists(work_docx) and work_docx != src_docx:
            os.remove(work_docx)


def _score_to_dict(score: "LayoutScore") -> dict:  # noqa: F821
    from dataclasses import asdict
    return asdict(score)


def _flatten_evidence(fc: "FailureClassification", score_ev: list[str]) -> list[str]:  # noqa: F821
    out: list[str] = list(score_ev)
    for cls, reasons in fc.evidence.items():
        for r in reasons:
            entry = f"[{cls}] {r}"
            if entry not in out:
                out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_case(
    case: BenchmarkCase,
    output_dir: str,
    lo_method: str = "subprocess",
    dpi: int = 200,
) -> CaseResult:
    """Run one changed-content benchmark case end-to-end.

    Writes all artifacts (DOCX, PDFs, images, report JSON) to
    ``output_dir/<case_id>/``.

    Parameters
    ----------
    case:       The benchmark case to evaluate.
    output_dir: Root directory for all run output.
    lo_method:  LibreOffice conversion method (subprocess | docker | local).
    dpi:        DPI for page-image artifacts.
    """
    from tailor.compiler.pipeline import compile_resume
    from tailor.eval.extractor import extract
    from tailor.eval.visualizer import render_page_artifacts
    from tailor.eval.changed_content.scorer import score_layout
    from tailor.eval.changed_content.taxonomy import classify_failures
    from tailor.eval.changed_content.report import build_case_report, write_case_report

    case_dir = os.path.join(output_dir, case.case_id)
    os.makedirs(case_dir, exist_ok=True)

    output_docx = os.path.join(case_dir, f"{case.case_id}_output.docx")
    src_pdf     = os.path.join(case_dir, "source.pdf")
    out_pdf     = os.path.join(case_dir, "output.pdf")

    try:
        # 1. Load generated text
        generated_text = _load_generated_text(case)

        # 2. Compile: source DOCX + generated text → output DOCX
        compile_resume(case.source_docx, generated_text, output_docx)

        # 3. Render both DOCX files to PDF
        _convert_docx_to_pdf(case.source_docx, src_pdf, lo_method)
        _convert_docx_to_pdf(output_docx, out_pdf, lo_method)

        # 4. Extract layout from both PDFs
        src_extracted = extract(src_pdf)
        out_extracted = extract(out_pdf)

        # 5. Score layout (layout-only metrics; text F1 intentionally excluded)
        # Use editable-sections-only text so locked sections (education,
        # certifications, languages, websites) don't inflate the stale-content ratio.
        source_text = _read_editable_source_text(case.source_docx)
        layout_score, score_evidence = score_layout(
            src_extracted, out_extracted, source_text, generated_text
        )

        # 6. Classify failures
        failure_cls = classify_failures(layout_score)

        # 7. Visual artifacts (reuse same-text visualizer unchanged)
        artifacts_list = render_page_artifacts(src_pdf, out_pdf, case_dir, dpi=dpi)

        result = CaseResult(
            case_id=case.case_id,
            template_class=case.template_class_hint,
            severity=case.severity,
            layout_score=layout_score.composite,
            metric_breakdown=_score_to_dict(layout_score),
            failure_classes=failure_cls.classes,
            evidence=_flatten_evidence(failure_cls, score_evidence),
            artifacts={
                "case_dir": case_dir,
                "source_docx": case.source_docx,
                "output_docx": output_docx,
                "source_pdf": src_pdf,
                "output_pdf": out_pdf,
                "visuals": artifacts_list,
            },
            notes=case.notes,
        )

    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        err_file = os.path.join(case_dir, "error.txt")
        with open(err_file, "w", encoding="utf-8") as fh:
            fh.write(tb)
        result = CaseResult(
            case_id=case.case_id,
            template_class=case.template_class_hint,
            severity=case.severity,
            layout_score=0.0,
            failure_classes=[],
            evidence=[f"ERROR: {exc}"],
            artifacts={"case_dir": case_dir, "error_file": err_file},
            notes=case.notes,
            error=str(exc),
        )

    # Always write per-case report
    report = build_case_report(result)
    write_case_report(report, case_dir)
    return result


def run_suite(
    cases: list[BenchmarkCase],
    output_dir: str,
    lo_method: str = "subprocess",
    dpi: int = 200,
) -> list[CaseResult]:
    """Run all cases in a benchmark suite, returning one CaseResult per case."""
    from tailor.eval.changed_content.report import build_suite_report, write_suite_report

    suite_dir = output_dir
    os.makedirs(suite_dir, exist_ok=True)

    results: list[CaseResult] = []
    for case in cases:
        print(f"  [{case.case_id}] template={case.template_class_hint} severity={case.severity}")
        result = run_case(case, suite_dir, lo_method=lo_method, dpi=dpi)
        status = result.status.upper()
        score = f"{result.layout_score:.3f}"
        fc = ",".join(result.failure_classes) if result.failure_classes else "none"
        print(f"  [{case.case_id}] {status}  score={score}  failures=[{fc}]")
        results.append(result)

    # Write aggregate suite report
    suite_report = build_suite_report(results)
    write_suite_report(suite_report, suite_dir)
    return results


def load_suite_from_file(path: str) -> tuple[str, list[BenchmarkCase]]:
    """Load a benchmark suite from a JSON file.

    Expected format::

        {
          "suite_id": "cc_eval_v1",
          "cases": [ { ...BenchmarkCase fields... }, ... ]
        }

    Returns (suite_id, cases).
    """
    data = json.loads(Path(path).read_bytes())
    suite_id = data.get("suite_id", Path(path).stem)
    cases = [BenchmarkCase.from_dict(c) for c in data.get("cases", [])]
    return suite_id, cases
