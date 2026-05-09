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

HARD FAIL triggers (continued):
  TEXT_FRAGMENTATION            Page 1 has >25% of lines as 1-2 char fragments —
                                text is broken at character level (word-wrapping at
                                individual chars) by a positioned template collapse.
                                Template-comparison gate suppresses structural
                                fragmentation (icon fonts, decorative chars).
  POSITIONED_TEMPLATE_COLLAPSE  LibreOffice PDFs show orig ≥2 columns collapsing
                                to 1 column in the generated output for a table-based
                                template (no native Word sectPr columns).  Only
                                triggered when LibreOffice was used for PDF conversion
                                (method=subprocess|docker); xhtml2pdf cannot detect
                                this because it renders tables without honouring CSS
                                absolute/relative positioning.

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

def _check_ir_summary_missing(
    ir: dict, llm_text: str
) -> "tuple[bool, list[str]]":
    """Detect when the LLM output contained a Professional Summary but the IR lacks one.

    Returns (triggered, evidence_list).
    triggered=True is a WARNING signal (C_SUMMARY_MISSING_WHEN_SAFE_ANCHOR_EXISTS).

    Algorithm
    ---------
    1. Extract the LLM summary text (same as _check_misplaced_llm_summary step 1).
    2. Check if the rendered IR has ANY section with substantial prose content
       that could be the summary (length > 60 chars, low comma density).
    3. If the LLM had a summary (> 60 chars) but the IR has no summary-like prose
       → flag the gap.

    False-positive guard: templates that legitimately have no summary region (e.g.
    dense skill-matrix or chronological templates) are excluded when the IR has
    experience/skills sections with substantive body content (the template simply
    doesn't have a summary area).
    """
    import re

    if not llm_text or not ir:
        return False, []

    # Step 1: find LLM summary
    summary_text = ""
    ps_match = re.search(
        r"(?:professional\s+summary|summary|profile|about\s+me)\s*\n+(.+)",
        llm_text,
        re.IGNORECASE,
    )
    if ps_match:
        candidate = ps_match.group(1).strip()
        summary_text = candidate[:200]
    if not summary_text:
        for line in llm_text.split("\n"):
            line = line.strip()
            if len(line) >= 60 and not re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", line):
                summary_text = line[:200]
                break
    if not summary_text or len(summary_text) < 60:
        return False, []

    # Step 2: check IR for summary-like prose
    sections = ir.get("sections", [])
    header_paras = ir.get("header_paras", [])

    # Check for explicit summary section
    has_explicit_summary = any(
        s.get("semantic_type") in ("summary",)
        and any(len(bp.get("text", "").strip()) > 60 for bp in s.get("body_paras", []))
        for s in sections
    )
    if has_explicit_summary:
        return False, []

    # Check for implicit summary in 'other' sections (first 3 sections only, not experience/skills)
    _BODY_CONTENT_TYPES2 = {"experience", "skills", "education", "certifications",
                            "languages", "websites"}
    for sec in sections[:3]:
        sec_type = sec.get("semantic_type", "") or ""
        if sec_type in ("summary",):
            continue
        if sec_type in _BODY_CONTENT_TYPES2:
            continue  # experience bullets are not a summary
        body_paras = sec.get("body_paras", [])
        long_prose = [
            bp.get("text", "").strip()
            for bp in body_paras
            if len(bp.get("text", "").strip()) > 60
        ]
        if long_prose:
            # Check comma density (prose vs skills list)
            total = " ".join(long_prose)
            if total.count(",") / max(1, len(total)) < 0.12:
                return False, []  # prose-like content exists — summary is present

    # Check header paras for summary-like content
    for pm in header_paras:
        text = pm.get("text", "").strip()
        if len(text) > 60:
            return False, []

    # Summary is missing from IR
    return True, [
        f"Summary missing: LLM output contained a Professional Summary "
        f"('{summary_text[:80]}...') but the rendered IR has no summary-like "
        f"prose content — summary may have been dropped or not anchored"
    ]


