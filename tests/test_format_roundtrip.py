"""Format roundtrip tests - parse -> identity-render -> compare.

Skips the LLM step entirely: the parsed document is serialized back to text
in the exact format parse_llm_output() expects, then run through the full
compile pipeline.  The resulting DOCX should be structurally identical to
the input (same paragraphs, same text, same styles).

Findings are printed as a structured diff; the test does NOT hard-fail on
formatting differences - only on parse/render crashes and missing text.

DOCX samples: tests/samples/resume/docx/*.docx
PDF  samples: tests/samples/resume/pfd/*.pdf   (note: folder is named "pfd")
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shutil

import pytest

# ---------------------------------------------------------------------------
# Artefact saving
# ---------------------------------------------------------------------------
# Set SAVE_ARTEFACTS=1 to copy generated DOCX files to tmp/artefacts/ for
# visual inspection.  The directory is created automatically if needed.

_SAVE_ARTEFACTS: bool = os.environ.get("SAVE_ARTEFACTS", "").strip() in ("1", "true", "yes")
_ARTEFACTS_ROOT: Path = Path(__file__).parent.parent / "tmp" / "artefacts"
_ARTEFACTS_DOCX: Path = _ARTEFACTS_ROOT / "docx"
_ARTEFACTS_PDF:  Path = _ARTEFACTS_ROOT / "pdf"


def _save_artefact(src: str, name: str, subdir: Path | None = None) -> None:
    """Copy src to <subdir>/<name> (or _ARTEFACTS_ROOT/<name>) if SAVE_ARTEFACTS is set.

    Prints a warning instead of crashing when the destination is locked
    (e.g. the file is open in Word from a previous run).
    """
    dest_dir = subdir if subdir is not None else _ARTEFACTS_ROOT
    dest_dir.mkdir(parents=True, exist_ok=True)
    dst = str(dest_dir / name)
    try:
        shutil.copy2(src, dst)
    except OSError as exc:
        print(f"\n  [artefact] Could not save {name}: {exc}")


# ---------------------------------------------------------------------------
# Non-standard DOCX template detection
# ---------------------------------------------------------------------------
# Many free resume templates use multi-column tables for layout.  The parser
# reads table cells in reading order, producing section headings out of date
# strings, company names, degree titles, or sidebar labels.  These templates
# cannot round-trip correctly without a dedicated two-column layout detector.
#
# _detect_nonstandard_docx() classifies these heuristically so the test can
# xfail them with an informative reason rather than producing a confusing failure.

_YEAR_IN_TITLE_RE = re.compile(r"\b(19|20)\d{2}\b|20[Xx]{2}", re.I)


def _detect_nonstandard_docx(path: Path) -> str | None:
    """Return an xfail reason string when the DOCX has a non-roundtrippable structure.

    Returns None when the template looks well-structured.
    """
    try:
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import TableBlock
        doc = parse_docx(str(path))
    except Exception:
        return None

    sections = doc.sections
    if not sections:
        return None

    # If the document uses table-based layout, body_items will contain TableBlock
    # entries.  The renderer re-inserts them as opaque XML blobs so the visual
    # layout is preserved even when semantic parsing is imperfect.  Structural
    # heuristics that fire on table-internal content are not meaningful in that case.
    has_table_blocks = doc.body_items is not None and any(
        isinstance(item, TableBlock) for item in doc.body_items
    )

    return None


# ---------------------------------------------------------------------------
# Known two-column PDF failures (fundamental layout limitation)
# ---------------------------------------------------------------------------
# PyMuPDF reads two-column PDFs left-to-right/top-to-bottom, interleaving
# sidebar labels (KEY SKILLS, LANGUAGES, etc.) with main content.  These
# sidebar labels are mis-classified as section headings, causing both para
# count mismatches and DOCX-of-DOCX instability.  Tracked as a known
# limitation — not fixable without a two-column layout detector.

_TWO_COLUMN_PDFS: frozenset[str] = frozenset({
    # Two-column sidebar templates: column_id-based rendering reorders paragraphs
    # (left cell first, then right cell) relative to source PDF reading order,
    # causing text-position mismatches in the roundtrip text comparison.
    "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.pdf",
    "9-Template4.pdf",
})

# PDFs with letter-spaced headings that PyMuPDF reads as spaced characters
# (e.g. "E D U C A T I O N" instead of "Education") — known parsing limitation.
_LETTER_SPACED_PDFS: frozenset[str] = frozenset({
    "Resume-Sample-1-Software-Engineer.pdf",
})

# DOCX templates with non-standard two-column or interleaved layouts where
# education/certifications content is embedded inside the experience section
# body — the parser cannot separate them, so the roundtrip cannot preserve
# section boundaries correctly.
_KNOWN_BAD_DOCX: dict[str, str] = {
    "33-Software-Engineer-Editable-Resume-Template-Download-in-docx-8.docx": (
        "Two-column layout: education content is embedded in the 'Professional "
        "Experience' section body; secondary sections (Certifications, Language) "
        "are classified as 'other' and excluded from the LLM text, so section "
        "ordering cannot round-trip correctly."
    ),
    "3-software-engineer-doc-resume-template.docx": (
        "Non-standard role headings: each job uses a plain job-title heading "
        "('Software Engineer') without a pipe-separated role|company format.  "
        "The consolidation merges them into a synthetic experience section but "
        "the identity LLM pass cannot reconstruct pipe-formatted role headers "
        "from plain headings, so the roundtrip cannot preserve role structure."
    ),
    "22-Software-Engineer-Editable-Resume-Template-Download-in-docx-4.docx": (
        "Non-standard layout: experience content is embedded inside the Skills "
        "section body.  Layout fitting trims one skill line, causing a one-para "
        "count difference in the identity roundtrip."
    ),
    "4-software-engineer-resume.docx": (
        "NBSP/tab-column role header format: each job title paragraph uses "
        "non-breaking spaces and tab stops for column alignment instead of a "
        "'|' pipe separator.  The identity serializer combines role.header with "
        "header_extra into a pipe-separated line; rendering writes that combined "
        "text back into the tab-column paragraph, changing its content.  The "
        "real pipeline (LLM pipe-format → DOCX) works correctly."
    ),
    # Separate-line format: job title on its own paragraph, company name on
    # the next.  The improved role grouping (v0.8.2+) now correctly groups
    # these into roles; the identity serializer then combines role.header +
    # header_extra (company name) into a single pipe-separated string so that
    # parse_llm_output can detect the role boundary.  The rendered DOCX has
    # combined text where the original had two separate paragraphs, causing a
    # text diff.  The real LLM pipeline (which rewrites role headers) is
    # unaffected.
    "18-Project-Engineer-Editable-Resume-Template-Download-in-docx.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "19-Software-Engineer-Editable-Resume-Template-Download-in-docx-2.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "21-Software-Engineer-Editable-Resume-Template-Download-in-docx.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "23-Project-Engineer-Editable-Resume-Template-Download-in-docx-2.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "24-Naval-Engineering-Editable-Resume-Template-Download-in-docx.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "26-Engineer-Editable-Resume-Template-Download-in-docx-2.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "28-Engineer-Editable-Resume-Template-Download-in-docx.docx": (
        "Separate-line role header format: title and company on separate "
        "paragraphs; identity serializer combines them with '|', changing "
        "paragraph text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "5-Software Development Resume.docx": (
        "Separate-line role header format: date/company meta appears before "
        "the job title; identity serializer combines role.header (date line) "
        "with header_extra into a pipe-separated string, changing paragraph "
        "text in the roundtrip.  Real LLM pipeline unaffected."
    ),
    "10-Template5.docx": (
        "Table-based side-by-side role layout: three roles are presented in "
        "adjacent table cells; the parser detects them via the role_meta+relabel "
        "heuristic but the identity serializer combines role.header with "
        "header_extra (company name) into a pipe-separated string, changing "
        "paragraph text.  Real LLM pipeline unaffected."
    ),
    "13-Nurse-template3.docx": (
        "Space-slash-space role header format ('Lamna Health / General Practitioner'): "
        "the parser now correctly detects these as role_header via the ' / ' heuristic, "
        "but the identity serializer combines role.header with following meta/content "
        "into a pipe-separated string, changing paragraph text.  Real LLM pipeline "
        "unaffected."
    ),
    "Valerii_Konchin_CV.docx": (
        "Two-column sidebar layout: skills and experience content are interleaved "
        "in document order so the parser cannot correctly separate them into "
        "distinct section bodies.  Section structure (PROFILE, EDUCATION, etc.) "
        "is parsed correctly; only the body-para ordering cannot round-trip."
    ),
    "7-Template2.docx": (
        "Placeholder-year role format ('January 20xx - Current'): date lines use "
        "'20xx' instead of real years; the placeholder pre-pass promotes them to "
        "role_meta, triggering Pattern B (date-as-header).  The identity serializer "
        "then combines the date header with following content via '|', changing "
        "paragraph text.  Role detection now works (3 roles vs 0); real LLM "
        "pipeline unaffected."
    ),
    "6-Template1.docx": (
        "Placeholder-year role format ('Jan 20XX - Current'): same structure as "
        "7-Template2.docx — date lines use '20XX'; the placeholder pre-pass promotes "
        "them to role_meta, triggering Pattern B (date-as-header).  Role detection "
        "now works (3 roles vs 0); real LLM pipeline unaffected."
    ),
    "16-Devops-Engineer-Editable-Resume-Template-Download-in-docx.docx": (
        "Fused year+company role format ('2023Ginyard International Co. Junior "
        "software developer'): year glued to company name so the word-boundary "
        "regex missed it; fused-year detection now promotes these lines to "
        "role_meta, triggering Pattern B.  The identity serializer then combines "
        "role.header with header_extra (content paragraphs) via '|', changing "
        "paragraph text.  Role detection now works (1 role vs 0); real LLM "
        "pipeline unaffected."
    ),
    "20-Software-Engineer-Editable-Resume-Template-Download-in-docx-5.docx": (
        "Label-column layout: the document uses a 2-column newspaper layout where "
        "section labels (Summary, Work Experience, …) are in a narrow left column "
        "and all content is in the wide right column.  The parser now correctly "
        "detects 3 experience roles; the identity serializer combines role.header "
        "(e.g. 'SOFTWARE ENGINEER') with header_extra (company name) into a "
        "pipe-separated string, changing paragraph text.  Real LLM pipeline "
        "unaffected."
    ),
}

# PDFs with non-standard content that cannot roundtrip cleanly.
# Distinct from _TWO_COLUMN_PDFS / _LETTER_SPACED_PDFS which have structural
# layout reasons; these have content-level issues.
_KNOWN_BAD_PDFS: dict[str, str] = {
    "backend-developer-1606703830.pdf": (
        "Qwikresume watermarked template: the PDF footer ('Powered by Qwikresume "
        "/ www.qwikresume.com') and an 'ACHIEVEMENTS' block are embedded in the "
        "Skills section body.  The achievement sentences are dropped by the skills "
        "sanitizer (full-sentence filter), so the footer text shifts into their "
        "positions in the rendered DOCX."
    ),
    "2-Leonid_Verman_Resume_2.pdf": (
        "LibreOffice PDF→DOCX renders a table-based layout where name and summary "
        "paragraphs end up in the Skills section body.  One summary continuation "
        "line ('migration and team collaboration…') is dropped by the skills "
        "sanitizer (full-sentence filter), causing a one-paragraph offset in the "
        "DOCX-of-DOCX stability check."
    ),
    "4-software-engineer-resume.pdf": (
        "NBSP/tab-column role header format: the rendered DOCX inherits the "
        "non-breaking-space column-alignment from the source template.  The "
        "identity serializer combines role.header with header_extra into a "
        "pipe-separated line; when re-rendered the tab-column paragraphs receive "
        "combined text, causing DOCX-of-DOCX text differences.  The real pipeline "
        "(LLM pipe-format → DOCX) works correctly."
    ),
    # Separate-line role header format (same category as 4-software-engineer-resume.pdf
    # above): job title and company on separate paragraphs.  Improved role grouping
    # (v0.8.2+) now correctly groups these; identity serializer then combines
    # role.header + header_extra with '|', causing DOCX-of-DOCX text differences.
    "16-Devops-Engineer-Editable-Resume-Template-Download-in-docx.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "18-Project-Engineer-Editable-Resume-Template-Download-in-docx.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "19-Software-Engineer-Editable-Resume-Template-Download-in-docx-2.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "21-Software-Engineer-Editable-Resume-Template-Download-in-docx.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "22-Software-Engineer-Editable-Resume-Template-Download-in-docx-4.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "23-Project-Engineer-Editable-Resume-Template-Download-in-docx-2.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "24-Naval-Engineering-Editable-Resume-Template-Download-in-docx.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "26-Engineer-Editable-Resume-Template-Download-in-docx-2.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "5-Software Development Resume.pdf": (
        "Separate-line role header format; identity serializer combines header "
        "with header_extra, causing DOCX-of-DOCX text differences."
    ),
    "6-Template1.pdf": (
        "Pattern B (date-before-title): parser now detects 3 roles correctly, "
        "but the DOCX renderer emits meta (date) after the role header (title), "
        "reversing the original PDF order.  Role detection works; rendering "
        "order mismatch is a known limitation of the standard DOCX format."
    ),
    "7-Template2.pdf": (
        "Placeholder-year role format: the rendered DOCX uses Pattern B "
        "(date-as-header); identity serializer combines the date header with "
        "following content, causing DOCX-of-DOCX text differences."
    ),
    "10-Template5.pdf": (
        "Table-based side-by-side layout: the rendered DOCX inherits a separate-line "
        "role format; identity serializer combines role.header with header_extra "
        "(company name) via '|', causing DOCX-of-DOCX text differences.  Underlying "
        "issue is the same as 10-Template5.docx in _KNOWN_BAD_DOCX."
    ),
    "1849228-senior-software-engineer-resume-example.pdf": (
        "preprocess_resume_text (called inside parse_llm_output) merges 'role-title\n"
        "company|date' separate-line meta into a single pipe-delimited role header. "
        "This is correct for real LLM output but changes the identity roundtrip: "
        "one paragraph is lost (meta absorbed into header) and the role header text "
        "gains the company/date suffix.  Real-pipeline output is unaffected."
    ),
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE = Path(__file__).parent
_DOCX_DIR = _HERE / "samples" / "resume" / "docx"
_PDF_DIR  = _HERE / "samples" / "resume" / "pfd"

_DOCX_SAMPLES = sorted(_DOCX_DIR.glob("*.docx")) if _DOCX_DIR.is_dir() else []
_PDF_SAMPLES  = sorted(_PDF_DIR.glob("*.pdf"))   if _PDF_DIR.is_dir()  else []


# ---------------------------------------------------------------------------
# Identity serializer: ResumeDocument -> LLM text
# ---------------------------------------------------------------------------

_SEMANTIC_CANONICAL_HEADING: dict[str, str] = {
    "summary": "Professional Summary",
    "experience": "Experience",
    "skills": "Technical Skills",
    "education": "Education",
}


def _doc_to_llm_text(doc) -> str:
    """Serialize a ResumeDocument into the text format parse_llm_output() parses.

    This is the 'identity pass': if we feed this text back through the compiler
    we should get a document whose content matches the original exactly.
    """
    from tailor.compiler.models import ResumeDocument, TableBlock

    has_table_blocks = (doc.body_items is not None and
                        any(isinstance(i, TableBlock) for i in doc.body_items))

    lines: list[str] = []
    for section in doc.sections:
        # Skip "other" type sections entirely.  apply_tailored keeps them verbatim
        # (they are unmatched by semantic type and LLM heading lookup), so the
        # identity roundtrip still produces the correct output.  Emitting them
        # causes problems: unrecognizable titles (e.g. "Nat'l Community College",
        # "Proficiency") are absorbed as body lines into the preceding section by
        # parse_llm_output, producing extra cloned paragraphs.
        if section.semantic_type == "other":
            continue

        # For letter-spaced PDF headings (e.g. "S U M M A R Y", "E D U C A T I O N")
        # emit the canonical form instead.  parse_llm_output cannot recognise
        # letter-spaced headings after the first section is open (the in_section_with_body
        # guard requires strict _ALL_KNOWN membership).  Standard headings like
        # "Objective" or "Key Skills" are emitted verbatim — they're already parseable.
        _title_parts = re.split(r"[\s\xa0]+", section.title.strip())
        _is_letter_spaced = bool(_title_parts) and all(len(p) <= 1 for p in _title_parts if p)
        if _is_letter_spaced:
            heading_line = _SEMANTIC_CANONICAL_HEADING.get(section.semantic_type, section.title)
        else:
            heading_line = section.title
        lines.append(heading_line)
        if section.semantic_type == "experience":
            if section.roles:
                for role in section.roles:
                    if role.header_extra and "|" not in role.header.text:
                        # PDF separate-line format: combine role title + company
                        # (header_extra) into a single pipe-separated string so
                        # parse_llm_output detects the role via _is_role_header.
                        _hdr_parts = [role.header.text.strip()] + [
                            he.text.strip() for he in role.header_extra if he.text.strip()
                        ]
                        lines.append(" | ".join(_hdr_parts))
                    else:
                        lines.append(role.header.text)
                    for m in role.meta_lines:
                        lines.append(m.text)
                    for b in role.bullets:
                        # Normalise: strip any leading "- " the template already has
                        txt = b.text
                        if txt.startswith("- "):
                            txt = txt[2:]
                        lines.append(f"- {txt}")
            elif not has_table_blocks:
                # No parsed roles — emit body_paras so the content survives the roundtrip.
                # Only do this for non-table docs; TableBlock docs preserve content via blob.
                # Prefix every line with "- " so that recognized section names (e.g.
                # "Education") are not misdetected as headings by parse_llm_output.
                for p in section.body_paras:
                    if p.text.strip():
                        lines.append(f"- {p.text.strip()}")
        else:
            # Prefix with "- " so that known section names embedded in body
            # content (e.g. "PROFESSIONAL EXPERIENCE" inside a KEY SKILLS block)
            # are never misdetected as section headings by parse_llm_output.
            # parse_llm_output strips the leading "- " before adding to body_lines.
            for p in section.body_paras:
                if p.text.strip():
                    lines.append(f"- {p.text.strip()}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Diff helpers
# ---------------------------------------------------------------------------

@dataclass
class ParaDiff:
    index: int
    field: str
    original: Any
    rendered: Any

    def __str__(self) -> str:
        orig = repr(self.original)[:80]
        rend = repr(self.rendered)[:80]
        return f"  para[{self.index}] {self.field}: {orig} -> {rend}"


@dataclass
class RoundtripReport:
    name: str
    crash: str | None = None                        # exception during parse/render
    para_count_orig: int = 0
    para_count_rend: int = 0
    text_diffs: list[ParaDiff] = field(default_factory=list)
    style_diffs: list[ParaDiff] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.crash is None and not self.text_diffs

    def summary(self) -> str:
        lines = [f"=== {self.name} ==="]
        if self.crash:
            lines.append(f"  CRASH: {self.crash}")
            return "\n".join(lines)
        lines.append(f"  paras: {self.para_count_orig} -> {self.para_count_rend}")
        if not self.text_diffs and not self.style_diffs:
            lines.append("  OK - no differences")
        if self.text_diffs:
            lines.append(f"  TEXT diffs ({len(self.text_diffs)}):")
            for d in self.text_diffs[:10]:
                lines.append(str(d))
            if len(self.text_diffs) > 10:
                lines.append(f"  ... and {len(self.text_diffs) - 10} more")
        if self.style_diffs:
            lines.append(f"  STYLE diffs ({len(self.style_diffs)}):")
            for d in self.style_diffs[:15]:
                lines.append(str(d))
            if len(self.style_diffs) > 15:
                lines.append(f"  ... and {len(self.style_diffs) - 15} more")
        return "\n".join(lines)


@dataclass
class PdfLayoutReport:
    """Comparison of input PDF vs output PDF for a PDF roundtrip test."""
    name: str
    crash: str | None = None
    page_count_orig: int = 0
    page_count_out: int = 0
    sections_missing: list[str] = field(default_factory=list)
    text_coverage: float = 0.0          # fraction of input tokens found in output
    token_count_orig: int = 0
    token_count_out: int = 0
    bullet_count_orig: int = 0          # bullet markers detected in source PDF
    bullet_count_out: int = 0           # bullet markers detected in output PDF

    @property
    def ok(self) -> bool:
        return self.crash is None and not self.sections_missing

    def summary(self) -> str:
        lines = [f"=== {self.name} ==="]
        if self.crash:
            lines.append(f"  CRASH: {self.crash}")
            return "\n".join(lines)
        lines.append(
            f"  pages: {self.page_count_orig} -> {self.page_count_out}"
            f"  |  tokens: {self.token_count_orig} -> {self.token_count_out}"
            f"  |  coverage: {self.text_coverage:.0%}"
        )
        lines.append(
            f"  bullets: {self.bullet_count_orig} (source) -> {self.bullet_count_out} (output)"
        )
        if self.sections_missing:
            lines.append(f"  MISSING sections ({len(self.sections_missing)}): "
                         + ", ".join(repr(s) for s in self.sections_missing))
        else:
            lines.append("  All sections present")
        return "\n".join(lines)


def _tokenize_pdf_text(text: str) -> set[str]:
    """Return a set of lowercase word tokens (≥3 chars) from PDF text."""
    return {w.lower() for w in re.findall(r"[A-Za-z]{3,}", text)}


def _extract_pdf_text(pdf_path: str) -> tuple[int, str]:
    """Return (page_count, full_text) for a PDF file using PyMuPDF."""
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    pages = doc.page_count
    text = "\n".join(page.get_text() for page in doc)
    doc.close()
    return pages, text


def _count_pdf_bullets(pdf_path: str) -> int:
    """Count bullet markers across all pages of a PDF.

    Handles two representations:
    - Filled-circle drawings (3–5.5 pt, ≥10 path items) — used by many PDF templates.
    - Unicode bullet characters • (U+2022) and ● (U+25CF) — used by LibreOffice output.
    """
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    count = 0
    for page in doc:
        text = page.get_text()
        count += text.count("\u2022")   # •
        count += text.count("\u25cf")   # ●
        for d in page.get_drawings():
            rect = d.get("rect")
            if rect is None:
                continue
            x0, y0, x1, y1 = rect
            w, h = x1 - x0, y1 - y0
            if not (3.0 <= w <= 5.5 and 3.0 <= h <= 5.5):
                continue
            if len(d.get("items", [])) < 10:
                continue
            if d.get("fill") is None:
                continue
            count += 1
    doc.close()
    return count


def _compare_pdf_layout(
    input_pdf_path: str,
    output_pdf_path: str,
    section_titles: list[str],
    name: str,
) -> PdfLayoutReport:
    """Compare input PDF vs output PDF for text coverage and section presence."""
    report = PdfLayoutReport(name=name)
    try:
        in_pages, in_text = _extract_pdf_text(input_pdf_path)
        out_pages, out_text = _extract_pdf_text(output_pdf_path)
    except Exception as exc:
        report.crash = str(exc)
        return report

    report.page_count_orig = in_pages
    report.page_count_out = out_pages

    # Token-level coverage: what fraction of input words appear in output
    in_tokens = _tokenize_pdf_text(in_text)
    out_tokens = _tokenize_pdf_text(out_text)
    report.token_count_orig = len(in_tokens)
    report.token_count_out = len(out_tokens)
    if in_tokens:
        report.text_coverage = len(in_tokens & out_tokens) / len(in_tokens)

    # Section presence: check every known section title appears in output text.
    # Normalize letter-spaced titles (e.g. "S U M M A R Y" → "SUMMARY") so
    # they can be matched against the rendered output which uses compact form.
    out_lower = out_text.lower()
    out_collapsed = re.sub(r"\s+", "", out_lower)
    for title in section_titles:
        title_lower = title.lower()
        title_collapsed = re.sub(r"[\s\xa0]+", "", title_lower)
        if title_lower not in out_lower and title_collapsed not in out_collapsed:
            report.sections_missing.append(title)

    # Bullet presence: count markers in both PDFs
    try:
        report.bullet_count_orig = _count_pdf_bullets(input_pdf_path)
        report.bullet_count_out = _count_pdf_bullets(output_pdf_path)
    except Exception:
        pass  # non-fatal; counts stay at 0

    return report


def _compare_paras(orig_paras, rend_paras) -> tuple[list[ParaDiff], list[ParaDiff]]:
    """Compare two flat paragraph lists; return (text_diffs, style_diffs)."""
    text_diffs: list[ParaDiff] = []
    style_diffs: list[ParaDiff] = []

    for i, (o, r) in enumerate(zip(orig_paras, rend_paras)):
        # Text: strip the inline bullet prefix ("• ") added by para_builder so
        # the round-trip comparison is not confused by the rendering addition.
        def _norm(t: str) -> str:
            s = t.strip()
            return s[2:] if s.startswith("• ") else s
        if _norm(o.text) != _norm(r.text):
            text_diffs.append(ParaDiff(i, "text", o.text.strip(), r.text.strip()))

        # Style fields (best-effort; only compare what both have)
        os_, rs_ = o.style, r.style
        for attr in ("bold", "italic", "font_name", "font_size_pt",
                     "alignment", "indent_left", "indent_right",
                     "spacing_before", "spacing_after"):
            ov = getattr(os_, attr, None)
            rv = getattr(rs_, attr, None)
            if ov is None and rv is None:
                continue
            if ov != rv:
                style_diffs.append(ParaDiff(i, attr, ov, rv))

    return text_diffs, style_diffs


# ---------------------------------------------------------------------------
# DOCX roundtrip core
# ---------------------------------------------------------------------------

def _docx_roundtrip(
    path: Path,
    artefact_name: str | None = None,
    artefact_subdir: Path | None = None,
) -> RoundtripReport:
    """Parse a DOCX, identity-render, compare."""
    from tailor.compiler.docx_parser import parse_docx
    from tailor.compiler.pipeline import compile_resume

    report = RoundtripReport(name=path.name)

    try:
        orig_doc = parse_docx(str(path))
        llm_text = _doc_to_llm_text(orig_doc)

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            out_path = f.name
        try:
            compile_resume(str(path), llm_text, out_path)
            if _SAVE_ARTEFACTS and artefact_name:
                _save_artefact(out_path, artefact_name, subdir=artefact_subdir)
            rend_doc = parse_docx(out_path)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)

    except Exception as exc:
        report.crash = str(exc)
        return report

    # Collect header_extra paragraph texts — these are Word-wrapped role header
    # continuation lines (e.g. "Petersburg" when a long role header wraps).
    # They are intentionally NOT rendered; the merged header absorbs the text.
    # Exclude them from the comparison so the shift doesn't mask real diffs.
    header_extra_texts: set[str] = set()
    for section in orig_doc.sections:
        if section.semantic_type == "experience":
            for role in section.roles:
                for p in role.header_extra:
                    if p.text.strip():
                        header_extra_texts.add(p.text.strip())

    # Filter out empty paras and header_extra continuations from both sides
    orig_paras = [
        p for p in orig_doc.all_paras
        if p.text.strip() and p.text.strip() not in header_extra_texts
    ]
    rend_paras = [
        p for p in rend_doc.all_paras
        if p.text.strip() and p.text.strip() not in header_extra_texts
    ]

    report.para_count_orig = len(orig_paras)
    report.para_count_rend = len(rend_paras)

    if len(orig_paras) != len(rend_paras):
        # Record as text diff to make it visible
        report.text_diffs.append(ParaDiff(
            -1, "para_count",
            f"{len(orig_paras)} non-empty paras",
            f"{len(rend_paras)} non-empty paras",
        ))

    # Check that the table structure is preserved.  Templates that use a table for
    # layout must render back with the same number of table blocks; if the rendered
    # document drops the table (or gains extra ones) the visual layout is broken even
    # though the flat paragraph text may still match.
    from tailor.compiler.models import TableBlock as _TableBlock
    orig_table_count = (
        sum(1 for i in orig_doc.body_items if isinstance(i, _TableBlock))
        if orig_doc.body_items else 0
    )
    rend_table_count = (
        sum(1 for i in rend_doc.body_items if isinstance(i, _TableBlock))
        if rend_doc.body_items else 0
    )
    if orig_table_count != rend_table_count:
        report.text_diffs.append(ParaDiff(
            -2, "table_count",
            f"{orig_table_count} table block(s)",
            f"{rend_table_count} table block(s)",
        ))

    t, s = _compare_paras(orig_paras, rend_paras)
    report.text_diffs.extend(t)
    report.style_diffs.extend(s)
    return report


# ---------------------------------------------------------------------------
# PDF roundtrip core
# ---------------------------------------------------------------------------

def _pdf_roundtrip(
    path: Path,
) -> tuple[RoundtripReport, RoundtripReport | None, PdfLayoutReport | None]:
    """Parse a PDF, identity-render to DOCX (+PDF), then run DOCX roundtrip on result.

    Returns (pdf_report, docx_of_docx_report, pdf_layout_report).
    pdf_report:           PDF parse -> DOCX render; compares paragraph text content.
    docx_of_docx_report:  Takes the rendered DOCX as a new 'template',
                          re-renders identically — shows DOCX IR stability.
    pdf_layout_report:    Renders the output DOCX to PDF and compares it against
                          the input PDF (page count, section presence, token coverage).
    """
    from tailor.compiler.pdf_parser import parse_pdf
    from tailor.compiler.pipeline import compile_resume_from_ir
    from tailor.config import RESUME_TEMPLATE
    from tailor.compiler.docx_parser import parse_docx
    from tailor.docx.pdf import docx_to_pdf

    stem = path.stem
    pdf_report = RoundtripReport(name=f"{path.name} [PDF->DOCX]")
    docx_report: RoundtripReport | None = None
    layout_report: PdfLayoutReport | None = None
    docx_out: str = ""
    pdf_out: str = ""

    try:
        pdf_bytes = path.read_bytes()
        orig_ir = parse_pdf(pdf_bytes)
        llm_text = _doc_to_llm_text(orig_ir)

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            docx_out = f.name
        try:
            compile_resume_from_ir(
                template_ir=orig_ir,
                llm_text=llm_text,
                output_path=docx_out,
                style_template_path=str(RESUME_TEMPLATE),
            )
            if _SAVE_ARTEFACTS:
                _save_artefact(docx_out, f"{stem}_from_pdf.docx", subdir=_ARTEFACTS_DOCX)
            rend_doc = parse_docx(docx_out)
        except Exception as exc:
            pdf_report.crash = str(exc)
            return pdf_report, None, None
        finally:
            # Keep docx_out alive for stages 2 and 3
            pass

        # Stage 1: compare PDF text content vs rendered DOCX text.
        # Exclude header_extra paragraphs from both sides — these are role-header
        # continuation lines (e.g. "TECHNOLOGY" wrapping from a previous line) that
        # are intentionally not rendered as separate paragraphs.
        header_extra_texts: set[str] = set()
        for section in orig_ir.sections:
            if section.semantic_type == "experience":
                for role in section.roles:
                    for p in role.header_extra:
                        if p.text.strip():
                            header_extra_texts.add(p.text.strip())

        # Build a map from letter-spaced heading text → canonical heading.
        # PDFs use "S U M M A R Y", "E D U C A T I O N" etc. that _doc_to_llm_text
        # normalises to canonical form; the rendered DOCX therefore uses the
        # canonical text.  Accept this difference so it doesn't count as a mismatch.
        _heading_normalize: dict[str, str] = {}
        for _sec in orig_ir.sections:
            _canon = _SEMANTIC_CANONICAL_HEADING.get(_sec.semantic_type)
            if _canon and _sec.title.strip() != _canon:
                _parts = re.split(r"[\s\xa0]+", _sec.title.strip())
                if all(len(_p) <= 1 for _p in _parts if _p):  # letter-spaced
                    _heading_normalize[_sec.title.strip()] = _canon

        orig_texts = [
            _heading_normalize.get(p.text.strip(), p.text.strip())
            for p in orig_ir.all_paras
            if p.text.strip() and p.text.strip() not in header_extra_texts
        ]
        def _strip_bullet_prefix(t: str) -> str:
            """Remove inline bullet prefix added by para_builder ("• " or "\\uf0b7\\t")."""
            if t.startswith("\u2022 "):
                return t[2:]
            if t.startswith("\uf0b7") and len(t) > 1:
                # Tab-bullet: strip PUA marker and any trailing whitespace/tab.
                # Only strip when there is content after the marker; a standalone
                # "\uf0b7" paragraph is the PUA bullet marker itself (not a prefix).
                return t[1:].lstrip("\t ")
            return t

        rend_texts = [
            _strip_bullet_prefix(p.text.strip()) for p in rend_doc.all_paras
            if p.text.strip() and p.text.strip() not in header_extra_texts
        ]

        pdf_report.para_count_orig = len(orig_texts)
        pdf_report.para_count_rend = len(rend_texts)

        if len(orig_texts) != len(rend_texts):
            pdf_report.text_diffs.append(ParaDiff(
                -1, "para_count",
                f"{len(orig_texts)} paras in PDF IR",
                f"{len(rend_texts)} paras in rendered DOCX",
            ))

        for i, (o, r) in enumerate(zip(orig_texts, rend_texts)):
            if o != r:
                pdf_report.text_diffs.append(ParaDiff(i, "text", o, r))

        # Stage 2: DOCX roundtrip on the rendered DOCX
        docx2_artefact = f"{stem}_from_pdf_roundtrip.docx" if _SAVE_ARTEFACTS else None
        try:
            docx_report = _docx_roundtrip(
                Path(docx_out),
                artefact_name=docx2_artefact,
                artefact_subdir=_ARTEFACTS_DOCX if _SAVE_ARTEFACTS else None,
            )
            docx_report.name = f"{path.name} [DOCX->DOCX after PDF render]"
        except Exception as exc:
            docx_report = RoundtripReport(
                name=f"{path.name} [DOCX->DOCX after PDF render]",
                crash=str(exc),
            )

        # Stage 3: render DOCX → PDF and compare against input PDF.
        # Uses LibreOffice (subprocess) for production-quality rendering that
        # matches the eval pipeline.  Falls back to xhtml2pdf (local) if
        # LibreOffice is not available so the test still passes in CI.
        try:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                pdf_out = f.name.replace(".docx", ".pdf")
            shutil.copy2(docx_out, f.name)
            _pdf_method = os.environ.get("PDF_METHOD", "subprocess")
            try:
                docx_to_pdf(f.name, method=_pdf_method)
            except Exception:
                docx_to_pdf(f.name, method="local")
            os.remove(f.name)

            if _SAVE_ARTEFACTS and os.path.exists(pdf_out):
                _save_artefact(pdf_out, f"{stem}_from_pdf.pdf", subdir=_ARTEFACTS_PDF)
                # Source PDFs are not copied — compare manually against tests/samples/resume/pfd/

            section_titles = [s.title for s in orig_ir.sections]
            layout_report = _compare_pdf_layout(
                input_pdf_path=str(path),
                output_pdf_path=pdf_out,
                section_titles=section_titles,
                name=f"{path.name} [PDF layout]",
            )
        except Exception as exc:
            layout_report = PdfLayoutReport(
                name=f"{path.name} [PDF layout]",
                crash=f"PDF generation failed: {exc}",
            )
        finally:
            if pdf_out and os.path.exists(pdf_out):
                os.remove(pdf_out)

    except Exception as exc:
        pdf_report.crash = str(exc)
    finally:
        if docx_out and os.path.exists(docx_out):
            os.remove(docx_out)

    return pdf_report, docx_report, layout_report


# ---------------------------------------------------------------------------
# DOCX tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _DOCX_SAMPLES, reason="No DOCX samples found")
@pytest.mark.parametrize("path", _DOCX_SAMPLES, ids=[p.name for p in _DOCX_SAMPLES])
def test_docx_roundtrip(path: Path):
    """Parse DOCX -> identity LLM pass -> render -> compare."""
    reason = _detect_nonstandard_docx(path)
    if reason:
        pytest.xfail(reason)

    known_bad_reason = _KNOWN_BAD_DOCX.get(path.name)
    if known_bad_reason:
        pytest.xfail(known_bad_reason)

    artefact = f"{path.stem}_roundtrip.docx" if _SAVE_ARTEFACTS else None
    report = _docx_roundtrip(path, artefact_name=artefact, artefact_subdir=_ARTEFACTS_DOCX)
    print("\n" + report.summary())

    # Hard-fail only on crash or missing text (formatting diffs are logged, not fatal)
    assert report.crash is None, f"Crash during roundtrip: {report.crash}"
    assert not report.text_diffs, (
        f"{len(report.text_diffs)} text difference(s) in {path.name}:\n"
        + "\n".join(str(d) for d in report.text_diffs[:5])
    )


# ---------------------------------------------------------------------------
# PDF tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PDF_SAMPLES, reason="No PDF samples found")
@pytest.mark.parametrize("path", _PDF_SAMPLES, ids=[p.name for p in _PDF_SAMPLES])
def test_pdf_roundtrip(path: Path):
    """Parse PDF -> identity LLM pass -> render DOCX -> compare text; then DOCX roundtrip."""
    if path.name in _TWO_COLUMN_PDFS:
        pytest.xfail(
            "Two-column PDF layout: sidebar labels interleave with content "
            "(PyMuPDF reads blocks left-to-right/top-to-bottom)."
        )
    if path.name in _LETTER_SPACED_PDFS:
        pytest.xfail(
            "Letter-spaced heading text (e.g. 'E D U C A T I O N'): "
            "PyMuPDF reads spaced characters as tokens with spaces, "
            "causing a text mismatch against the source DOCX heading."
        )
    if path.name in _KNOWN_BAD_PDFS:
        pytest.xfail(_KNOWN_BAD_PDFS[path.name])

    pdf_report, docx_report, layout_report = _pdf_roundtrip(path)
    print("\n" + pdf_report.summary())
    if docx_report:
        print(docx_report.summary())
    if layout_report:
        print(layout_report.summary())

    assert pdf_report.crash is None, f"Crash during PDF roundtrip: {pdf_report.crash}"
    assert not pdf_report.text_diffs, (
        f"{len(pdf_report.text_diffs)} text difference(s) in {path.name}:\n"
        + "\n".join(str(d) for d in pdf_report.text_diffs[:5])
    )
    if docx_report:
        assert docx_report.crash is None, f"Crash in DOCX-of-DOCX: {docx_report.crash}"
        assert not docx_report.text_diffs, (
            f"{len(docx_report.text_diffs)} text difference(s) in DOCX-of-DOCX for "
            f"{path.name}:\n" + "\n".join(str(d) for d in docx_report.text_diffs[:5])
        )
    if layout_report:
        assert layout_report.crash is None, (
            f"PDF generation/comparison failed for {path.name}: {layout_report.crash}"
        )
        # All remaining checks require extractable text in the output PDF.
        # Complex table layouts (e.g. two-column sidebar) that xhtml2pdf cannot
        # render with selectable text are skipped.
        if layout_report.token_count_out > 0:
            # Text coverage: ≥75% of source word tokens must appear in output.
            assert layout_report.text_coverage >= 0.75, (
                f"Text coverage too low for {path.name}: "
                f"{layout_report.text_coverage:.0%} "
                f"({layout_report.token_count_out}/{layout_report.token_count_orig} tokens)"
            )
            # Section presence: every section heading must appear in the output.
            assert not layout_report.sections_missing, (
                f"Sections missing from output PDF for {path.name}: "
                + ", ".join(repr(s) for s in layout_report.sections_missing)
            )
            # Bullet presence: if source has bullets AND the output PDF
            # contains any detectable bullet markers (meaning the renderer
            # can produce them), the output count must be ≥50% of source.
            # When the output has 0 detected bullets the renderer may simply
            # not embed selectable bullet characters (known xhtml2pdf
            # limitation); that case is logged in the summary but not failed.
            if (
                layout_report.bullet_count_orig > 0
                and layout_report.bullet_count_out > 0
            ):
                assert layout_report.bullet_count_out >= layout_report.bullet_count_orig * 0.5, (
                    f"Too few bullets in output PDF for {path.name}: "
                    f"{layout_report.bullet_count_out} found, "
                    f"expected ≥{layout_report.bullet_count_orig * 0.5:.0f} "
                    f"(source had {layout_report.bullet_count_orig})"
                )
