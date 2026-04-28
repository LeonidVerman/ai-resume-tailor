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
  TABLE_COLUMN_LAYOUT_DETECTED             (w=1) Band-aware fix applied; left/right streams separated.
  TABLE_COLUMN_REORDER_ABORTED             (w=1) Fix detected but rolled back (quality check failed).
  TABLE_COLUMN_CONTAMINATION_SUSPECTED     (w=1) Umbrella: skills-list in certifications when no more
                                                  specific code applies.  Requires multicolumn context.
  TABLE_CONTACT_INSIDE_EXPERIENCE          (w=2) Standalone phone/email/URL in orphan experience para.
  TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION   (w=1) Skills-list in certifications (multicolumn context).
  TABLE_EDUCATION_INSIDE_EXPERIENCE        (w=2) Degree entry in orphan experience para (explicit
                                                  degree signal required; institution alone insufficient).
  TABLE_ORG_NAME_PROMOTED_TO_FAKE_SECTION  (w=1) Org name became a top-level section (not absorbed).

Notes on false-positive reduction (v3):
  Education / contact detectors (strict context-aware mode):
  - Only inspect paragraphs with parser_semantic == "paragraph" (orphan paras).
  - Skip role_header, role_meta, bullet, section_heading, empty semantics entirely.
  - Skip paragraphs whose para_id appears in any role's header/meta/bullet lists.
  _is_education_line: requires explicit degree signal (abbreviation B.S./M.S./PhD/MBA
    or degree phrase "Bachelor of Science", "Master's degree", GPA, coursework).
    Institution keywords alone (university, school, college) do NOT trigger — they
    appear in company names.  Action verbs and | separators are excluded early.
  _is_contact_para: requires STANDALONE contact line using full-line anchors.
    Embedded emails/URLs in sentences and company+address combinations are not flagged.
  Skills / contamination detectors:
  - Only run when has_multicolumn=True (multicolumn layout detected).
  - Restricted to certifications sections.
  - Skip section_heading paragraphs.
  - Deduplicated: TABLE_COLUMN_CONTAMINATION_SUSPECTED suppressed when specific
    TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION already covers the section.
  Summary formatter: at most 3 examples per code per file.
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
    "TABLE_COLUMN_REORDER_ABORTED":            (_CAT_B, 1, "info"),
    "TABLE_COLUMN_CONTAMINATION_SUSPECTED":    (_CAT_B, 1, "info"),
    "TABLE_CONTACT_INSIDE_EXPERIENCE":         (_CAT_B, 2, "low"),
    "TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION":  (_CAT_B, 1, "info"),
    "TABLE_EDUCATION_INSIDE_EXPERIENCE":       (_CAT_B, 2, "low"),
    "TABLE_ORG_NAME_PROMOTED_TO_FAKE_SECTION": (_CAT_B, 1, "info"),
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


_ACTION_VERBS: frozenset[str] = frozenset({
    "developed", "built", "implemented", "led", "designed", "created",
    "improved", "managed", "architected", "deployed", "delivered",
    "collaborated", "optimized", "maintained", "integrated", "migrated",
    "automated", "reduced", "increased", "launched", "established",
    "coordinated", "analyzed", "analysed", "resolved", "supported",
    "mentored", "spearheaded", "streamlined", "facilitated", "engineered",
    "authored", "assisted", "participated", "conducted", "oversaw",
    "supervised", "directed", "administered", "operated", "executed",
    "planned", "monitored", "evaluated", "trained", "prepared", "produced",
})

_CONTACT_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"^\+?\d[\d\s\-().]{6,}$"),           # standalone phone number
    re.compile(r"^[\w.+-]+@[\w-]+\.\w{2,}$"),         # standalone email address
    re.compile(r"^(https?://|www\.)\S+$", re.I),       # standalone URL
    re.compile(                                        # labelled contact at start of line
        r"^(email|e-mail|phone|mobile|landline|tel|website|linkedin|github|twitter)"
        r"\s*[:\-]\s*\S",
        re.IGNORECASE,
    ),
)

_INSTITUTION_WORDS: frozenset[str] = frozenset({
    "university", "college", "school", "institute", "academy", "polytechnic",
})

_ORG_NAME_INDICATORS: frozenset[str] = frozenset({
    "inc", "corp", "co", "ltd", "llc", "company", "tech", "technologies",
    "university", "college", "school", "institute",
})

# Sections that naturally contain contact / education / reference text.
# Skills-list diagnostics are suppressed for these sections.
_NON_SKILLS_SKIP_TITLES: frozenset[str] = frozenset({
    "contact", "contact info", "contact information", "personal information",
    "personal details", "references", "personal references", "professional references",
    "education", "educational history", "academic background", "degrees",
    "educational background", "academic credentials",
    "languages", "language skills",
    "websites", "profiles", "links", "portfolio",
    "summary", "professional summary", "objective", "profile", "about me",
})