def _check_ir_duplicate_summary(
    ir: dict, llm_text: str
) -> "tuple[bool, list[str]]":
    """Detect when the same summary text appears in more than one IR section.

    Returns (triggered, evidence_list).
    triggered=True is a WARNING (E_DUPLICATE_SUMMARY).

    This IR-based check is useful for table-based templates where xhtml2pdf
    produces a blank/near-blank PDF, making PDF-level duplicate detection blind.
    """
    import re

    if not llm_text or not ir:
        return False, []

    # Extract LLM summary key
    summary_text = ""
    ps_match = re.search(
        r"(?:professional\s+summary|summary|profile|about\s+me)\s*\n+(.+)",
        llm_text,
        re.IGNORECASE,
    )
    if ps_match:
        summary_text = ps_match.group(1).strip()[:200]
    if not summary_text:
        for line in llm_text.split("\n"):
            line = line.strip()
            if len(line) >= 60 and not re.match(r"^[A-Z][a-z]+ [A-Z][a-z]+$", line):
                summary_text = line[:200]
                break
    if not summary_text or len(summary_text) < 50:
        return False, []

    summary_key = summary_text[:40].lower().strip()

    # Find which sections contain the summary key
    matched_sections: list[str] = []
    for sec in ir.get("sections", []):
        for bp in sec.get("body_paras", []):
            text = bp.get("text", "").strip().lower()
            if len(text) >= 40 and summary_key[:30] in text[:80]:
                matched_sections.append(
                    f"{sec.get('semantic_type','?')}:'{sec.get('title','?')[:30]}'"
                )
                break

    if len(matched_sections) >= 2:
        return True, [
            f"Duplicate summary: summary text found in {len(matched_sections)} IR sections "
            f"({', '.join(matched_sections)}) — summary may be injected twice"
        ]

    return False, []


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
        # a contact/websites-type neighbor (Guard 2 below).  Guard 1 ensures
        # the section has at least minimal content (≥3 body_paras) so that
        # trivially empty sections don't trigger the check.  The contact-
        # neighbor guard is the primary false-positive protection.
        if len(body_paras) < 3:
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
    import platform
    env = os.environ.get("GRADE_PDF_METHOD", "").strip().lower()
    if env in ("subprocess", "docker", "local"):
        return env
    if shutil.which("soffice") or shutil.which("libreoffice"):
        return "subprocess"
    # Windows: LibreOffice may be installed at the standard path but not in PATH.
    # _docx_to_pdf_subprocess uses _find_libreoffice_exe which checks these paths,
    # so "subprocess" works even without a PATH entry.
    if platform.system() == "Windows":
        for _lo_base in (
            r"C:\Program Files\LibreOffice\program",
            r"C:\Program Files (x86)\LibreOffice\program",
        ):
            if os.path.exists(os.path.join(_lo_base, "soffice.exe")):
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
                sc_hard_fail, sc_ev = _check_misplaced_llm_summary(ir, llm_text)
                if sc_hard_fail:
                    hard_fail = True
                    hard_fail_reasons.append("SUMMARY_IN_WRONG_SECTION")
                    if "C_SECTION_CONTENT_MISPLACED" not in failure_classes:
                        failure_classes.append("C_SECTION_CONTENT_MISPLACED")
                evidence.extend(sc_ev)

                # ── IR-based summary missing check ────────────────────────────
                # Detect when LLM had a Professional Summary but the rendered IR
                # has no summary-like prose — the summary was dropped/not anchored.
                sm_triggered, sm_ev = _check_ir_summary_missing(ir, llm_text)
                if sm_triggered:
                    if "C_SUMMARY_MISSING" not in failure_classes:
                        failure_classes.append("C_SUMMARY_MISSING")
                evidence.extend(sm_ev)

                # ── IR-based duplicate summary check ─────────────────────────
                # Detect when the summary appears in more than one IR section.
                ds_triggered, ds_ev = _check_ir_duplicate_summary(ir, llm_text)
                if ds_triggered:
                    if "E_DUPLICATE_SUMMARY" not in failure_classes:
                        failure_classes.append("E_DUPLICATE_SUMMARY")
                evidence.extend(ds_ev)

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
            # F_POSITIONED_TEMPLATE_COLLAPSE: multi-column template (table-based or
            # positioned text-box based) collapsed to single column in LibreOffice.
            # Complements F_TOPOLOGY_COLLAPSE for templates that achieve their
            # 2-column layout without native Word sectPr columns — either via table
            # cells or absolutely positioned text boxes.
            # Only trusted when LibreOffice was the PDF converter (xhtml2pdf cannot
            # reliably detect column counts for non-sectPr layouts).
            _used_lo = pdf_method in ("subprocess", "docker")
            if ("COLUMN_LAYOUT_LOST" in pdf_result.hard_fail_reasons
                    and not _docx_has_orig_cols   # no native Word columns
                    and _used_lo                   # LibreOffice PDF → trusted
                    and orig_columns >= 2
                    and gen_columns_count < 2):
                hard_fail = True
                if "POSITIONED_TEMPLATE_COLLAPSE" not in hard_fail_reasons:
                    hard_fail_reasons.append("POSITIONED_TEMPLATE_COLLAPSE")
                if "F_POSITIONED_TEMPLATE_COLLAPSE" not in failure_classes:
                    failure_classes.append("F_POSITIONED_TEMPLATE_COLLAPSE")
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
            # J. Layer order broken (identity block on page 2, contact on page 1)
            if "LAYER_ORDER_BROKEN" in pdf_result.hard_fail_reasons:
                if "F_LAYER_ORDER_BROKEN" not in failure_classes:
                    failure_classes.append("F_LAYER_ORDER_BROKEN")
            elif getattr(pdf_result, "layer_order_broken", False):
                if "D_LAYER_ORDER_DEGRADED" not in failure_classes:
                    failure_classes.append("D_LAYER_ORDER_DEGRADED")
            # K. Duplicate top-area content
            if "DUPLICATE_TOP_CONTENT" in pdf_result.hard_fail_reasons:
                if "E_DUPLICATE_SUMMARY" not in failure_classes:
                    failure_classes.append("E_DUPLICATE_SUMMARY")
            elif getattr(pdf_result, "duplicate_top_content", False):
                if "E_DUPLICATE_SUMMARY" not in failure_classes:
                    failure_classes.append("E_DUPLICATE_SUMMARY")
            # L. Thin overflow page (sparse_page_score already combined above)
            if "THIN_OVERFLOW_PAGE" in pdf_result.hard_fail_reasons:
                if "C_THIN_OVERFLOW_PAGE" not in failure_classes:
                    failure_classes.append("C_THIN_OVERFLOW_PAGE")
            elif pdf_result.sparse_page_score < 100:
                # Already covered by C_SPARSE_CONTINUATION_PAGE — no additional class
                pass
            # M. Text fragmentation (character-level word breaks)
            if "TEXT_FRAGMENTATION" in pdf_result.hard_fail_reasons:
                if "F_TEXT_FRAGMENTATION" not in failure_classes:
                    failure_classes.append("F_TEXT_FRAGMENTATION")
            # N. Experience section displaced (informational + conditional escalation)
            #
            # D_EXPERIENCE_DISPLACED is always set as an informational failure class
            # when either signal fires — it appears in reports but does NOT alone trigger
            # a soft cap.
            #
            # D_EXPERIENCE_DISPLACED_SEVERE escalates to the soft cap (WARNING ceiling 74)
            # only under two specific conditions indicating genuine layout degradation:
            #
            #   Case A — pushed into lower half of page 1:
            #     experience_pushed_down AND experience_in_lower_half (exp y > 55% of page).
            #     Ordinary summary expansion moves experience from the upper quarter to the
            #     upper half (benign); displacement past the midpoint is perceptible.
            #
            #   Case B — page-2 experience shifted to a lower region:
            #     experience_region_shifted (downward only) AND NOT experience_on_page1.
            #     When experience lives on page 2 and its position within that page moved
            #     to a lower vertical region, the content pushed past the page's natural
            #     reading start — a genuine layout regression.
            _exp_any = (
                getattr(pdf_result, "experience_region_shifted", False)
                or getattr(pdf_result, "experience_pushed_down", False)
            )
            if _exp_any:
                if "D_EXPERIENCE_DISPLACED" not in failure_classes:
                    failure_classes.append("D_EXPERIENCE_DISPLACED")

            _exp_escalate = (
                # Case A: pushed into lower half of page 1
                (getattr(pdf_result, "experience_pushed_down", False)
                 and getattr(pdf_result, "experience_in_lower_half", False))
                or
                # Case B: experience not on page 1 and region shifted downward
                (getattr(pdf_result, "experience_region_shifted", False)
                 and not getattr(pdf_result, "experience_on_page1", True))
                or
                # Case C: MAJOR downward shift (2 regions, top->bottom) on page 1.
                # A 2-region jump indicates catastrophic section displacement that
                # dominates visual reading quality regardless of page position.
                getattr(pdf_result, "experience_region_shifted_major", False)
            )
            if _exp_escalate:
                if "D_EXPERIENCE_DISPLACED_SEVERE" not in failure_classes:
                    failure_classes.append("D_EXPERIENCE_DISPLACED_SEVERE")
            # O. Header-region block overlap (positioned template collapse on page 1)
            if "HEADER_BLOCK_OVERLAP" in pdf_result.hard_fail_reasons:
                hard_fail = True
                if "POSITIONED_TEMPLATE_COLLAPSE" not in hard_fail_reasons:
                    hard_fail_reasons.append("POSITIONED_TEMPLATE_COLLAPSE")
                if "F_POSITIONED_TEMPLATE_COLLAPSE" not in failure_classes:
                    failure_classes.append("F_POSITIONED_TEMPLATE_COLLAPSE")
            # P. Near-empty overflow page (critically sparse)
            if "THIN_OVERFLOW_HARD_FAIL" in pdf_result.hard_fail_reasons:
                hard_fail = True
                if "THIN_OVERFLOW_HARD_FAIL" not in hard_fail_reasons:
                    hard_fail_reasons.append("THIN_OVERFLOW_HARD_FAIL")
                if "B_THIN_OVERFLOW_CRITICAL" not in failure_classes:
                    failure_classes.append("B_THIN_OVERFLOW_CRITICAL")
            # Q. Word-level fragmentation (spaced-out letter runs)
            if "WORD_FRAGMENTATION" in pdf_result.hard_fail_reasons:
                hard_fail = True
                if "WORD_FRAGMENTATION" not in hard_fail_reasons:
                    hard_fail_reasons.append("WORD_FRAGMENTATION")
                if "F_WORD_FRAGMENTATION" not in failure_classes:
                    failure_classes.append("F_WORD_FRAGMENTATION")
            elif getattr(pdf_result, "word_fragmentation", False):
                if "F_WORD_FRAGMENTATION" not in failure_classes:
                    failure_classes.append("F_WORD_FRAGMENTATION")
            # R. Duplicate semantic block (extended area detection)
            if "DUPLICATE_BODY_BLOCK" in pdf_result.hard_fail_reasons:
                hard_fail = True
                if "DUPLICATE_BODY_BLOCK" not in hard_fail_reasons:
                    hard_fail_reasons.append("DUPLICATE_BODY_BLOCK")
                if "E_DUPLICATE_SEMANTIC_BLOCK" not in failure_classes:
                    failure_classes.append("E_DUPLICATE_SEMANTIC_BLOCK")
            elif getattr(pdf_result, "duplicate_body_block", False):
                if "E_DUPLICATE_SEMANTIC_BLOCK" not in failure_classes:
                    failure_classes.append("E_DUPLICATE_SEMANTIC_BLOCK")
            # Density degraded (>50% roles no bullets, not a hard fail)
            if pdf_result.density_score <= 65 and "A_DENSITY_HARD_FAIL" not in failure_classes:
                if "D_DENSITY_DEGRADED" not in failure_classes:
                    failure_classes.append("D_DENSITY_DEGRADED")
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

    # ── Soft failure caps ─────────────────────────────────────────────────────
    # Failure classes that signal content loss not visible in PDF-level metrics
    # cap the composite at 74 (WARNING ceiling) even when all visual dimensions
    # score highly.  Mirrors the HARD FAIL cap at 30.
    _SOFT_CAP_CLASSES = {
        "C_SUMMARY_MISSING",
        "D_DENSITY_DEGRADED",
        "D_EXPERIENCE_DISPLACED_SEVERE",
        "E_DUPLICATE_SEMANTIC_BLOCK",  # duplicate summary/skills in same region
    }
    if not hard_fail and any(fc in _SOFT_CAP_CLASSES for fc in failure_classes):
        composite = min(composite, 74.0)

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
        "pdf_method": pdf_method,
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
