"""
backend/app/services/document_normalization_service.py

Input normalization — prepares uploaded documents for the resume compiler.

Behavior
--------
- DOCX uploads: passed through unchanged; template_ir is None.
- PDF uploads: parsed with PyMuPDF into a ResumeDocument IR, which is
  serialized to a dict and returned in template_ir.  normalized_data
  contains the original PDF bytes (not converted); the compiler uses the
  IR directly.  A user-visible notice is attached.
- Other formats: returned as-is with no conversion flag.

Failure behavior
----------------
If PDF parsing fails (scanned PDF, corrupt file, missing PyMuPDF), a
RuntimeError is raised.  Generation must not continue with partial state.
"""

from __future__ import annotations

import dataclasses
import logging

logger = logging.getLogger(__name__)

# Notice shown to users when a PDF was uploaded.
PDF_CONVERSION_WARNING = (
    "PDF files are parsed directly for processing. "
    "Minor formatting differences may appear in the generated output."
)


@dataclasses.dataclass
class NormalizeResult:
    """Result of normalizing an uploaded document."""
    normalized_data: bytes
    source_type: str          # 'docx' | 'pdf' | 'other'
    conversion_performed: bool
    warning_message: str | None
    # Serialized ResumeDocument IR for PDF uploads; None for DOCX.
    template_ir: dict | None = None


def normalize_input_document(data: bytes, filename: str) -> NormalizeResult:
    """
    Normalize an uploaded document.

    For DOCX: passes bytes through unchanged.
    For PDF: parses into ResumeDocument IR via PyMuPDF; returns serialized IR.
    For others: passes bytes through.

    Parameters
    ----------
    data:
        Raw file bytes of the uploaded document.
    filename:
        Original filename (used to detect file type by extension).

    Returns
    -------
    NormalizeResult

    Raises
    ------
    RuntimeError
        If the PDF cannot be parsed (scanned, corrupt, or PyMuPDF missing).
    """
    name_lower = filename.lower()

    if name_lower.endswith(".docx"):
        logger.debug("normalize_input_document: DOCX input, passing through (%d bytes)", len(data))
        return NormalizeResult(
            normalized_data=data,
            source_type="docx",
            conversion_performed=False,
            warning_message=None,
            template_ir=None,
        )

    if name_lower.endswith(".pdf"):
        logger.info("normalize_input_document: PDF input, parsing via PyMuPDF")
        template_ir = _parse_pdf_to_ir(data)
        logger.info("normalize_input_document: PDF parsed successfully")
        return NormalizeResult(
            normalized_data=data,
            source_type="pdf",
            conversion_performed=False,
            warning_message=PDF_CONVERSION_WARNING,
            template_ir=template_ir,
        )

    # Other formats (TXT, etc.) — pass through as-is.
    logger.debug("normalize_input_document: unrecognized extension %r, passing through", filename)
    return NormalizeResult(
        normalized_data=data,
        source_type="other",
        conversion_performed=False,
        warning_message=None,
        template_ir=None,
    )


def _parse_pdf_to_ir(pdf_data: bytes) -> dict:
    """Parse PDF bytes into a serialized ResumeDocument IR dict.

    Raises
    ------
    RuntimeError
        If the PDF is scanned, corrupt, or PyMuPDF is not installed.
    """
    from tailor.compiler.pdf_parser import parse_pdf

    doc = parse_pdf(pdf_data)
    return doc.to_dict()

