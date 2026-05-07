"""Composite layout grader — orchestrates three evaluation dimensions.

Dimensions and weights:
  ir_score                12%   IR structural correctness
  content_injection_score 15%   LLM output actually applied
  page_count_score         7%   page growth
  blank_page_score         8%   blank-page detection
  region_score            15%   section order + region + column consistency
  container_score          8%   content-area expansion (overflow)
  density_score           10%   bullet/paragraph density from IR
  sparse_page_score       25%   sparse continuation page (forced-break spill)

Classification:
  HARD FAIL: composite capped at 30, status = 'hard_fail'
  PASS      composite >= 75
  WARNING   60-74
  FAIL      < 60

HARD FAIL triggers:
  IR:       EXPERIENCE_NO_ROLES
            EMPTY_PARA_ID (conditional: count > 2 OR ci_score < 80 OR region_score < 80)
  DOCX:     columns_lost (w:cols dropped)
  PDF:      page count > original + 2
            blank middle page
            column layout lost (PDF-detected)
  Content:  lorem ipsum detected, extreme template similarity

HARD FAIL triggers (semantic / IR fallback — work without PDF extraction):
  SUMMARY_IN_WRONG_SECTION    LLM Professional Summary text found in a non-summary
                              IR section (e.g., Contact/sidebar table cell).
  OVERFLOW_COLUMN_LOSS        Overflow page drops from ≥2 column layout (page 1)
                              to 1-column layout — reading topology breaks.

HARD FAIL triggers (continued):
  SPARSE_CONTINUATION_PAGE    non-first sparse page with area_ratio < 30%.
  BLANK_PAGE_CONTENT_LOSS     trailing blank page when page count matches template
                              (generated_pages == original_pages) or when the only
                              rendered page is blank — signals content loss, not
                              tail overflow.
  SPARSE_FIRST_PAGE           page 1 area_ratio < 8% while page 2+ has real
                              content — rendering artefact displaced content.
  COLUMN_CONTINUITY_BREAK     median x-centre of content on page 1 (lower half)
                              differs from page 2 (upper half) by > 25% of page
                              width — section continues in wrong column lane.

WARNING triggers (via sparse_page_score = 0):
  C_SPARSE_CONTINUATION_PAGE  non-first page fills < 65% of page height after
                              a well-packed previous page (>= 75% full) and
                              contains substantial content (>= 18 lines).
                              Escalates to HARD FAIL when area_ratio < 30%.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

_WEIGHTS: dict[str, float] = {
    "ir_score": 0.12,
    "content_injection_score": 0.15,
    "page_count_score": 0.07,
    "blank_page_score": 0.08,
    "region_score": 0.15,
    "container_score": 0.08,
    "density_score": 0.10,
    "sparse_page_score": 0.25,  # detects forced-break sparse continuation pages
}

# Defaults used when PDF conversion or content injection check is unavailable
_PDF_DEFAULTS: dict[str, float] = {
    "page_count_score": 80.0,
    "blank_page_score": 100.0,
    "region_score": 80.0,
    "container_score": 80.0,
    "density_score": 80.0,
    "sparse_page_score": 80.0,  # conservative default when PDF not available
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
# IR-based semantic fallback detectors
# ---------------------------------------------------------------------------

def _check_misplaced_llm_summary(
    ir: dict, llm_text: str
) -> "tuple[bool, list[str]]":
    """Detect LLM Professional Summary injected into a non-summary IR section.

    Returns (hard_fail, evidence_list).

    Works entirely on the IR (parsed from the rendered DOCX) and the raw LLM
    output text — no PDF extraction needed.  This is the correct fallback for
    table-based templates where xhtml2pdf cannot extract text and sim_template
    is near-zero, making PDF-level text-comparison detectors blind.

    Algorithm
    ---------
    1. Extract the LLM Professional Summary (text after the "Professional
       Summary" / "Summary" / "Profile" heading, or the first long line ≥80
       chars in the LLM output).
    2. For every IR section whose semantic_type is NOT 'summary', check whether
       any body_para text starts with the same 40+ characters as the LLM summary.
    3. Additionally flag if any non-summary section has ≥5 body_paras whose
       average length exceeds 80 chars — a contact/other section with that many
       long sentences is almost always a misplaced summary.
    """
    import re

    if not llm_text or not ir:
        return False, []

    # ── Step 1: extract LLM summary text ─────────────────────────────────────
    summary_text = ""
    # Look for an explicit section heading
    ps_match = re.search(
        r"(?:professional\s+summary|summary|profile|about\s+me)\s*\n+(.+)",
        llm_text,
        re.IGNORECASE,
    )
    if ps_match:
        # Get the first substantive sentence (up to 200 chars)
        candidate = ps_match.group(1).strip()
        summary_text = candidate[:200]

    if not summary_text:
        # Fall back: first line of the body that is long enough to be a summary
        for line in llm_text.split("\n"):
            line = line.strip()
            # Skip header lines (short, likely name/contact)
            if len(line) >= 80 and not re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", line):
                summary_text = line[:200]
                break

    if not summary_text or len(summary_text) < 50:
        return False, []

    # Key: first 50 chars lowercase used for substring matching
    summary_key = summary_text[:50].lower().strip()

    # ── Step 2: check IR sections ────────────────────────────────────────────
    _EXPECTED_SUMMARY_TYPES = {"summary", "profile"}
    _BODY_CONTENT_TYPES = {"experience", "education", "skills", "certifications",
                           "projects", "awards", "publications", "languages"}

    for sec in ir.get("sections", []):
        sec_type = sec.get("semantic_type", "") or ""
        if sec_type in _EXPECTED_SUMMARY_TYPES:
            continue  # correct location — not contamination
        if sec_type in _BODY_CONTENT_TYPES:
            continue  # structural body section — professional summary wouldn't normally land here

        body_paras = sec.get("body_paras", [])
        if not body_paras:
            continue

        # Collect text of body paragraphs
        para_texts = []
        for bp in body_paras:
            txt = (bp.get("text", "") if isinstance(bp, dict) else str(bp)).strip()
            if txt:
                para_texts.append(txt)

        # Signal A: LLM summary key found in a body_para AND the section has
        # two additional guards to avoid false positives.
        #
        # Guard 1 — body_para count ≥ 8:
        #   A legitimate 'other' section that happens to be the template's
        #   unlabelled Professional Summary typically has 1–5 body_paras (just
        #   the summary sentences).  A contaminated contact/sidebar table cell
        #   absorbs the full LLM header block (name + contact lines + summary),
        #   producing many more paragraphs (14 in sample 2).
        #
        # Guard 2 — contact/websites neighbor:
        #   When the summary ends up in a sidebar, the immediately adjacent
        #   section is almost always a 'websites', 'contact', or 'social'
        #   section (e.g. sec_2 type=websites in sample 2).  Templates where
        #   the summary is legitimately in an 'other' section have neighbours
        #   that are 'skills', 'experience', or 'education' — not contact types.
        if len(body_paras) < 8:
            continue

        # Check for contact/websites-type neighbour in the sections list
        _CONTACT_NEIGHBOUR_TYPES = {"contact", "websites", "social", "header"}
        sections_list = ir.get("sections", [])
        sec_idx = next(
            (i for i, s in enumerate(sections_list)
             if s.get("section_id") == sec.get("section_id")),
            -1,
        )
        adjacent = []
        if sec_idx > 0:
            adjacent.append(sections_list[sec_idx - 1])
        if sec_idx + 1 < len(sections_list):
            adjacent.append(sections_list[sec_idx + 1])
        has_contact_neighbour = any(
            s.get("semantic_type", "") in _CONTACT_NEIGHBOUR_TYPES
            for s in adjacent
        )
        if not has_contact_neighbour:
            continue  # no contact section nearby — likely a correctly placed summary

        for pt in para_texts[:5]:
            if len(pt) < 50:
                continue
            if summary_key[:40] in pt[:100].lower():
                neighbour_types = [s.get("semantic_type","?") for s in adjacent]
                return True, [
                    f"LLM Professional Summary injected into section "
                    f"'{sec.get('section_id')}' (semantic_type={sec_type!r}, "
                    f"{len(body_paras)} body_paras, neighbours={neighbour_types}) "
                    f"instead of a summary section (HARD FAIL). "
                    f"Summary text found in Contact/sidebar area: '{pt[:140]}'"
                ]

    return False, []


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

                # ── IR-based summary contamination check ─────────────────────
                # Detect when the LLM Professional Summary was injected into a
                # non-summary section (e.g., Contact/sidebar).  This check works
                # on the IR (DOCX-derived) rather than the PDF so it catches
                # table-based templates where xhtml2pdf renders no extractable text.
                sc_hard_fail, sc_ev = _check_misplaced_llm_summary(ir, llm_text)
                if sc_hard_fail:
                    hard_fail = True
                    hard_fail_reasons.append("SUMMARY_IN_WRONG_SECTION")
                    if "C_SECTION_CONTENT_MISPLACED" not in failure_classes:
                        failure_classes.append("C_SECTION_CONTENT_MISPLACED")
                evidence.extend(sc_ev)

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
                "sparse_page_score": pdf_result.sparse_page_score,
            }
            orig_pages = pdf_result.original_pages
            gen_pages_count = pdf_result.generated_pages
            orig_columns = pdf_result.orig_columns
            gen_columns_count = pdf_result.gen_columns
            # COLUMN_LAYOUT_LOST from the PDF scorer is a false positive when
            # the original DOCX has no native Word columns (w:cols).  The PDF
            # x-clustering may reflect content indentation patterns rather than
            # a true 2-column sectPr layout.  Only honour the PDF signal when
            # the DOCX comparator confirms native columns were present.
            _docx_has_orig_cols = (
                docx_result is not None
                and docx_result.has_word_columns_original
            )
            _pdf_hard_reasons = [
                r for r in pdf_result.hard_fail_reasons
                if r != "COLUMN_LAYOUT_LOST" or _docx_has_orig_cols
            ]
            if _pdf_hard_reasons:
                hard_fail = True
                hard_fail_reasons.extend(_pdf_hard_reasons)
            if "PAGE_COUNT_OVERFLOW" in pdf_result.hard_fail_reasons:
                if "C_OVERFLOW_FIT" not in failure_classes:
                    failure_classes.append("C_OVERFLOW_FIT")
            if "BLANK_MIDDLE_PAGE" in pdf_result.hard_fail_reasons:
                if "B_BLANK_PAGE" not in failure_classes:
                    failure_classes.append("B_BLANK_PAGE")
            if "BLANK_PAGE_CONTENT_LOSS" in pdf_result.hard_fail_reasons:
                if "B_BLANK_PAGE_CONTENT_LOSS" not in failure_classes:
                    failure_classes.append("B_BLANK_PAGE_CONTENT_LOSS")
            # Only add F_TOPOLOGY_COLLAPSE from PDF column detection when the
            # DOCX comparator confirmed native Word columns (w:cols) were present.
            # Without native columns, the PDF x-clustering may reflect table
            # column patterns or indentation — not a true column layout loss.
            if "COLUMN_LAYOUT_LOST" in pdf_result.hard_fail_reasons and _docx_has_orig_cols:
                if "F_TOPOLOGY_COLLAPSE" not in failure_classes:
                    failure_classes.append("F_TOPOLOGY_COLLAPSE")
            if pdf_result.region_score < 60:
                if "D_SECTION_MISPLACED" not in failure_classes:
                    failure_classes.append("D_SECTION_MISPLACED")
            if pdf_result.container_score < 70:
                if "G_CONTAINER_OVERFLOW" not in failure_classes:
                    failure_classes.append("G_CONTAINER_OVERFLOW")
            if pdf_result.sparse_page_score < 100:
                if "C_SPARSE_CONTINUATION_PAGE" not in failure_classes:
                    failure_classes.append("C_SPARSE_CONTINUATION_PAGE")
            if "SPARSE_CONTINUATION_PAGE" in pdf_result.hard_fail_reasons:
                if "C_SPARSE_CONTINUATION_PAGE" not in failure_classes:
                    failure_classes.append("C_SPARSE_CONTINUATION_PAGE")
            if "ALL_EXPERIENCE_ROLES_EMPTY" in pdf_result.hard_fail_reasons:
                if "A_DENSITY_HARD_FAIL" not in failure_classes:
                    failure_classes.append("A_DENSITY_HARD_FAIL")
            if "SPARSE_FIRST_PAGE" in pdf_result.hard_fail_reasons:
                if "C_SPARSE_FIRST_PAGE" not in failure_classes:
                    failure_classes.append("C_SPARSE_FIRST_PAGE")
            if "COLUMN_CONTINUITY_BREAK" in pdf_result.hard_fail_reasons:
                if "H_COLUMN_CONTINUITY_BREAK" not in failure_classes:
                    failure_classes.append("H_COLUMN_CONTINUITY_BREAK")
            if "OVERFLOW_COLUMN_LOSS" in pdf_result.hard_fail_reasons:
                if "H_OVERFLOW_COLUMN_LOSS" not in failure_classes:
                    failure_classes.append("H_OVERFLOW_COLUMN_LOSS")
            evidence.extend(pdf_result.evidence[:8])
        except Exception as exc:
            evidence.append(f"PDF scoring error: {exc}")
    else:
        if not template_pdf_ok:
            evidence.append("Template PDF conversion failed -- PDF scores use defaults")
        if not generated_pdf_ok:
            evidence.append("Generated PDF conversion failed -- PDF scores use defaults")

    # ── Duplicate template+LLM content detection ──────────────────────────────
    # When both sim_llm (LLM text present in doc) and sim_template (original
    # template text present in doc) are simultaneously high, the document likely
    # contains the original placeholder text alongside the new LLM content —
    # neither fully replaced the other.  This detects cases where the LLM summary
    # was injected into a wrong container (Contact/sidebar) while the original
    # summary or template filler was left in place.
    # Threshold: sim_llm > 0.85 (LLM content is present) AND sim_template > 0.75
    # (original template is also still present).  Both simultaneously being this
    # high signals coexistence rather than replacement.
    if (
        ci_sim_llm is not None
        and ci_sim_template is not None
        and ci_sim_llm > 0.85
        and ci_sim_llm < 0.97      # sim_llm=1.0 means PERFECT injection (not duplicate)
        and ci_sim_template > 0.75
    ):
        hard_fail = True
        hard_fail_reasons.append("DUPLICATE_TEMPLATE_AND_LLM_CONTENT")
        if "E_DUPLICATE_CONTENT" not in failure_classes:
            failure_classes.append("E_DUPLICATE_CONTENT")
        evidence.append(
            f"Duplicate content (HARD FAIL): sim_llm={ci_sim_llm:.2f}, "
            f"sim_template={ci_sim_template:.2f} — LLM and original template text "
            f"coexist; LLM content likely injected into wrong container"
        )

    # ── Conditional EMPTY_PARA_ID hard-fail ───────────────────────────────────
    # Only hard-fail when the count is high or combined with content/layout
    # degradation.  A single empty para_id on a mis-parsed header in an
    # otherwise perfect layout (ci_score=100, region=100) is a warning only.
    if "EMPTY_PARA_ID" in ir_result.failures:
        _epi_count = ir_result.empty_para_id_count
        _epi_degraded = (
            ci_score < 80
            or pdf_scores.get("region_score", 100) < 80
            or hard_fail  # already failing for another reason
        )
        if _epi_count > 2 or _epi_degraded:
            hard_fail = True
            if "EMPTY_PARA_ID" not in hard_fail_reasons:
                hard_fail_reasons.append("EMPTY_PARA_ID")

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