_NARRATIVE_CONNECTORS = (
    " to ", " for ", " with ", " using ", " by ", " in order to ",
    " through ", " across ", " within ", " on behalf of ",
)


def _is_contact_para(text: str) -> bool:
    """Return True when text is a STANDALONE contact-info line.

    The contact signal must dominate the text — embedded contact info inside
    sentences or company+address combinations is NOT flagged.  All four
    patterns use full-line anchors (^…$) so partial matches don't trigger.
    """
    t = text.strip()
    return any(pat.match(t) for pat in _CONTACT_PATTERNS)


def _is_skills_list_para(text: str) -> bool:
    """Return True ONLY when text strongly resembles a skills/technology list.

    Strict criteria designed to minimise false positives.  A paragraph is
    considered skills-like when it passes ALL exclusion checks AND has at
    least ONE strong list structural signal.

    Exclusions (immediately return False):
    - Fewer than 4 space-separated tokens  (rules out role titles, 2-word headings)
    - Fewer than 20 characters total
    - Paragraph semantic is section_heading (checked by caller, not here)
    - Contains a year / date range (date lines are not skills)
    - Matches contact-info patterns (phone, email, URL, labelled contact)
    - First word is an action verb (experience bullets start with verbs)
    - Full sentence: ≥8 tokens and ends with .!?
    - Contains narrative connectors (to/for/with/using/by/through …)
    - Contains institution keywords (university, college, school …)
    - Short title-case phrase ≤4 tokens with no separators (role titles, section names)
    - Matches person-name pattern (2–4 Capitalised words, no digits/punctuation)

    List signals (at least one required):
    - Comma/semicolon-separated: ≥2 separators and ≥4 tokens
    - Space-separated keyword list: ≥6 tokens, no sentence punctuation
    """
    t = text.strip()
    tokens = t.split()

    # Hard minimums
    if len(tokens) < 4 or len(t) < 20:
        return False

    t_lower = t.lower()

    # Date lines are not skills
    if _DATE_RE.search(t):
        return False

    # Contact info
    if _is_contact_para(t):
        return False

    # Action verb sentence (experience bullet)
    first_word = re.split(r"\W+", t_lower)[0]
    if first_word in _ACTION_VERBS:
        return False

    # Full sentence ending with punctuation
    if len(tokens) >= 8 and t[-1] in ".!?":
        return False

    # Narrative connectors → this is a sentence, not a list
    if any(c in t_lower for c in _NARRATIVE_CONNECTORS):
        return False

    # Institution names → education/reference text
    if any(w in t_lower for w in _INSTITUTION_WORDS):
        return False

    # Short title-case phrase with no separators (role title: "Senior Engineer")
    if len(tokens) <= 4 and "," not in t and ";" not in t:
        cap = sum(1 for w in tokens if w and w[0].isupper())
        if cap == len(tokens):
            return False

    # Person-name pattern (2–4 capitalised words, no digits or special chars)
    if 2 <= len(tokens) <= 4 and re.match(r'^([A-Z][a-z]+ ){1,3}[A-Z][a-z]+$', t):
        return False

    # ── List signals (at least one required) ──────────────────────────────
    separators = t.count(",") + t.count(";")
    if separators >= 2 and len(tokens) >= 4:
        return True

    # Space-separated keyword list: many tokens, no sentence punctuation
    if len(tokens) >= 6 and t[-1] not in ".!?":
        return True

    return False


# Degree abbreviations (anchored to word boundaries so "bs" alone doesn't match)
_DEGREE_ABBREV_RE = re.compile(
    r"\b(b\.?s\.?|m\.?s\.?|b\.?a\.?|m\.?a\.?|ph\.?d\.?|m\.?b\.?a\.|b\.?sc\.?|m\.?sc\.?)\b",
    re.IGNORECASE,
)
# Degree words and explicit academic phrases
_DEGREE_KEYWORD_RE = re.compile(
    r"\b(bachelor|master|doctorate|diploma|associate)\s+(of|in|degree)\b"
    r"|\b(bachelor|master|doctorate|diploma|associate)'?s\b"
    r"|\bGPA\b"
    r"|\bcoursework\b",
    re.IGNORECASE,
)


