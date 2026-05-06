"""Composite layout grader — orchestrates three evaluation dimensions.

Dimensions and weights:
  ir_score                15%   IR structural correctness
  content_injection_score 20%   LLM output actually applied
  page_count_score        10%   page growth
  blank_page_score        10%   blank-page detection
  region_score            20%   section order + region + column consistency
  container_score         10%   content-area expansion (overflow)
  density_score           15%   bullet/paragraph density from IR

Classification:
  HARD FAIL: composite capped at 30, status = 'hard_fail'
  PASS      composite >= 75
  WARNING   60-74
  FAIL      < 60

HARD FAIL triggers:
  IR:       EMPTY_PARA_ID, EXPERIENCE_NO_ROLES
  DOCX:     columns_lost (w:cols dropped)
  PDF:      page count > original + 2
            blank middle page
            column layout lost (PDF-detected)
  Content:  lorem ipsum detected, extreme template similarity
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

_WEIGHTS: dict[str, float] = {
    "ir_score": 0.15,
    "content_injection_score": 0.20,
    "page_count_score": 0.10,
    "blank_page_score": 0.10,
    "region_score": 0.20,
    "container_score": 0.10,
    "density_score": 0.15,
}

# Defaults used when PDF conversion or content injection check is unavailable
_PDF_DEFAULTS: dict[str, float] = {
    "page_count_score": 80.0,
    "blank_page_score": 100.0,
    "region_score": 80.0,
    "container_score": 80.0,
    "density_score": 80.0,
}
_CONTENT_INJECTION_DEFAULT = 80.0


@dataclass
class SampleGrade:
    sample_id: str
    status: str          # pass | warning | fail | hard_fail
    composite_score: float
    hard_fail: bool
    hard_fail_reasons: list[str]
    metrics: dict
    facts: dict
    failure_classes: list[str]
    evidence: list[str]

    def to_dict(self) -> dict:
        return {
            "sample_id": self.sample_id,
            "status": self.status,
            "composite_score": self.composite_score,
            "hard_fail": self.hard_fail,
            "hard_fail_reasons": self.hard_fail_reasons,
            "metrics": self.metrics,
            "facts": self.facts,
            "failure_classes": self.failure_classes,
            "evidence": self.evidence,
        }


# ---------------------------------------------------------------------------
# PDF conversion helper
# ---------------------------------------------------------------------------

def _detect_pdf_method() -> str:
    import os
    env = os.environ.get("GRADE_PDF_METHOD", "").strip().lower()
    if env in ("subprocess", "docker", "local"):
        return env
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return "subprocess"
    return "local"


def _ensure_pdf(docx_path: str, dest_pdf: str, method: str) -> bool:
    """Convert docx_path to a PDF at dest_pdf.  Reuses existing file."""
    dest = Path(dest_pdf)
    if dest.exists():
        return True
    try:
        from tailor.docx.pdf import docx_to_pdf

        dest.parent.mkdir(parents=True, exist_ok=True)
        work_docx = dest.with_suffix(".docx")
        shutil.copy2(docx_path, work_docx)
        try:
            docx_to_pdf(str(work_docx), method=method)
            produced = work_docx.with_suffix(".pdf")
            if produced.exists():
                if produced != dest:
                    shutil.move(str(produced), str(dest))
                return dest.exists()
            return False
        finally:
            if work_docx.exists() and str(work_docx) != docx_path:
                work_docx.unlink(missing_ok=True)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Public grading function
# ---------------------------------------------------------------------------

def grade_sample(
    sample_id: str,
    template_docx_path: str,
    generated_docx_path: str,
    template_pdf_path: str,
    generated_pdf_path: str,
    ir_path: str,
    gen_json_path: str | None = None,
    pdf_method: str | None = None,
) -> SampleGrade:
    """Grade one sample across all three evaluation dimensions.

    Parameters
    ----------
    sample_id:            Human-readable identifier.
    template_docx_path:   Original template DOCX.
    generated_docx_path:  DOCX produced by the rendering pipeline.
    template_pdf_path:    Destination for the template PDF (created if absent).
    generated_pdf_path:   Destination for the generated PDF (created if absent).
    ir_path:              Path to the generated IR JSON (*_IR.json).
    gen_json_path:        Optional path to the generation JSON; enables the
                          content-injection check (llm_response.resume).
    pdf_method:           'subprocess' | 'docker' | 'local' | None (auto).
    """
    from .ir_validator import validate_ir, ir_score
    from .docx_comparator import compare_docx_structure
    from .content_injection import check_content_injection
    from .pdf_scorer import score_pdf_visual

    if pdf_method is None:
        pdf_method = _detect_pdf_method()

    hard_fail = False
    hard_fail_reasons: list[str] = []
    failure_classes: list[str] = []
    evidence: list[str] = []

    # ── Load rendered IR ──────────────────────────────────────────────────────
    ir: dict = {}
    try:
        ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
    except Exception as exc:
        hard_fail = True
        hard_fail_reasons.append("IR_LOAD_FAILED")
        evidence.append(f"Could not load IR: {exc}")

    # ── Dimension 1: IR Integrity ─────────────────────────────────────────────
    ir_result = validate_ir(ir)
    ir_s = ir_score(ir_result)
    if ir_result.hard_fail:
        hard_fail = True
        hard_fail_reasons.extend(ir_result.failures)
        failure_classes.append("A_IR_CORRUPTION")
    elif ir_result.failures:
        failure_classes.append("A_IR_SOFT")
    evidence.extend(ir_result.evidence[:5])

    # ── DOCX structure check (column loss, renderer fallback) ─────────────────
    docx_result = None
    paragraph_ratio = 1.0
    try:
        docx_result = compare_docx_structure(template_docx_path, generated_docx_path)
        paragraph_ratio = docx_result.paragraph_ratio
        if docx_result.columns_lost:
            hard_fail = True
            hard_fail_reasons.append("COLUMNS_LOST")
            failure_classes.append("F_TOPOLOGY_COLLAPSE")
        if docx_result.renderer_fallback and "C_OVERFLOW_FIT" not in failure_classes:
            failure_classes.append("C_OVERFLOW_FIT")
        evidence.extend(docx_result.evidence[:3])
    except Exception as exc:
        hard_fail = True
        hard_fail_reasons.append("DOCX_COMPARE_FAILED")
        evidence.append(f"DOCX comparison failed: {exc}")

    # ── Dimension 2: Content Injection ────────────────────────────────────────
    ci_score = _CONTENT_INJECTION_DEFAULT
    ci_sim_llm = None
    ci_sim_template = None

    if gen_json_path:
        try:
            gen_data = json.loads(Path(gen_json_path).read_text(encoding="utf-8"))
            llm_text: str = gen_data.get("llm_response", {}).get("resume", "") or ""
            if llm_text.strip():
                ci_result = check_content_injection(llm_text, ir, template_docx_path)
                ci_score = ci_result.score
                ci_sim_llm = ci_result.sim_llm
                ci_sim_template = ci_result.sim_template
                if ci_result.hard_fail:
                    hard_fail = True
                    hard_fail_reasons.append("CONTENT_NOT_INJECTED")
                    failure_classes.append("E_CONTENT_INJECTION")
                elif ci_result.evidence and ci_score < 75:
                    failure_classes.append("E_INJECTION_PARTIAL")
                evidence.extend(ci_result.evidence[:3])
        except Exception as exc:
            evidence.append(f"Content injection check failed: {exc}")

    # ── Dimension 3: PDF Visual Layout ────────────────────────────────────────
    pdf_scores: dict[str, float] = dict(_PDF_DEFAULTS)
    orig_pages = 0
    gen_pages_count = 0
    orig_columns = 1
    gen_columns_count = 1

    template_pdf_ok = _ensure_pdf(template_docx_path, template_pdf_path, pdf_method)
    generated_pdf_ok = _ensure_pdf(generated_docx_path, generated_pdf_path, pdf_method)

    if template_pdf_ok and generated_pdf_ok:
        try:
            pdf_result = score_pdf_visual(
                template_pdf_path, generated_pdf_path, ir or None
            )
            pdf_scores = {
                "page_count_score": pdf_result.page_count_score,
                "blank_page_score": pdf_result.blank_page_score,
                "region_score": pdf_result.region_score,
                "container_score": pdf_result.container_score,
                "density_score": pdf_result.density_score,
            }
            orig_pages = pdf_result.original_pages
            gen_pages_count = pdf_result.generated_pages
            orig_columns = pdf_result.orig_columns
            gen_columns_count = pdf_result.gen_columns
            if pdf_result.hard_fail:
                hard_fail = True
                hard_fail_reasons.extend(pdf_result.hard_fail_reasons)
            if "PAGE_COUNT_OVERFLOW" in pdf_result.hard_fail_reasons:
                if "C_OVERFLOW_FIT" not in failure_classes:
                    failure_classes.append("C_OVERFLOW_FIT")
            if "BLANK_MIDDLE_PAGE" in pdf_result.hard_fail_reasons:
                if "B_BLANK_PAGE" not in failure_classes:
                    failure_classes.append("B_BLANK_PAGE")
            if "COLUMN_LAYOUT_LOST" in pdf_result.hard_fail_reasons:
                if "F_TOPOLOGY_COLLAPSE" not in failure_classes:
                    failure_classes.append("F_TOPOLOGY_COLLAPSE")
            if pdf_result.region_score < 60:
                if "D_SECTION_MISPLACED" not in failure_classes:
                    failure_classes.append("D_SECTION_MISPLACED")
            if pdf_result.container_score < 70:
                if "G_CONTAINER_OVERFLOW" not in failure_classes:
                    failure_classes.append("G_CONTAINER_OVERFLOW")
            evidence.extend(pdf_result.evidence[:6])
        except Exception as exc:
            evidence.append(f"PDF scoring error: {exc}")
    else:
        if not template_pdf_ok:
            evidence.append("Template PDF conversion failed -- PDF scores use defaults")
        if not generated_pdf_ok:
            evidence.append("Generated PDF conversion failed -- PDF scores use defaults")

    # ── Composite ─────────────────────────────────────────────────────────────
    metrics: dict[str, float] = {
        "ir_score": round(ir_s, 1),
        "content_injection_score": round(ci_score, 1),
        **{k: round(v, 1) for k, v in pdf_scores.items()},
    }
    composite = sum(metrics[k] * w for k, w in _WEIGHTS.items())

    if hard_fail:
        composite = min(composite, 30.0)
        status = "hard_fail"
    elif composite >= 75:
        status = "pass"
    elif composite >= 60:
        status = "warning"
    else:
        status = "fail"

    facts: dict = {
        "original_pages": orig_pages,
        "generated_pages": gen_pages_count,
        "paragraph_ratio": round(paragraph_ratio, 3),
        "tables_original": docx_result.table_count_original if docx_result else 0,
        "tables_generated": docx_result.table_count_generated if docx_result else 0,
        "orig_columns": orig_columns,
        "gen_columns": gen_columns_count,
        "sim_llm": ci_sim_llm,
        "sim_template": ci_sim_template,
    }

    return SampleGrade(
        sample_id=sample_id,
        status=status,
        composite_score=round(composite, 1),
        hard_fail=hard_fail,
        hard_fail_reasons=list(dict.fromkeys(hard_fail_reasons)),
        metrics=metrics,
        facts=facts,
        failure_classes=list(dict.fromkeys(failure_classes)),
        evidence=evidence[:20],
    )
