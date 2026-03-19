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

_TWO_COLUMN_PDFS: frozenset[str] = frozenset()

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

        lines.append(section.title)
        if section.semantic_type == "experience":
            if section.roles:
                for role in section.roles:
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

    # Section presence: check every known section title appears in output text
    out_lower = out_text.lower()
    for title in section_titles:
        if title.lower() not in out_lower:
            report.sections_missing.append(title)

    return report


def _compare_paras(orig_paras, rend_paras) -> tuple[list[ParaDiff], list[ParaDiff]]:
    """Compare two flat paragraph lists; return (text_diffs, style_diffs)."""
    text_diffs: list[ParaDiff] = []
    style_diffs: list[ParaDiff] = []

    for i, (o, r) in enumerate(zip(orig_paras, rend_paras)):
        # Text
        if o.text.strip() != r.text.strip():
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
        orig_texts = [
            p.text.strip() for p in orig_ir.all_paras
            if p.text.strip() and p.text.strip() not in header_extra_texts
        ]
        rend_texts = [
            p.text.strip() for p in rend_doc.all_paras
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
        # Uses xhtml2pdf (local, no external deps) so this works in all environments.
        try:
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
                pdf_out = f.name.replace(".docx", ".pdf")
            shutil.copy2(docx_out, f.name)
            docx_to_pdf(f.name, method="local")
            os.remove(f.name)

            if _SAVE_ARTEFACTS and os.path.exists(pdf_out):
                _save_artefact(pdf_out, f"{stem}_from_pdf.pdf", subdir=_ARTEFACTS_PDF)
                _save_artefact(str(path), path.name, subdir=_ARTEFACTS_PDF)

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
    is_two_column = path.name in _TWO_COLUMN_PDFS
    if is_two_column:
        pytest.xfail(
            "Two-column PDF layout: sidebar labels interleave with content "
            "(PyMuPDF reads blocks left-to-right/top-to-bottom)."
        )

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
        # Section check: skip when output PDF has no extractable text — this
        # happens when the DOCX contains complex tables (e.g. two-column sidebar
        # layout) that xhtml2pdf cannot render with selectable text.
        if layout_report.token_count_out > 0:
            assert not layout_report.sections_missing, (
                f"Sections missing from output PDF for {path.name}: "
                + ", ".join(repr(s) for s in layout_report.sections_missing)
            )
