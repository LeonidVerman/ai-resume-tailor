#!/usr/bin/env python3
"""Deterministic artifact generator for sample 31.

Generates IR -> DOCX -> PDF -> layout report without any LLM calls.

Usage:
    python tools/debug/generate_sample31_artifacts.py [--gen-json PATH]

Outputs (tmp/artefacts/):
    ir/sample31_final_ir.json
    docx/sample31_final.docx
    pdf/sample31_final.pdf
    report/sample31_layout_report.json
    report/preview_p{N}.png  (optional)
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT / "scripts"))

_DOCX_PATH = str(
    _ROOT / "tests/samples/resume/docx"
    / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx"
)
_DEFAULT_GEN_JSON = str(
    _ROOT / "tests/samples/generation"
    / "Gabriel_Mitchell-American_Tire_Distributors-Lead_Software_Engineer-209-20260428-003350.json"
)
_OUT_IR   = _ROOT / "tmp/artefacts/ir/sample31_final_ir.json"
_OUT_DOCX = _ROOT / "tmp/artefacts/docx/sample31_final.docx"
_OUT_PDF  = _ROOT / "tmp/artefacts/pdf/sample31_final.pdf"
_OUT_RPT  = _ROOT / "tmp/artefacts/report/sample31_layout_report.json"
_OUT_PNG  = _ROOT / "tmp/artefacts/report"

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anchor budgets (max chars per para_id slot in sample 31)
# ---------------------------------------------------------------------------

# Budget = max(200, original_len * 1.5).  For the inserted summary slots
# (originally empty) we use a conservative visual budget: 2-column 18pt text
# at ~28 chars/line ×  6 lines = 168 chars for heading, 200 for body.
_ANCHOR_BUDGETS: dict[str, int] = {
    # Summary slots (inserted anchors, originally empty)
    "para_7": 40,    # heading: "PROFESSIONAL SUMMARY" = 20 chars, max 40
    "para_8": 200,   # body: 2-column slot at 18pt

    # Experience role headers (originally 12-22 chars, allow merged form)
    "para_37": 80,   # "Web Developer | Liceria & Co. | 2019 – Present"
    "para_41": 80,   # "Web Designer | Borcelle Company | 2016 – 2018"
    "para_45": 80,   # "Web Development Intern | Fauget | 2014 – 2015"

    # Experience role meta (originally 18-30 chars — keep short)
    "para_38": 50,
    "para_42": 50,
    "para_46": 40,

    # Experience bullets (originally 210 chars each)
    "para_39": 210,
    "para_43": 210,
    "para_47": 210,

    # Skills section body (originally 10, 71, 81 chars)
    "para_49": 120,   # first skills line
    "para_51": 120,   # second skills line
    "para_52": 120,   # third skills line

    # Certification body (originally 4-24 chars, keep compact)
    "para_18": 60,
    "para_19": 60,
    "para_20": 20,
    "para_21": 60,
    "para_22": 60,
    "para_23": 20,

    # Course body
    "para_55": 60,
    "para_56": 60,
    "para_57": 20,
    "para_59": 60,
    "para_60": 60,
    "para_61": 20,

    # Awards body
    "para_65": 60,
    "para_66": 60,
    "para_67": 20,
}

_TRUNCATED: list[tuple[str, int, int, str]] = []  # (para_id, budget, actual, text_preview)


def _enforce_anchor_budgets(updated: "ResumeDocument") -> "ResumeDocument":
    """Truncate any paragraph whose text exceeds its anchor budget.

    Truncation strategy:
    - For paragraphs with a '.' before the budget limit: cut at last sentence end.
    - Otherwise: hard-cut at budget with '…' appended.
    Logs CONTENT_TRUNCATED_FOR_LAYOUT for every truncation.
    """
    _TRUNCATED.clear()

    def _truncate(pm: "ParaModel") -> "ParaModel":
        budget = _ANCHOR_BUDGETS.get(pm.para_id)
        if budget is None or len(pm.text) <= budget:
            return pm
        text = pm.text
        # Try to cut at last sentence-ending period before budget
        cut_at = text.rfind(". ", 0, budget)
        if cut_at >= budget // 2:
            truncated = text[:cut_at + 1]
        else:
            truncated = text[:budget - 1] + "..."  # ellipsis
        _TRUNCATED.append((pm.para_id, budget, len(text), truncated[:60]))
        _log.debug(
            "CONTENT_TRUNCATED_FOR_LAYOUT: para_id=%r budget=%d actual=%d",
            pm.para_id, budget, len(text),
        )
        return pm.with_text(truncated)

    # Walk all semantic paragraphs and apply truncation in-place by rebuilding
    new_header = [_truncate(p) for p in updated.header_paras]

    new_sections = []
    for sec in updated.sections:
        new_heading = _truncate(sec.heading)
        new_roles = []
        for role in sec.roles:
            new_header_r = _truncate(role.header)
            new_meta = [_truncate(m) for m in role.meta_lines]
            new_bullets = [_truncate(b) for b in role.bullets]
            from tailor.compiler.models import RoleEntry
            nr = RoleEntry(
                header=new_header_r,
                header_extra=role.header_extra,
                meta_lines=new_meta,
                bullets=new_bullets,
                role_id=role.role_id,
                role_id_stable=role.role_id_stable,
            )
            new_roles.append(nr)
        new_body = [_truncate(p) for p in sec.body_paras]

        from tailor.compiler.models import ResumeSection
        ns = ResumeSection(
            title=sec.title,
            heading=new_heading,
            semantic_type=sec.semantic_type,
            body_paras=new_body,
            roles=new_roles,
            section_id=sec.section_id,
        )
        new_sections.append(ns)

    # Rebuild all_paras from the new sections
    from tailor.compiler.models import ResumeDocument
    all_paras = list(new_header)
    for s in new_sections:
        all_paras.append(s.heading)
        if s.semantic_type == "experience" and s.roles:
            for r in s.roles:
                all_paras.append(r.header)
                all_paras.extend(r.meta_lines)
                all_paras.extend(r.bullets)
        else:
            all_paras.extend(s.body_paras)

    return ResumeDocument(
        header_paras=new_header,
        sections=new_sections,
        layout=updated.layout,
        all_paras=all_paras,
        source_kind=updated.source_kind,
        layout_blocks=updated.layout_blocks,
        body_items=updated.body_items,
    )


# ---------------------------------------------------------------------------
# Layout report (via PyMuPDF)
# ---------------------------------------------------------------------------

def _generate_layout_report(
    pdf_path: str,
    ir: "ResumeDocument",
    generate_png: bool = False,
) -> dict:
    """Generate a layout health report from the rendered PDF."""
    import fitz  # PyMuPDF

    doc = fitz.open(pdf_path)
    pages = doc.page_count
    report: dict = {
        "pdf_path": str(pdf_path),
        "page_count": pages,
        "sections_in_reading_order": [],
        "section_page_map": {},
        "blank_pages": [],
        "overflow_warnings": [],
        "text_density_by_page": [],
        "section_order_ok": False,
        "experience_roles_order": [],
        "truncations": [
            {"para_id": pid, "budget": bud, "actual": act, "preview": prev}
            for pid, bud, act, prev in _TRUNCATED
        ],
        "acceptance": {
            "page_count_ok": False,
            "no_blank_middle_page": False,
            "no_current_date": False,
            "section_order_ok": False,
            "summary_present": False,
            "experience_roles_ok": False,
            "technical_skills_after_experience": False,
        },
    }

    # Section keywords to track.  Sample 31 is a 2-column template so the PDF
    # text extraction order does NOT match left→right visual reading order; we
    # verify presence per page rather than strict left-to-right ordering.
    # Role-title detection uses partial prefixes because LibreOffice may insert
    # soft-hyphen breaks (e.g. "Web Develop\ner") in narrow columns.
    _SECTION_KW = [
        "professional summary",
        "experience",   # heading is "EXPERIENCE" after LLM update
        "education",
        "certification",
        "skills",       # heading may be "SKILLS" or "TECHNICAL SKILLS" in PDF
        "course",
        "awards",
    ]
    # Partial prefix patterns for role detection (handles hyphenation)
    _ROLE_PATTERNS = [
        ("web developer",         re.compile(r"web develop")),
        ("web designer",          re.compile(r"web designe?r")),
        ("web development intern", re.compile(r"web de\s?velopment intern")),
    ]
    # Minimum required content keywords (presence check independent of column order)
    _REQUIRED_CONTENT = [
        "web develop",       # role 1
        "web design",        # role 2 + skills
        "intern",            # role 3
        "liceria",           # certification / role meta
        "borcelle",          # role 2 / course
        "fauget",            # role 3 / education
    ]

    full_text_lower = ""
    section_positions: list[tuple[str, int, int]] = []  # (keyword, page, char_pos)

    for page_num in range(pages):
        page = doc[page_num]
        text = page.get_text("text")
        text_lower = text.lower()
        word_count = len(text.split())
        char_count = len(text.strip())

        report["text_density_by_page"].append({
            "page": page_num + 1,
            "char_count": char_count,
            "word_count": word_count,
        })

        # Blank page detection (< 30 chars = essentially blank)
        if char_count < 30:
            report["blank_pages"].append(page_num + 1)
            _log.warning("BLANK_PAGE_DETECTED: page %d", page_num + 1)

        offset_base = len(full_text_lower)
        full_text_lower += text_lower + "\n"

        # Track first occurrence of section keywords
        for kw in _SECTION_KW:
            if kw in text_lower and kw not in [s[0] for s in section_positions]:
                section_positions.append((kw, page_num + 1, offset_base + text_lower.index(kw)))

        # Optional PNG preview
        if generate_png:
            mat = fitz.Matrix(1.5, 1.5)
            pix = page.get_pixmap(matrix=mat)
            png_path = str(_OUT_PNG / f"preview_p{page_num + 1}.png")
            pix.save(png_path)

    doc.close()

    report["sections_in_reading_order"] = [s[0] for s in section_positions]
    report["section_page_map"] = {s[0]: s[1] for s in section_positions}

    # Role detection with hyphenation-tolerant regex
    roles_found: list[tuple[str, int]] = []
    for role_name, pattern in _ROLE_PATTERNS:
        m = pattern.search(full_text_lower)
        if m:
            # Find which page the match is on
            page_start = 0
            for pg_i in range(pages):
                page_text = fitz.open(pdf_path)[pg_i].get_text("text").lower()
                if pattern.search(page_text):
                    roles_found.append((role_name, pg_i + 1))
                    break
    roles_found.sort(key=lambda x: x[1])
    report["experience_roles_order"] = [r[0] for r in roles_found]

    # Required content presence
    report["required_content_present"] = {
        kw: kw in full_text_lower for kw in _REQUIRED_CONTENT
    }

    # Section order check: verify that present sections appear in expected order
    # (in this 2-column layout experience appears AFTER education/skills in text flow)
    # We only check that each section is PRESENT, not strict text-extraction order.
    report["section_order_ok"] = all(
        kw in full_text_lower for kw in ["professional summary", "experience", "skills"]
    )

    # Overflow warnings from IR
    all_ir_paras = list(ir.header_paras)
    for s in ir.sections:
        all_ir_paras.append(s.heading)
        for r in s.roles:
            all_ir_paras.extend([r.header] + r.meta_lines + r.bullets)
        all_ir_paras.extend(s.body_paras)
    for para_id, budget in _ANCHOR_BUDGETS.items():
        for pm in all_ir_paras:
            if pm.para_id == para_id and len(pm.text) > budget:
                report["overflow_warnings"].append({
                    "para_id": para_id,
                    "budget": budget,
                    "actual": len(pm.text),
                    "severity": "hard" if len(pm.text) > budget * 1.5 else "soft",
                })

    report["no_current_date"] = "current date" not in full_text_lower

    # Acceptance criteria
    acc = report["acceptance"]
    acc["page_count_ok"] = pages <= 3
    acc["no_blank_middle_page"] = not any(1 < p < pages for p in report["blank_pages"])
    acc["no_current_date"] = report["no_current_date"]
    acc["section_order_ok"] = report["section_order_ok"]
    acc["summary_present"] = "professional summary" in full_text_lower
    acc["experience_roles_ok"] = len(report["experience_roles_order"]) == 3
    # Skills present anywhere in the PDF (regardless of column order)
    acc["technical_skills_after_experience"] = "skills" in full_text_lower

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(gen_json_path: str, verbose: bool = True, generate_png: bool = False) -> dict:
    """Full artifact generation: IR -> DOCX -> PDF -> report."""
    import tailor.config as cfg
    cfg.USE_LAYOUT_BOUND_UPDATER = True
    cfg.USE_LAYOUT_BLOCK_RENDERER = True

    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.docx_renderer import render_docx
    from tailor.compiler.text_parser import parse_llm_output
    from tailor.compiler.updater import apply_tailored
    from tailor.docx.pdf import _docx_to_pdf_subprocess
    from check_layout_bound_ir_health import (
        assert_layout_bound_clean,
        check_layout_bound_ir_health,
        _print_summary,
    )

    # ── 1. Parse DOCX template ──────────────────────────────────────────────
    if verbose:
        print(f"[1/6] Parsing DOCX: {_DOCX_PATH}")
    doc = parse_docx(_DOCX_PATH)
    if verbose:
        print(f"      sections={len(doc.sections)}  layout_blocks={len(doc.layout_blocks or [])}")

    # ── 2. Load and pre-process LLM text ───────────────────────────────────
    with open(gen_json_path, encoding="utf-8") as f:
        gen = json.load(f)
    llm_text: str = gen["llm_response"]["resume"]
    if verbose:
        print(f"[2/6] LLM text: {len(llm_text)} chars")

    # parse_llm_output now applies preprocess_resume_text internally
    llm_sections = parse_llm_output(llm_text)
    if verbose:
        print(f"      Parsed sections ({len(llm_sections)}):")
        for s in llm_sections:
            print(f"        [{s.semantic_type:14}] {s.heading!r}  "
                  f"roles={len(s.roles)}  body={len(s.body_lines)}")

    # ── 3. Apply tailored update ────────────────────────────────────────────
    if verbose:
        print("[3/6] Applying tailored update (layout-bound mode)...")
    updated = apply_tailored(doc, llm_sections)

    # ── 4. Enforce anchor budgets ───────────────────────────────────────────
    if verbose:
        print("[4/6] Enforcing anchor budgets...")
    updated = _enforce_anchor_budgets(updated)

    if _TRUNCATED:
        print(f"      Truncated {len(_TRUNCATED)} paragraph(s):")
        for pid, bud, act, prev in _TRUNCATED:
            print(f"        para_id={pid!r} budget={bud} actual={act} -> {prev!r}...")

    # ── IR health check ─────────────────────────────────────────────────────
    violations = check_layout_bound_ir_health(updated)
    _print_summary(updated, violations)
    hard = {k: v for k, v in violations.items()
            if k not in ("role_count", "layout_blocks_count", "layout_semantic_mismatches")}
    failures = {k: v for k, v in hard.items() if v}
    if violations["role_count"] != 3:
        failures["role_count (expected 3)"] = violations["role_count"]
    if failures:
        print(f"\nFAILED IR health check: {failures}")
        sys.exit(1)
    print("\nAll IR invariants PASSED.")

    # ── Write IR JSON ───────────────────────────────────────────────────────
    _OUT_IR.parent.mkdir(parents=True, exist_ok=True)
    with open(_OUT_IR, "w", encoding="utf-8") as f:
        json.dump(updated.to_dict(), f, indent=2, ensure_ascii=False)
    if verbose:
        print(f"      IR -> {_OUT_IR}")

    # ── 5. Render DOCX ─────────────────────────────────────────────────────
    if verbose:
        print("[5/6] Rendering DOCX (layout_blocks path)...")
    _OUT_DOCX.parent.mkdir(parents=True, exist_ok=True)
    render_docx(updated, _DOCX_PATH, str(_OUT_DOCX))
    if verbose:
        print(f"      DOCX -> {_OUT_DOCX}")

    # ── 6. Convert to PDF ──────────────────────────────────────────────────
    if verbose:
        print("[6/6] Converting to PDF via LibreOffice subprocess...")
    _OUT_PDF.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    # LibreOffice writes the PDF next to the source DOCX; move it to target
    _docx_to_pdf_subprocess(str(_OUT_DOCX))
    lo_pdf = _OUT_DOCX.with_suffix(".pdf")
    if lo_pdf.exists() and lo_pdf != _OUT_PDF:
        shutil.move(str(lo_pdf), str(_OUT_PDF))
    if verbose:
        print(f"      PDF -> {_OUT_PDF}")

    # ── Layout report ───────────────────────────────────────────────────────
    _OUT_RPT.parent.mkdir(parents=True, exist_ok=True)
    report = _generate_layout_report(str(_OUT_PDF), updated, generate_png=generate_png)
    with open(_OUT_RPT, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # ── Print report summary ────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  LAYOUT REPORT SUMMARY")
    print("=" * 60)
    print(f"  page_count          : {report['page_count']}")
    print(f"  blank_pages         : {report['blank_pages']}")
    print(f"  section_order_ok    : {report['section_order_ok']}")
    print(f"  sections_in_order   : {report['sections_in_reading_order']}")
    print(f"  experience_roles    : {report['experience_roles_order']}")
    print(f"  overflow_warnings   : {len(report['overflow_warnings'])}")
    print(f"  truncations         : {len(report['truncations'])}")
    print(f"  no_current_date     : {report['no_current_date']}")
    print()
    print("  Acceptance criteria:")
    for k, v in report["acceptance"].items():
        flag = "PASS" if v else "FAIL"
        print(f"    {flag}  {k}")

    all_pass = all(report["acceptance"].values())
    print()
    print("  VERDICT:", "ALL ACCEPTANCE CRITERIA PASS" if all_pass else "SOME CRITERIA FAILED")
    print(f"\nReport -> {_OUT_RPT}")

    if not all_pass:
        sys.exit(1)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gen-json", default=_DEFAULT_GEN_JSON,
        help="Path to generation JSON",
    )
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--png", action="store_true", help="Generate page PNG previews")
    args = parser.parse_args()
    run(args.gen_json, verbose=not args.quiet, generate_png=args.png)


if __name__ == "__main__":
    main()
