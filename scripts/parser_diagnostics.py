"""
scripts/parser_diagnostics.py

Deterministic QA tool that scans parser-produced llm-input JSON files and
reports structural findings in two categories:

  Category A — True parser bugs
    Structurally wrong independent of final section semantics.  Contribute to
    parser_issue_score and set file status = "parser_issue".

  Category B — Structural candidates / informational signals
    Parser emits role-like or ambiguous structure that the LLM/classifier will
    resolve at classification time.  Do NOT make a file count as broken.
    Contribute to info_score only.

Definition of CLEAN:
  A file is CLEAN if it has zero Category A issues.
  Category B warnings are allowed.

Usage:
  python scripts/parser_diagnostics.py
  python scripts/parser_diagnostics.py --input tmp/artefacts/classification/llm-input
  python scripts/parser_diagnostics.py --input path/to/dir --json out.json --txt out.txt

Reads:  tmp/artefacts/classification/llm-input/{docx,pdf}/*.json  (by default)
Writes: tmp/artefacts/classification/parser_diagnostics.json
        tmp/artefacts/classification/parser_diagnostics_summary.txt

Issue codes
-----------
Category A (true parser bugs):
  EXPERIENCE_NO_ROLES                      (w=3) Experience section has no roles[].
  MERGED_SECTION_SUSPECTED                 (w=4) Experience body absorbed a section heading.
  HEADING_NOT_RECOGNIZED                   (w=2) Paragraph looks like a heading but is 'paragraph'.
  EXPERIENCE_ROLE_BOUNDARY_INSIDE_BULLETS  (w=5) Experience role bullet_para_ids contain role_meta
                                                  entries (true job-boundary markers absorbed as bullets).
  EXPERIENCE_OVERMERGED_ROLE               (w=3) Single experience role with many bullets and
                                                  multiple role_meta inside (multi-job merge).

Category B (structural candidates / informational):
  BULLET_NOT_RECOGNIZED                    (w=1) Text looks like a bullet but semantic is not 'bullet'.
  ROLE_GROUPING_WEAK                       (w=2) Role has 2+ headers and 0 bullets.
  ROLE_HEADER_CONTAINS_DATE                (w=2) Role header is predominantly a date line.
  ROLE_LIKE_GROUPING_NON_EXPERIENCE        (w=1) Non-experience section has role-like structure;
                                                  LLM decides final section semantics.
  LABEL_COLUMN_DETECTED                    (w=1) Parser detected and fixed a label-column layout
                                                  (narrow left column of section labels + wide right
                                                  column of content); section structure was reordered.
  TABLE_COLUMN_LAYOUT_DETECTED             (w=1) Parser detected and fixed a newspaper/table
                                                  multi-column layout (column-break contamination);
                                                  paragraphs were reordered to column-first order.
  TABLE_COLUMN_CONTAMINATION_SUSPECTED     (w=2) A non-skills section contains skills-like dense
                                                  text; possible cross-column contamination not fixed.
  TABLE_COLUMN_REORDER_ABORTED             (w=1) Multi-column reorder was detected but rolled back
                                                  because the candidate reduced sections or roles.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Issue metadata: code → (category, weight, severity)
# ---------------------------------------------------------------------------

_CAT_A = "A"
_CAT_B = "B"

_ISSUE_METADATA: dict[str, tuple[str, int, str]] = {
    # Category A — true parser bugs
    "EXPERIENCE_NO_ROLES":                     (_CAT_A, 3, "high"),
    "MERGED_SECTION_SUSPECTED":                (_CAT_A, 4, "high"),
    "HEADING_NOT_RECOGNIZED":                  (_CAT_A, 2, "medium"),
    "ROLE_HEADER_CONTAINS_DATE":               (_CAT_B, 2, "low"),
    "EXPERIENCE_ROLE_BOUNDARY_INSIDE_BULLETS": (_CAT_A, 5, "high"),
    "EXPERIENCE_OVERMERGED_ROLE":              (_CAT_A, 3, "high"),
    # Category B — structural signals / informational
    "BULLET_NOT_RECOGNIZED":                   (_CAT_B, 1, "info"),
    "ROLE_GROUPING_WEAK":                      (_CAT_B, 2, "low"),
    "ROLE_LIKE_GROUPING_NON_EXPERIENCE":       (_CAT_B, 1, "info"),
    "LABEL_COLUMN_DETECTED":                   (_CAT_B, 1, "info"),
    # Category B — multi-column / table-column layout signals
    "TABLE_COLUMN_LAYOUT_DETECTED":            (_CAT_B, 1, "info"),
    "TABLE_COLUMN_CONTAMINATION_SUSPECTED":    (_CAT_B, 2, "low"),
    "TABLE_COLUMN_REORDER_ABORTED":            (_CAT_B, 1, "info"),
}


def _category(code: str) -> str:
    return _ISSUE_METADATA.get(code, (_CAT_A, 1, "medium"))[0]


def _weight(code: str) -> int:
    return _ISSUE_METADATA.get(code, (_CAT_A, 1, "medium"))[1]


# ---------------------------------------------------------------------------
# Pattern constants
# ---------------------------------------------------------------------------

_SECTION_HEADING_WORDS: frozenset[str] = frozenset({
    "experience", "work experience", "professional experience",
    "employment history", "employment", "career history",
    "summary", "professional summary", "objective", "profile",
    "skills", "technical skills", "core competencies", "competencies",
    "education", "academic background", "degrees",
    "certifications", "certification", "licenses",
    "languages", "language skills",
    "projects", "publications", "awards", "honors",
    "references", "activities", "volunteer", "volunteering",
    "leadership", "interests", "additional information",
    "communication", "contact", "about me",
})

_HEADING_TRIGGER_WORDS: frozenset[str] = frozenset({
    "experience", "education", "skills", "summary", "objective",
    "certifications", "projects", "references", "awards", "profile",
    "leadership", "communication", "publications", "languages",
    "employment", "qualifications", "volunteer", "interests",
    "career", "background", "contact", "about",
})

_BULLET_PREFIX_RE = re.compile(
    r"^(\s*[-•·–*●◦•–][ \t]|^\s*\d+[.)]\s)",
    re.UNICODE,
)

_DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b"
    r"|20[Xx]{2}"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b"
    r"|\bpresent\b"
    r"|[-–—]\s*(present|current)\b",
    re.IGNORECASE,
)

_DATE_STRIP_RE = re.compile(
    r"\b(19|20)\d{2}\b|20[Xx]{2}"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b"
    r"|\bpresent\b|\bcurrent\b"
    r"|[-–—/]"
    r"|\bto\b",
    re.IGNORECASE,
)

_EXPERIENCE_TITLES: frozenset[str] = frozenset({
    "experience", "experiences", "work experience", "professional experience",
    "employment history", "employment", "career history", "work history",
    "professional background", "employment summary", "work summary",
    "experience summary",
})

_SKILLS_SECTION_WORDS: frozenset[str] = frozenset({
    "skill", "skills", "competenc", "expertise", "proficien", "key skills",
    "technical skills", "core competencies",
})

_OVERMERGE_BULLET_THRESHOLD = 15


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    code: str
    detail: str
    section_id: str = ""
    para_id: str = ""
    role_id: str = ""

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "detail": self.detail,
            "section_id": self.section_id or None,
            "para_id": self.para_id or None,
            "role_id": self.role_id or None,
        }


@dataclass
class FileReport:
    path: str
    source_kind: str
    document_id: str
    category_a_issues: list[Issue] = field(default_factory=list)
    category_b_issues: list[Issue] = field(default_factory=list)

    @property
    def parser_issue_score(self) -> int:
        return sum(_weight(i.code) for i in self.category_a_issues)

    @property
    def info_score(self) -> int:
        return sum(_weight(i.code) for i in self.category_b_issues)

    @property
    def status(self) -> str:
        if self.category_a_issues:
            return "parser_issue"
        if self.category_b_issues:
            return "clean_with_info"
        return "clean"

    @property
    def severity(self) -> str:
        if not self.category_a_issues:
            return "info" if self.category_b_issues else "none"
        max_sev = "medium"
        for issue in self.category_a_issues:
            sev = _ISSUE_METADATA.get(issue.code, (_CAT_A, 1, "medium"))[2]
            if sev == "high":
                return "high"
            if sev == "medium":
                max_sev = "medium"
        return max_sev

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "document_id": self.document_id,
            "source_kind": self.source_kind,
            "status": self.status,
            "severity": self.severity,
            "parser_issue_score": self.parser_issue_score,
            "info_score": self.info_score,
            "category_a_issues": [i.to_dict() for i in self.category_a_issues],
            "category_b_issues": [i.to_dict() for i in self.category_b_issues],
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_para_map(sections: list[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for sec in sections:
        for p in sec.get("paragraphs", []):
            result[p["para_id"]] = p.get("text", "")
    return result


def _is_experience_section(title: str) -> bool:
    t = title.strip().lower()
    if t in _EXPERIENCE_TITLES:
        return True
    # Strip leading numbering ("1. ", "2. ", "I. ", etc.) and recheck.
    t2 = re.sub(r"^\d+\.\s+|^[ivxlIVXL]+\.\s+", "", t)
    if t2 != t and t2 in _EXPERIENCE_TITLES:
        return True
    # Letter-spaced headings ("E X P E R I E N C E"): collapse and recheck.
    collapsed = re.sub(r"(?<=\b[A-Za-z])\s+(?=[A-Za-z]\b)", "", t)
    return collapsed in _EXPERIENCE_TITLES


# ---------------------------------------------------------------------------
# Category A detectors
# ---------------------------------------------------------------------------

def detect_experience_no_roles(sections: list[dict]) -> list[Issue]:
    issues: list[Issue] = []
    for sec in sections:
        if _is_experience_section(sec.get("raw_title", "")):
            if not sec.get("roles"):
                issues.append(Issue(
                    code="EXPERIENCE_NO_ROLES",
                    detail=f"Section '{sec['raw_title']}' is experience-like but has no roles",
                    section_id=sec["section_id"],
                ))
    return issues


def detect_merged_section(sections: list[dict], para_map: dict[str, str]) -> list[Issue]:
    """Experience body contains text matching a known top-level section heading."""
    issues: list[Issue] = []
    for sec in sections:
        if not _is_experience_section(sec.get("raw_title", "")):
            continue
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") in ("section_heading", "paragraph", "role_header"):
                raw = p.get("text", "").strip()
                if not raw or not raw[0].isupper():
                    continue
                text = raw.lower()
                if text in _SECTION_HEADING_WORDS and text not in _EXPERIENCE_TITLES:
                    issues.append(Issue(
                        code="MERGED_SECTION_SUSPECTED",
                        detail=f"Experience body contains section-heading text '{p['text'].strip()}'",
                        section_id=sec["section_id"],
                        para_id=p["para_id"],
                    ))
    return issues


def detect_heading_not_recognized(sections: list[dict]) -> list[Issue]:
    """Short paragraph that looks like a heading but is marked 'paragraph'."""
    issues: list[Issue] = []
    for sec in sections:
        sec_title_lower = sec.get("raw_title", "").lower()
        in_skills_sec = any(kw in sec_title_lower for kw in _SKILLS_SECTION_WORDS)
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") != "paragraph":
                continue
            text = p.get("text", "").strip()
            if not text or len(text) > 60:
                continue
            if not text[0].isupper():
                continue
            words = set(re.split(r"[\s/&,]+", text.lower()))
            words.discard("")
            if words & _HEADING_TRIGGER_WORDS and text.lower() in _SECTION_HEADING_WORDS:
                if in_skills_sec and len(words) == 1:
                    continue
                issues.append(Issue(
                    code="HEADING_NOT_RECOGNIZED",
                    detail=f"'{text}' looks like a section heading but is marked 'paragraph'",
                    section_id=sec["section_id"],
                    para_id=p["para_id"],
                ))
    return issues


def detect_role_header_contains_date(
    sections: list[dict], para_map: dict[str, str]
) -> list[Issue]:
    """Role header paragraph is predominantly date-like text (header/meta mixup)."""
    issues: list[Issue] = []
    for sec in sections:
        for role in sec.get("roles", []):
            for pid in role.get("header_para_ids", []):
                text = para_map.get(pid, "")
                if not _DATE_RE.search(text):
                    continue
                residual = _DATE_STRIP_RE.sub(" ", text)
                residual_words = [
                    w for w in residual.split()
                    if len(w) > 2 and w not in ("|", "&", "and", "AND", "the", "THE")
                ]
                if len(residual_words) < 2:
                    issues.append(Issue(
                        code="ROLE_HEADER_CONTAINS_DATE",
                        detail=(
                            f"Role header '{text.strip()[:60]}' looks like a date-only line "
                            f"(residual: '{' '.join(residual_words)}')"
                        ),
                        section_id=sec["section_id"],
                        para_id=pid,
                        role_id=role["role_id"],
                    ))
    return issues


def detect_experience_role_boundary_inside_bullets(sections: list[dict]) -> list[Issue]:
    """Experience role bullet_para_ids contain role_meta entries.

    role_meta semantics signal a job boundary.  A role_meta inside bullet_para_ids
    means a job boundary was absorbed into a prior role's bullet list instead of
    triggering a new role split — a true structural parser bug.

    Note: 'paragraph' semantics inside bullet_para_ids are normal parser behaviour
    (continuation paragraphs) and are NOT flagged here.
    """
    issues: list[Issue] = []
    for sec in sections:
        if not _is_experience_section(sec.get("raw_title", "")):
            continue
        para_sem: dict[str, str] = {
            p["para_id"]: p.get("parser_semantic", "")
            for p in sec.get("paragraphs", [])
        }
        for role in sec.get("roles", []):
            boundary_pids = [
                pid for pid in role.get("bullet_para_ids", [])
                if para_sem.get(pid) == "role_meta"
            ]
            if boundary_pids:
                issues.append(Issue(
                    code="EXPERIENCE_ROLE_BOUNDARY_INSIDE_BULLETS",
                    detail=(
                        f"Role bullet_para_ids contain {len(boundary_pids)} role_meta "
                        f"entry(ies) (job-boundary markers inside bullets): "
                        f"{boundary_pids[:4]}"
                    ),
                    section_id=sec["section_id"],
                    role_id=role["role_id"],
                ))
    return issues


def detect_experience_overmerged_role(sections: list[dict]) -> list[Issue]:
    """Experience role has ≥15 bullet slots with ≥2 role_meta entries — multi-job merge."""
    issues: list[Issue] = []
    for sec in sections:
        if not _is_experience_section(sec.get("raw_title", "")):
            continue
        para_sem: dict[str, str] = {
            p["para_id"]: p.get("parser_semantic", "")
            for p in sec.get("paragraphs", [])
        }
        for role in sec.get("roles", []):
            bullet_ids = role.get("bullet_para_ids", [])
            if len(bullet_ids) < _OVERMERGE_BULLET_THRESHOLD:
                continue
            role_meta_pids = [pid for pid in bullet_ids if para_sem.get(pid) == "role_meta"]
            if len(role_meta_pids) >= 2:
                issues.append(Issue(
                    code="EXPERIENCE_OVERMERGED_ROLE",
                    detail=(
                        f"Role has {len(bullet_ids)} bullet_para_ids with "
                        f"{len(role_meta_pids)} role_meta entries — likely multi-job merge "
                        f"(first: {role_meta_pids[0]})"
                    ),
                    section_id=sec["section_id"],
                    role_id=role["role_id"],
                ))
    return issues


# ---------------------------------------------------------------------------
# Category B detectors
# ---------------------------------------------------------------------------

def detect_bullet_not_recognized(sections: list[dict]) -> list[Issue]:
    """Text starts with a bullet character but semantic is not 'bullet'."""
    issues: list[Issue] = []
    for sec in sections:
        for p in sec.get("paragraphs", []):
            sem = p.get("parser_semantic")
            if sem in ("bullet", "section_heading", "role_header"):
                continue
            text = p.get("text", "")
            if _BULLET_PREFIX_RE.match(text):
                issues.append(Issue(
                    code="BULLET_NOT_RECOGNIZED",
                    detail=f"'{text.strip()[:60]}' looks like a bullet but is '{sem}'",
                    section_id=sec["section_id"],
                    para_id=p["para_id"],
                ))
    return issues


def detect_role_grouping_weak(
    sections: list[dict], para_map: dict[str, str]
) -> list[Issue]:
    """Role has 2+ header paras and 0 bullet paras — possibly weak role grouping."""
    issues: list[Issue] = []
    for sec in sections:
        for role in sec.get("roles", []):
            header_count = len(role.get("header_para_ids", []))
            bullet_count = len(role.get("bullet_para_ids", []))
            if header_count >= 2 and bullet_count == 0:
                header_texts = [para_map.get(pid, "")[:30] for pid in role["header_para_ids"]]
                issues.append(Issue(
                    code="ROLE_GROUPING_WEAK",
                    detail=(
                        f"Role has {header_count} header paras and 0 bullets "
                        f"(headers: {header_texts})"
                    ),
                    section_id=sec["section_id"],
                    role_id=role["role_id"],
                ))
    return issues


def detect_table_column_layout(data: dict) -> list[Issue]:
    """Emit informational issues for the multi-column layout fix.

    TABLE_COLUMN_LAYOUT_DETECTED — fix was applied (contamination resolved).
    TABLE_COLUMN_REORDER_ABORTED — fix detected but was rolled back by quality check.
    TABLE_COLUMN_CONTAMINATION_SUSPECTED — fix not applied but contamination signals found.
    """
    issues: list[Issue] = []
    meta = data.get("table_column_layout_meta", {})

    if data.get("table_column_layout_fixed"):
        col_count = meta.get("col_count", "?")
        hpp = meta.get("headings_per_col", {})
        ppc = meta.get("paras_per_col", {})
        hpp_str = "; ".join(
            f"col{k}={v}" for k, v in sorted(hpp.items())
        ) if hpp else "n/a"
        ppc_str = "; ".join(
            f"col{k}={v}" for k, v in sorted(ppc.items())
        ) if ppc else "n/a"
        issues.append(Issue(
            code="TABLE_COLUMN_LAYOUT_DETECTED",
            detail=(
                f"Multi-column newspaper layout detected and fixed "
                f"(cols={col_count}, headings=[{hpp_str}], paras=[{ppc_str}])"
            ),
        ))
        return issues

    if meta.get("aborted"):
        issues.append(Issue(
            code="TABLE_COLUMN_REORDER_ABORTED",
            detail=f"Multi-column reorder detected but aborted: {meta.get('reason', 'unknown')}",
        ))

    return issues


_SKILLS_DENSITY_WORDS: frozenset[str] = frozenset({
    "networking", "operating", "systems", "cross-platform", "encryption",
    "testing", "integration", "critical", "management", "databases",
    "proficiency", "proficiencies", "adobe", "photoshop", "illustrator",
    "figma", "react", "angular", "javascript", "python", "java", "sql",
    "linux", "windows", "macos", "agile", "scrum", "git", "docker",
})


def detect_table_column_contamination_suspected(sections: list[dict]) -> list[Issue]:
    """Detect likely cross-column contamination: skills-like dense text inside
    non-skills sections (certification, education, etc.)."""
    issues: list[Issue] = []
    for sec in sections:
        sem = sec.get("semantic_type", "")
        if sem in ("experience", "skills", "other"):
            continue
        title_lower = sec.get("raw_title", "").lower()
        if "skill" in title_lower or "technical" in title_lower:
            continue
        # Check for skill-like dense text paragraphs
        suspicious: list[str] = []
        for p in sec.get("paragraphs", []):
            text = p.get("text", "").strip()
            if len(text) < 20:
                continue
            words = set(re.split(r"\W+", text.lower())) - {""}
            skill_hits = len(words & _SKILLS_DENSITY_WORDS)
            if skill_hits >= 2:
                suspicious.append(text[:60])
        if suspicious:
            issues.append(Issue(
                code="TABLE_COLUMN_CONTAMINATION_SUSPECTED",
                detail=(
                    f"Section '{sec.get('raw_title', '')}' contains "
                    f"{len(suspicious)} skills-like paragraph(s); "
                    f"possible cross-column contamination: {suspicious[0]!r}"
                ),
                section_id=sec.get("section_id", ""),
            ))
    return issues


def detect_label_column(data: dict) -> list[Issue]:
    """Emit one informational issue when the label-column layout fix was applied."""
    if data.get("label_column_fixed"):
        return [Issue(
            code="LABEL_COLUMN_DETECTED",
            detail=(
                "Document uses a 2-column label-column layout; parser reordered "
                "section headings to align with right-column content."
            ),
        )]
    return []


def detect_role_like_grouping_non_experience(sections: list[dict]) -> list[Issue]:
    """Non-experience section has role-like grouping.

    The parser may emit role-like structure for sections whose final semantics
    (projects, additional, other, etc.) are determined by the LLM at
    classification time.  This is intentional parser behaviour, not a bug.
    """
    issues: list[Issue] = []
    for sec in sections:
        if _is_experience_section(sec.get("raw_title", "")):
            continue
        if sec.get("roles"):
            issues.append(Issue(
                code="ROLE_LIKE_GROUPING_NON_EXPERIENCE",
                detail=(
                    f"Section '{sec.get('raw_title', '')}' has {len(sec['roles'])} "
                    f"role(s) but is not experience-titled; "
                    f"LLM will resolve final section semantics"
                ),
                section_id=sec["section_id"],
            ))
    return issues


# ---------------------------------------------------------------------------
# Per-file analysis
# ---------------------------------------------------------------------------

def analyse_file(path: Path) -> FileReport:
    with open(path, encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)

    sections: list[dict] = data.get("sections", [])
    para_map = _build_para_map(sections)

    report = FileReport(
        path=str(path),
        source_kind=data.get("source_kind", "unknown"),
        document_id=data.get("document_id", path.stem),
    )

    all_issues: list[Issue] = []
    all_issues.extend(detect_experience_no_roles(sections))
    all_issues.extend(detect_merged_section(sections, para_map))
    all_issues.extend(detect_heading_not_recognized(sections))
    all_issues.extend(detect_role_header_contains_date(sections, para_map))
    all_issues.extend(detect_experience_role_boundary_inside_bullets(sections))
    all_issues.extend(detect_experience_overmerged_role(sections))
    all_issues.extend(detect_bullet_not_recognized(sections))
    all_issues.extend(detect_role_grouping_weak(sections, para_map))
    all_issues.extend(detect_role_like_grouping_non_experience(sections))
    all_issues.extend(detect_label_column(data))
    all_issues.extend(detect_table_column_layout(data))
    all_issues.extend(detect_table_column_contamination_suspected(sections))

    for issue in all_issues:
        if _category(issue.code) == _CAT_A:
            report.category_a_issues.append(issue)
        else:
            report.category_b_issues.append(issue)

    return report


# ---------------------------------------------------------------------------
# Aggregate JSON payload
# ---------------------------------------------------------------------------

def _build_json_payload(reports: list[FileReport]) -> dict:
    total = len(reports)
    clean_count = sum(1 for r in reports if r.status == "clean")
    clean_with_info_count = sum(1 for r in reports if r.status == "clean_with_info")
    parser_issue_count = sum(1 for r in reports if r.status == "parser_issue")

    total_a_score = sum(r.parser_issue_score for r in reports)
    total_b_score = sum(r.info_score for r in reports)

    ratio = parser_issue_count / total if total else 0.0
    if parser_issue_count == 0:
        health_status = "ok"
    elif ratio < 0.15:
        health_status = "warning"
    elif ratio < 0.30:
        health_status = "degraded"
    else:
        health_status = "critical"

    # Aggregate category A and B code counts
    a_codes: Counter = Counter()
    b_codes: Counter = Counter()
    for r in reports:
        for i in r.category_a_issues:
            a_codes[i.code] += 1
        for i in r.category_b_issues:
            b_codes[i.code] += 1

    # Sort files: parser_issue first, then clean_with_info, then clean; ties by score desc
    _STATUS_ORDER = {"parser_issue": 0, "clean_with_info": 1, "clean": 2}
    sorted_reports = sorted(
        reports,
        key=lambda r: (_STATUS_ORDER.get(r.status, 3), -r.parser_issue_score, -r.info_score),
    )

    return {
        "scanned": total,
        "clean_count": clean_count,
        "clean_with_info_count": clean_with_info_count,
        "parser_issue_count": parser_issue_count,
        "health": {
            "status": health_status,
            "parser_issue_score": total_a_score,
            "info_score": total_b_score,
        },
        "issue_categories": {
            "A_true_parser_bugs": {
                "count": parser_issue_count,
                "codes": dict(a_codes.most_common()),
            },
            "B_structural_candidates": {
                "count": clean_with_info_count,
                "codes": dict(b_codes.most_common()),
            },
        },
        "files": [r.to_dict() for r in sorted_reports],
    }


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

def _format_summary(reports: list[FileReport], input_dirs: list[Path]) -> str:
    lines: list[str] = []
    W = 72

    lines.append("=" * W)
    lines.append("PARSER DIAGNOSTICS SUMMARY")
    lines.append("=" * W)
    lines.append(f"Scanned: {len(reports)}")
    for d in input_dirs:
        lines.append(f"  {d}")
    lines.append("")

    parser_issues = [r for r in reports if r.status == "parser_issue"]
    clean_with_info = [r for r in reports if r.status == "clean_with_info"]
    fully_clean = [r for r in reports if r.status == "clean"]

    total_a_score = sum(r.parser_issue_score for r in reports)
    total_b_score = sum(r.info_score for r in reports)

    lines.append(f"Clean:                          {len(fully_clean)}")
    lines.append(f"Clean with informational notes: {len(clean_with_info)}")
    lines.append(f"Parser issues:                  {len(parser_issues)}")
    lines.append("")
    lines.append(f"Parser issue score: {total_a_score}")
    lines.append(f"Informational score: {total_b_score}")
    lines.append("")

    def _file_block(r: FileReport, issues: list[Issue]) -> None:
        name = Path(r.path).name
        lines.append(f"\n  {name}  ({r.source_kind})")
        counts = Counter(i.code for i in issues)
        for code, cnt in sorted(counts.items()):
            w = _weight(code)
            lines.append(f"    {code} x{cnt}  (w={w})")
        seen: set[str] = set()
        for i in issues:
            if i.code not in seen:
                seen.add(i.code)
                detail = i.detail if len(i.detail) <= 90 else i.detail[:87] + "..."
                loc = " | ".join(filter(None, [i.section_id, i.para_id, i.role_id]))
                lines.append(f"      → {detail}")
                if loc:
                    lines.append(f"        @ {loc}")

    if parser_issues:
        parser_issues_sorted = sorted(parser_issues, key=lambda r: -r.parser_issue_score)
        lines.append("─" * W)
        lines.append("PARSER ISSUES  (Category A — true parser bugs)")
        lines.append("─" * W)
        for r in parser_issues_sorted:
            _file_block(r, r.category_a_issues)

    if clean_with_info:
        cinfo_sorted = sorted(clean_with_info, key=lambda r: -r.info_score)
        lines.append("")
        lines.append("─" * W)
        lines.append("INFORMATIONAL WARNINGS  (Category B — structural signals for LLM resolution)")
        lines.append("  These are NOT parser failures.  LLM/classifier resolves them at runtime.")
        lines.append("─" * W)
        for r in cinfo_sorted:
            _file_block(r, r.category_b_issues)

    if fully_clean:
        lines.append("")
        lines.append("─" * W)
        lines.append("CLEAN FILES  (no issues)")
        lines.append("─" * W)
        for r in sorted(fully_clean, key=lambda r: Path(r.path).name):
            lines.append(f"  {Path(r.path).name}  ({r.source_kind})")

    lines.append("")
    lines.append("=" * W)
    lines.append("Issue code reference:")
    for code, (cat, w, sev) in sorted(_ISSUE_METADATA.items()):
        lines.append(f"  [{cat}] {code:<45} w={w}  sev={sev}")
    lines.append("=" * W)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_input_dirs() -> list[Path]:
    base = Path("tmp/artefacts/classification/llm-input")
    return [base / "docx", base / "pdf"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan parser-produced llm-input JSON files for structural issues."
    )
    parser.add_argument(
        "--input", "-i",
        metavar="DIR",
        action="append",
        help=(
            "Directory to scan (may be repeated). "
            "Defaults to tmp/artefacts/classification/llm-input/{docx,pdf}."
        ),
    )
    parser.add_argument(
        "--json", "-j",
        metavar="FILE",
        default="tmp/artefacts/classification/parser_diagnostics.json",
        help="Path for machine-readable JSON report.",
    )
    parser.add_argument(
        "--txt", "-t",
        metavar="FILE",
        default="tmp/artefacts/classification/parser_diagnostics_summary.txt",
        help="Path for human-readable text summary.",
    )
    args = parser.parse_args(argv)

    input_dirs: list[Path] = [Path(d) for d in args.input] if args.input else _default_input_dirs()

    json_out = Path(args.json)
    txt_out = Path(args.txt)
    json_out.parent.mkdir(parents=True, exist_ok=True)
    txt_out.parent.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    for d in input_dirs:
        if d.exists():
            files.extend(sorted(d.rglob("*.json")))
        else:
            print(f"Warning: directory not found: {d}", file=sys.stderr)

    if not files:
        print("No JSON files found. Run classify_samples.cmd/sh first.", file=sys.stderr)
        return 1

    reports: list[FileReport] = []
    for f in files:
        try:
            reports.append(analyse_file(f))
        except Exception as exc:
            print(f"Error processing {f}: {exc}", file=sys.stderr)

    json_payload = _build_json_payload(reports)
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2, ensure_ascii=False)

    summary = _format_summary(reports, input_dirs)
    with open(txt_out, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n")

    safe = summary.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8"
    )
    print(safe)
    print(f"\nJSON report : {json_out}")
    print(f"Text summary: {txt_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
