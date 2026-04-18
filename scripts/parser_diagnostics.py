"""
scripts/parser_diagnostics.py

Deterministic QA tool that scans parser-produced llm-input JSON files and
flags likely parser structural problems before LLM classification runs.

Use cases:
  - Track parser fix progress over time
  - Quickly identify broken samples after parser changes
  - Separate parser problems from classifier problems

Usage:
  python scripts/parser_diagnostics.py
  python scripts/parser_diagnostics.py --input tmp/artefacts/classification/llm-input
  python scripts/parser_diagnostics.py --input path/to/dir --json out.json --txt out.txt

Reads:  tmp/artefacts/classification/llm-input/{docx,pdf}/*.json  (by default)
Writes: tmp/artefacts/classification/parser_diagnostics.json
        tmp/artefacts/classification/parser_diagnostics_summary.txt

Issue codes and weights
-----------------------
EXPERIENCE_NO_ROLES       (w=3)  Experience section has no roles[].
MERGED_SECTION_SUSPECTED  (w=4)  Experience body contains text that looks like
                                  a later top-level section heading.
HEADING_NOT_RECOGNIZED    (w=2)  Paragraph text looks like a section heading
                                  but parser_semantic is 'paragraph'.
BULLET_NOT_RECOGNIZED     (w=1)  Paragraph text looks like a bullet but
                                  parser_semantic is not 'bullet'.
ROLE_HEADER_CONTAINS_DATE (w=2)  A role header para_id maps to text containing
                                  a date-like pattern (header/meta mixup).
ROLE_GROUPING_WEAK        (w=2)  Role has multiple header paras and zero
                                  bullets (suggests weak role grouping).

Suspicion score = sum(issue_count * weight) for each issue type.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_ISSUE_WEIGHTS: dict[str, int] = {
    "EXPERIENCE_NO_ROLES": 3,
    "MERGED_SECTION_SUSPECTED": 4,
    "HEADING_NOT_RECOGNIZED": 2,
    "BULLET_NOT_RECOGNIZED": 1,
    "ROLE_HEADER_CONTAINS_DATE": 2,
    "ROLE_GROUPING_WEAK": 2,
}

# Section titles that indicate a paragraph has been wrongly absorbed into
# an experience section instead of becoming its own top-level section.
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

# Words commonly found in section titles (for HEADING_NOT_RECOGNIZED).
_HEADING_TRIGGER_WORDS: frozenset[str] = frozenset({
    "experience", "education", "skills", "summary", "objective",
    "certifications", "projects", "references", "awards", "profile",
    "leadership", "communication", "publications", "languages",
    "employment", "qualifications", "volunteer", "interests",
    "career", "background", "contact", "about",
})

# Bullet prefix characters/patterns.
_BULLET_PREFIX_RE = re.compile(
    r"^(\s*[-•·–*\u25cf\u25e6\u2022\u2013][ \t]|^\s*\d+[.)]\s)",
    re.UNICODE,
)

# Date-like patterns for ROLE_HEADER_CONTAINS_DATE.
_DATE_RE = re.compile(
    r"\b(19|20)\d{2}\b"                       # 4-digit year
    r"|20[Xx]{2}"                              # placeholder 20XX
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b"  # month abbrev
    r"|\bpresent\b"                            # "present" as end date
    r"|[-\u2013\u2014]\s*(present|current)\b", # "– Present"
    re.IGNORECASE,
)

# Experience-like section titles.
_EXPERIENCE_TITLES: frozenset[str] = frozenset({
    "experience", "experiences", "work experience", "professional experience",
    "employment history", "employment", "career history", "work history",
    "professional background", "employment summary", "work summary",
    "experience summary",
})


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


@dataclass
class FileReport:
    path: str
    source_kind: str
    document_id: str
    issues: list[Issue] = field(default_factory=list)
    suspicion_score: int = 0

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "source_kind": self.source_kind,
            "document_id": self.document_id,
            "suspicion_score": self.suspicion_score,
            "issues": [
                {
                    "code": i.code,
                    "detail": i.detail,
                    "section_id": i.section_id or None,
                    "para_id": i.para_id or None,
                    "role_id": i.role_id or None,
                }
                for i in self.issues
            ],
        }


# ---------------------------------------------------------------------------
# Detectors
# ---------------------------------------------------------------------------

def _build_para_map(sections: list[dict]) -> dict[str, str]:
    """Map para_id → text across all sections."""
    result: dict[str, str] = {}
    for sec in sections:
        for p in sec.get("paragraphs", []):
            result[p["para_id"]] = p.get("text", "")
    return result


def _is_experience_section(title: str) -> bool:
    return title.strip().lower() in _EXPERIENCE_TITLES


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
                # Sentence fragments (lowercase first char) are not headings.
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
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") != "paragraph":
                continue
            text = p.get("text", "").strip()
            if not text or len(text) > 60:
                continue
            # Sentence fragments that end up as short paragraphs in PDFs
            # (e.g. "projects" as the last word of a wrapped line) are
            # not headings.  Genuine missed headings always start uppercase.
            if not text[0].isupper():
                continue
            words = set(re.split(r"[\s/&,]+", text.lower()))
            words.discard("")
            if words & _HEADING_TRIGGER_WORDS and text.lower() in _SECTION_HEADING_WORDS:
                issues.append(Issue(
                    code="HEADING_NOT_RECOGNIZED",
                    detail=f"'{text}' looks like a section heading but is marked 'paragraph'",
                    section_id=sec["section_id"],
                    para_id=p["para_id"],
                ))
    return issues


def detect_bullet_not_recognized(sections: list[dict]) -> list[Issue]:
    """Text starts with a bullet character but semantic is not 'bullet'."""
    issues: list[Issue] = []
    for sec in sections:
        for p in sec.get("paragraphs", []):
            if p.get("parser_semantic") == "bullet":
                continue
            text = p.get("text", "")
            if _BULLET_PREFIX_RE.match(text):
                issues.append(Issue(
                    code="BULLET_NOT_RECOGNIZED",
                    detail=f"'{text.strip()[:60]}' looks like a bullet but is '{p.get('parser_semantic')}'",
                    section_id=sec["section_id"],
                    para_id=p["para_id"],
                ))
    return issues


_DATE_STRIP_RE = re.compile(
    r"\b(19|20)\d{2}\b|20[Xx]{2}"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b"
    r"|\bpresent\b|\bcurrent\b"
    r"|[-\u2013\u2014/]"
    r"|\bto\b",
    re.IGNORECASE,
)


def detect_role_header_contains_date(
    sections: list[dict], para_map: dict[str, str]
) -> list[Issue]:
    """A role header paragraph is predominantly date-like text (header/meta mixup).

    Only flags when stripping date patterns and connectors leaves very little
    residual text (< 4 words).  This avoids false positives on legitimate
    pipe-separated headers like "Company Name | Aug 2016 – Present" where a
    date is intentionally part of the header format.
    """
    issues: list[Issue] = []
    for sec in sections:
        for role in sec.get("roles", []):
            for pid in role.get("header_para_ids", []):
                text = para_map.get(pid, "")
                if not _DATE_RE.search(text):
                    continue
                # Strip date tokens and count remaining substantive words.
                residual = _DATE_STRIP_RE.sub(" ", text)
                # Keep only substantive words (length > 2, not punctuation/connectors).
                residual_words = [
                    w for w in residual.split()
                    if len(w) > 2 and w not in ("|", "&", "and", "AND", "the", "THE")
                ]
                if len(residual_words) < 2:
                    issues.append(Issue(
                        code="ROLE_HEADER_CONTAINS_DATE",
                        detail=(
                            f"Role header para '{text.strip()[:60]}' looks like a "
                            f"date-only line (residual after date removal: "
                            f"'{' '.join(residual_words)}')"
                        ),
                        section_id=sec["section_id"],
                        para_id=pid,
                        role_id=role["role_id"],
                    ))
    return issues


def detect_role_grouping_weak(
    sections: list[dict], para_map: dict[str, str]
) -> list[Issue]:
    """Role has 2+ header paras and 0 bullet paras — likely weak role grouping."""
    issues: list[Issue] = []
    for sec in sections:
        for role in sec.get("roles", []):
            header_count = len(role.get("header_para_ids", []))
            bullet_count = len(role.get("bullet_para_ids", []))
            if header_count >= 2 and bullet_count == 0:
                header_texts = [
                    para_map.get(pid, "")[:30] for pid in role["header_para_ids"]
                ]
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

    report.issues.extend(detect_experience_no_roles(sections))
    report.issues.extend(detect_merged_section(sections, para_map))
    report.issues.extend(detect_heading_not_recognized(sections))
    report.issues.extend(detect_bullet_not_recognized(sections))
    report.issues.extend(detect_role_header_contains_date(sections, para_map))
    report.issues.extend(detect_role_grouping_weak(sections, para_map))

    score = 0
    for issue in report.issues:
        score += _ISSUE_WEIGHTS.get(issue.code, 1)
    report.suspicion_score = score

    return report


# ---------------------------------------------------------------------------
# Summary formatting
# ---------------------------------------------------------------------------

def _format_summary(reports: list[FileReport], input_dirs: list[Path]) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append("PARSER DIAGNOSTICS SUMMARY")
    lines.append("=" * 72)
    lines.append(f"Scanned {len(reports)} file(s) from:")
    for d in input_dirs:
        lines.append(f"  {d}")
    lines.append("")

    clean = [r for r in reports if not r.issues]
    flagged = [r for r in reports if r.issues]
    flagged.sort(key=lambda r: r.suspicion_score, reverse=True)

    lines.append(
        f"Results: {len(flagged)} flagged, {len(clean)} clean "
        f"(total suspicion score: {sum(r.suspicion_score for r in reports)})"
    )
    lines.append("")

    if flagged:
        lines.append("─" * 72)
        lines.append("FLAGGED FILES  (sorted by suspicion score, highest first)")
        lines.append("─" * 72)
        for r in flagged:
            name = Path(r.path).name
            lines.append(f"\n[score={r.suspicion_score}]  {name}  ({r.source_kind})")
            from collections import Counter
            counts = Counter(i.code for i in r.issues)
            for code, cnt in sorted(counts.items()):
                w = _ISSUE_WEIGHTS.get(code, 1)
                lines.append(f"  {code} x{cnt}  (weight={w})")
            # Print first occurrence details for each code
            seen_codes: set[str] = set()
            for i in r.issues:
                if i.code not in seen_codes:
                    seen_codes.add(i.code)
                    detail = i.detail
                    if len(detail) > 90:
                        detail = detail[:87] + "..."
                    loc = " | ".join(filter(None, [i.section_id, i.para_id, i.role_id]))
                    lines.append(f"    → {detail}")
                    if loc:
                        lines.append(f"      @ {loc}")

    if clean:
        lines.append("")
        lines.append("─" * 72)
        lines.append("CLEAN FILES")
        lines.append("─" * 72)
        for r in clean:
            lines.append(f"  {Path(r.path).name}  ({r.source_kind})")

    lines.append("")
    lines.append("=" * 72)
    lines.append("Issue code reference:")
    for code, weight in sorted(_ISSUE_WEIGHTS.items()):
        lines.append(f"  {code:<35} weight={weight}")
    lines.append("=" * 72)
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

    # Collect all JSON files
    files: list[Path] = []
    for d in input_dirs:
        if d.exists():
            files.extend(sorted(d.rglob("*.json")))
        else:
            print(f"Warning: directory not found: {d}", file=sys.stderr)

    if not files:
        print("No JSON files found. Run parse_samples.cmd/sh first.", file=sys.stderr)
        return 1

    reports: list[FileReport] = []
    for f in files:
        try:
            reports.append(analyse_file(f))
        except Exception as exc:
            print(f"Error processing {f}: {exc}", file=sys.stderr)

    # Write JSON report
    json_payload = {
        "scanned": len(reports),
        "flagged": sum(1 for r in reports if r.issues),
        "total_suspicion_score": sum(r.suspicion_score for r in reports),
        "files": [r.to_dict() for r in sorted(reports, key=lambda r: r.suspicion_score, reverse=True)],
    }
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(json_payload, f, indent=2, ensure_ascii=False)

    # Write text summary
    summary = _format_summary(reports, input_dirs)
    with open(txt_out, "w", encoding="utf-8") as f:
        f.write(summary)
        f.write("\n")

    # Also print to stdout (replace un-encodable chars so Windows cp1252 doesn't crash)
    safe = summary.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
        sys.stdout.encoding or "utf-8"
    )
    print(safe)
    print(f"\nJSON report : {json_out}")
    print(f"Text summary: {txt_out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