def _is_education_line(text: str) -> bool:
    """Return True ONLY when text strongly resembles an education entry.

    Requires at least one EXPLICIT degree signal — institution keywords alone
    (university, college, school) are NOT sufficient because they appear in
    company names and role meta lines.

    Strong signals accepted:
    - Degree abbreviation: B.S., M.S., Ph.D., MBA, BSc, etc.
    - Degree phrase: "Bachelor of Science", "Master's degree", GPA, coursework

    Explicit exclusions (immediately False):
    - Action-verb sentence start (experience bullet)
    - Contains a "|" separator (role header format)
    - Starts with an all-caps token followed by nothing or only punctuation
      (section heading / company name acronym)
    """
    t = text.strip()
    if not t:
        return False
    # Role header format: "Title | Company" — never education
    if "|" in t:
        return False
    # Action verb start → experience bullet
    first_word = re.split(r"\W+", t.lower())[0]
    if first_word in _ACTION_VERBS:
        return False
    # Check for strong education signal
    if _DEGREE_ABBREV_RE.search(t):
        return True
    if _DEGREE_KEYWORD_RE.search(t):
        return True
    return False


def detect_table_column_contamination_suspected(
    sections: list[dict],
    has_multicolumn: bool,
) -> list[Issue]:
    """Emit TABLE_COLUMN_CONTAMINATION_SUSPECTED as an umbrella when skills-list
    text appears inside a certification section and multi-column context is known.

    Only runs when has_multicolumn=True to avoid false positives on normal files.
    Skips section_heading paragraphs.
    Skips sections already covered by TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION
    (deduplication is done in analyse_file).
    """
    if not has_multicolumn:
        return []

    issues: list[Issue] = []
    # Only flag certifications: skills content there is the clearest contamination signal
    for sec in sections:
        if sec.get("semantic_type", "") != "certifications":
            continue
        title_lower = sec.get("raw_title", "").lower()
        if any(kw in title_lower for kw in ("skill", "technical", "expertise")):
            continue
        suspicious: list[str] = []
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") == "section_heading":
                continue
            text = p.get("text", "").strip()
            if _is_skills_list_para(text):
                suspicious.append(text[:60])
        if suspicious:
            issues.append(Issue(
                code="TABLE_COLUMN_CONTAMINATION_SUSPECTED",
                detail=(
                    f"Section '{sec.get('raw_title', '')}' may contain skills-list text: "
                    f"{suspicious[0]!r}"
                ),
                section_id=sec.get("section_id", ""),
            ))
    return issues


# Semantics that belong to well-formed role content — never orphan contamination
_ROLE_CONTENT_SEMANTICS: frozenset[str] = frozenset({
    "role_header", "role_meta", "bullet", "section_heading", "empty",
})


def _role_para_ids(roles: list[dict]) -> set[str]:
    """Collect all para_ids assigned to role entries in a section."""
    ids: set[str] = set()
    for role in roles:
        for key in ("header_para_ids", "meta_para_ids", "bullet_para_ids"):
            ids.update(role.get(key, []))
    return ids


def detect_table_contact_inside_experience(sections: list[dict]) -> list[Issue]:
    """Flag standalone contact-info lines (phone, email, URL) inside experience.

    Only checks orphan paragraphs — paragraphs with semantic 'paragraph' that are
    NOT assigned to any role entry.  Role headers, meta lines, and bullets are
    always skipped.
    """
    issues: list[Issue] = []
    for sec in sections:
        if not _is_experience_section(sec.get("raw_title", "")):
            continue
        assigned = _role_para_ids(sec.get("roles", []))
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") in _ROLE_CONTENT_SEMANTICS:
                continue
            if p.get("parser_semantic") != "paragraph":
                continue
            para_id = p.get("para_id", "")
            if para_id and para_id in assigned:
                continue
            text = p.get("text", "").strip()
            if _is_contact_para(text):
                issues.append(Issue(
                    code="TABLE_CONTACT_INSIDE_EXPERIENCE",
                    detail=f"Contact-like text inside experience section: {text[:60]!r}",
                    section_id=sec.get("section_id", ""),
                    para_id=para_id,
                ))
    return issues


def detect_table_education_inside_experience(sections: list[dict]) -> list[Issue]:
    """Flag education entries (degree/institution lines) inside experience sections.

    Only checks orphan paragraphs — paragraphs with semantic 'paragraph' that are
    NOT assigned to any role entry.  Requires explicit degree signals; institution
    keywords alone are insufficient to avoid false positives on company names.
    """
    issues: list[Issue] = []
    for sec in sections:
        if not _is_experience_section(sec.get("raw_title", "")):
            continue
        assigned = _role_para_ids(sec.get("roles", []))
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") in _ROLE_CONTENT_SEMANTICS:
                continue
            if p.get("parser_semantic") != "paragraph":
                continue
            para_id = p.get("para_id", "")
            if para_id and para_id in assigned:
                continue
            text = p.get("text", "").strip()
            if _is_education_line(text):
                issues.append(Issue(
                    code="TABLE_EDUCATION_INSIDE_EXPERIENCE",
                    detail=f"Education-like text inside experience section: {text[:60]!r}",
                    section_id=sec.get("section_id", ""),
                    para_id=para_id,
                ))
    return issues


