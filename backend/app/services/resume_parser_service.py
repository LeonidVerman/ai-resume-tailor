"""
backend/app/services/resume_parser_service.py

Resume parser service — converts uploaded DOCX/PDF to a
StructuredResumeDocument stored in the DB.

Parsing strategy
----------------
1. Extract raw text from the uploaded file (DOCX via python-docx,
   PDF via pypdf).
2. Run lightweight heuristic parsing to populate StructuredResumeDocument
   fields where possible.
3. Preserve the raw_text field for downstream use (e.g. feeding the
   generator's prompt directly, which currently uses the template DOCX
   text anyway).

The heuristic parser is intentionally minimal — a full NLP-based parser
is a future enhancement.  The important thing is that the DB record
exists and the raw text is available.
"""

from __future__ import annotations

import io
import logging
import re

from backend.app.schemas.structured_resume import (
    ContactInfo,
    ExperienceEntry,
    SkillsSection,
    StructuredResumeDocument,
)

logger = logging.getLogger(__name__)


# ── Text extraction ────────────────────────────────────────────────────────

def extract_text_from_docx(data: bytes) -> str:
    """Extract plain text from a DOCX file given as bytes.

    Handles both standard DOCX files and LibreOffice-converted files.

    LibreOffice PDF→DOCX conversion wraps every text box inside an
    ``<mc:AlternateContent>`` element that contains both a ``<mc:Choice>``
    (real content) and a ``<mc:Fallback>`` (duplicate for old viewers).
    Standard ``doc.paragraphs`` only sees body-level paragraphs and misses
    text boxes entirely, so we collect paragraphs from the full XML tree
    while skipping anything inside a ``<mc:Fallback>`` to avoid duplicates.
    """
    from docx import Document
    from docx.oxml.ns import qn

    doc = Document(io.BytesIO(data))

    W  = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
    _FALLBACK = "{%s}Fallback" % MC
    _T        = "{%s}t" % W
    _P        = "{%s}p" % W

    body = doc.element.body

    def _in_fallback(elem) -> bool:
        """Return True if elem is a descendant of an mc:Fallback element.

        lxml proxy objects are recreated on each iter() call so id()-based
        sets are unreliable.  Walking the parent chain is O(depth) but
        always correct.
        """
        parent = elem.getparent()
        while parent is not None:
            if parent.tag == _FALLBACK:
                return True
            parent = parent.getparent()
        return False

    # Walk all paragraphs in document order, skipping Fallback descendants.
    lines: list[str] = []
    for para in body.iter(_P):
        if _in_fallback(para):
            continue
        text = "".join(
            elem.text
            for elem in para.iter(_T)
            if elem.text and not _in_fallback(elem)
        )
        if text.strip():
            lines.append(text)

    return "\n".join(lines)


def extract_text_from_pdf(data: bytes) -> str:
    """Extract plain text from a PDF file given as bytes."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def extract_text(data: bytes, filename: str) -> str:
    """Dispatch to the correct extractor based on file extension."""
    name_lower = filename.lower()
    if name_lower.endswith(".docx"):
        return extract_text_from_docx(data)
    if name_lower.endswith(".pdf"):
        return extract_text_from_pdf(data)
    # Try DOCX first, fall back to treating as text
    try:
        return extract_text_from_docx(data)
    except Exception:
        return data.decode("utf-8", errors="replace")


# ── Heuristic structure extraction ────────────────────────────────────────

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"\+?[\d\s\-().]{7,20}")
_LINKEDIN_RE = re.compile(r"linkedin\.com/in/[\w-]+", re.IGNORECASE)
_GITHUB_RE = re.compile(r"github\.com/[\w-]+", re.IGNORECASE)


def _extract_contacts(text: str) -> ContactInfo:
    email = next(iter(_EMAIL_RE.findall(text)), None)
    phone_matches = _PHONE_RE.findall(text[:500])  # only search header area
    phone = phone_matches[0].strip() if phone_matches else None
    linkedin = next(iter(_LINKEDIN_RE.findall(text)), None)
    linkedin_url = f"https://{linkedin}" if linkedin else None
    github = next(iter(_GITHUB_RE.findall(text)), None)
    github_url = f"https://{github}" if github else None
    return ContactInfo(
        email=email,
        phone=phone,
        linkedin_url=linkedin_url,
        github_url=github_url,
    )


def _extract_name(lines: list[str]) -> str:
    """Best-effort: assume the first non-empty line is the name."""
    for line in lines[:5]:
        stripped = line.strip()
        if stripped and not _EMAIL_RE.search(stripped):
            return stripped
    return "Unknown"


def _heuristic_parse(raw_text: str) -> StructuredResumeDocument:
    """Very lightweight structure extraction from raw resume text."""
    lines = raw_text.splitlines()
    non_empty = [l for l in lines if l.strip()]

    name = _extract_name(non_empty)
    contacts = _extract_contacts(raw_text)

    # Summary: look for a "summary" or "profile" section header
    summary = ""
    for i, line in enumerate(non_empty):
        if re.search(r"\b(summary|profile|objective)\b", line, re.IGNORECASE):
            # Collect next 1–3 non-empty lines as summary
            snippet_lines = [l.strip() for l in non_empty[i + 1 : i + 5] if l.strip()]
            summary = " ".join(snippet_lines[:3])
            break

    return StructuredResumeDocument(
        name=name,
        contacts=contacts,
        summary=summary,
        experience=[],          # heuristic experience extraction is future work
        technical_skills=SkillsSection(),
        raw_text=raw_text,
    )


# ── Service class ──────────────────────────────────────────────────────────

class ResumeParserService:
    """
    Parse an uploaded resume file into a StructuredResumeDocument.

    Responsibilities
    ----------------
    - extract text from DOCX or PDF
    - run heuristic structure extraction
    - return a StructuredResumeDocument suitable for DB storage

    The DB write itself is handled by the caller (API layer or generation
    service) so this service remains pure and testable.
    """

    def parse(
        self,
        data: bytes,
        filename: str = "resume.docx",
    ) -> StructuredResumeDocument:
        """
        Parse resume bytes into a StructuredResumeDocument.

        Parameters
        ----------
        data:
            Raw file bytes (DOCX or PDF).
        filename:
            Original filename; used to detect file type.
        """
        logger.debug("Parsing resume file=%s size=%d bytes", filename, len(data))
        raw_text = extract_text(data, filename)
        if not raw_text.strip():
            logger.warning("Resume file %s yielded empty text", filename)
            return StructuredResumeDocument(name="Unknown", raw_text="")

        doc = _heuristic_parse(raw_text)
        logger.debug("Parsed resume name=%r summary_len=%d", doc.name, len(doc.summary))
        return doc

    def parse_raw_text(self, text: str) -> StructuredResumeDocument:
        """Parse from already-extracted text (e.g. pasted in UI)."""
        return _heuristic_parse(text)