def detect_table_skills_inside_non_skills(
    sections: list[dict],
    has_multicolumn: bool,
) -> list[Issue]:
    """Flag skills-list paragraphs inside certifications sections.

    Only runs when has_multicolumn=True.
    Skips section_heading paragraphs and sections that naturally hold
    non-skills text (education, contact, references, languages).
    Only targets certifications semantic_type (clearest contamination case).
    """
    if not has_multicolumn:
        return []

    issues: list[Issue] = []
    for sec in sections:
        # Only certifications warrant this check — the clearest contamination case
        if sec.get("semantic_type", "") != "certifications":
            continue
        title_lower = sec.get("raw_title", "").lower()
        # Skip if the section title itself suggests skills content is expected
        if any(kw in title_lower for kw in ("skill", "technical", "expertise")):
            continue
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") == "section_heading":
                continue
            text = p.get("text", "").strip()
            if _is_skills_list_para(text):
                issues.append(Issue(
                    code="TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION",
                    detail=(
                        f"Skills-list text in '{sec.get('raw_title', '')}': {text[:60]!r}"
                    ),
                    section_id=sec.get("section_id", ""),
                    para_id=p.get("para_id", ""),
                ))
    return issues


def detect_table_org_name_fake_sections(sections: list[dict]) -> list[Issue]:
    """Flag sections that look like organization names promoted from sub-headings.

    Restricted to sections with 1–3 body paragraphs where the last paragraph
    is a date line and the title contains an org-name indicator.
    """
    issues: list[Issue] = []
    for sec in sections:
        if _is_experience_section(sec.get("raw_title", "")):
            continue
        if sec.get("roles"):
            continue
        title = sec.get("raw_title", "").strip()
        if title.lower() in _SECTION_HEADING_WORDS:
            continue
        if title.lower() in _NON_SKILLS_SKIP_TITLES:
            continue
        body_paras = sec.get("paragraphs", [])
        non_empty = [p for p in body_paras if p.get("text", "").strip()]
        if not non_empty or len(non_empty) > 3:
            continue
        title_words = set(re.split(r"\W+", title.lower())) - {""}
        last_text = non_empty[-1].get("text", "")
        if _DATE_RE.search(last_text) and title_words & _ORG_NAME_INDICATORS:
            issues.append(Issue(
                code="TABLE_ORG_NAME_PROMOTED_TO_FAKE_SECTION",
                detail=(
                    f"Section '{title}' looks like an org-name sub-heading "
                    f"(short body, date-like last line)"
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

    # Multi-column context: only run column-specific contamination detectors when
    # the parser detected and applied (or attempted) a multi-column layout fix.
    has_multicolumn = bool(
        data.get("table_column_layout_fixed")
        or data.get("table_column_layout_meta", {}).get("aborted")
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
    all_issues.extend(detect_table_contact_inside_experience(sections))
    all_issues.extend(detect_table_education_inside_experience(sections))

    # Column-contamination diagnostics: only when multi-column context is known
    skills_issues = detect_table_skills_inside_non_skills(sections, has_multicolumn)
    all_issues.extend(skills_issues)

    # De-duplicate: suppress umbrella contamination for sections already covered
    # by a specific TABLE_SKILLS_INSIDE_NON_SKILLS_SECTION issue.
    sections_with_skills_issue: set[str] = {
        i.section_id for i in skills_issues if i.section_id
    }
    contamination_issues = detect_table_column_contamination_suspected(
        sections, has_multicolumn
    )
    for issue in contamination_issues:
        if issue.section_id not in sections_with_skills_issue:
            all_issues.append(issue)

    all_issues.extend(detect_table_org_name_fake_sections(sections))

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

    _SUMMARY_MAX_EXAMPLES = 3  # max detail lines per code per file

    def _file_block(r: FileReport, issues: list[Issue]) -> None:
        name = Path(r.path).name
        lines.append(f"\n  {name}  ({r.source_kind})")
        counts = Counter(i.code for i in issues)
        for code, cnt in sorted(counts.items()):
            w = _weight(code)
            lines.append(f"    {code} x{cnt}  (w={w})")
        code_example_count: dict[str, int] = {}
        for i in issues:
            n = code_example_count.get(i.code, 0)
            if n >= _SUMMARY_MAX_EXAMPLES:
                continue
            code_example_count[i.code] = n + 1
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
