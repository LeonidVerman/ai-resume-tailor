"""
backend/app/services/document_normalization_service.py

Input normalization — ensures all uploaded documents are in DOCX format
before parsing and generation.

Behavior
--------
- DOCX uploads: passed through unchanged.
- PDF uploads: converted to DOCX via LibreOffice (subprocess method).
  A user-visible disclaimer is attached when conversion is performed.
- Other formats: returned as-is with no conversion flag.

Failure behavior
----------------
If PDF → DOCX conversion fails, a RuntimeError is raised.
Generation must not continue with partial state.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

# Disclaimer shown to users when a PDF was converted to DOCX.
PDF_CONVERSION_WARNING = (
    "PDF files are converted to DOCX for processing. "
    "Minor formatting discrepancies may occur in the generated output."
)


@dataclasses.dataclass
class NormalizeResult:
    """Result of normalizing an uploaded document."""
    normalized_data: bytes
    source_type: str          # 'docx' | 'pdf' | 'other'
    conversion_performed: bool
    warning_message: str | None


def normalize_input_document(data: bytes, filename: str) -> NormalizeResult:
    """
    Normalize an uploaded document to DOCX format.

    Parameters
    ----------
    data:
        Raw file bytes of the uploaded document.
    filename:
        Original filename (used to detect file type by extension).

    Returns
    -------
    NormalizeResult
        - normalized_data: DOCX bytes (converted if input was PDF).
        - source_type: 'docx', 'pdf', or 'other'.
        - conversion_performed: True when PDF→DOCX conversion ran.
        - warning_message: Human-readable disclaimer, or None.

    Raises
    ------
    RuntimeError
        If PDF→DOCX conversion fails.
    """
    name_lower = filename.lower()

    if name_lower.endswith(".docx"):
        logger.debug("normalize_input_document: DOCX input, passing through (%d bytes)", len(data))
        return NormalizeResult(
            normalized_data=data,
            source_type="docx",
            conversion_performed=False,
            warning_message=None,
        )

    if name_lower.endswith(".pdf"):
        logger.info("normalize_input_document: PDF input, converting to DOCX via LibreOffice")
        docx_data = _convert_pdf_to_docx(data)
        logger.info("normalize_input_document: PDF→DOCX conversion succeeded (%d bytes)", len(docx_data))
        return NormalizeResult(
            normalized_data=docx_data,
            source_type="pdf",
            conversion_performed=True,
            warning_message=PDF_CONVERSION_WARNING,
        )

    # Other formats (TXT, etc.) — pass through as-is.
    logger.debug("normalize_input_document: unrecognized extension %r, passing through", filename)
    return NormalizeResult(
        normalized_data=data,
        source_type="other",
        conversion_performed=False,
        warning_message=None,
    )


def _convert_pdf_to_docx(pdf_data: bytes) -> bytes:
    """
    Convert PDF bytes to DOCX bytes via LibreOffice.

    Uses a subprocess call to ``libreoffice --headless --convert-to docx``.
    LibreOffice must be available in PATH (installed in the backend container).

    Raises
    ------
    RuntimeError
        If LibreOffice is not found, conversion fails, or output is missing.
    """
    from tailor.docx.pdf import pdf_to_docx

    # Write PDF to a temp file, run conversion, read back DOCX.
    tmp_pdf = None
    tmp_docx = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_data)
            tmp_pdf = f.name

        tmp_docx = pdf_to_docx(tmp_pdf, method="subprocess")

        with open(tmp_docx, "rb") as f:
            return f.read()

    except RuntimeError:
        raise  # re-raise LibreOffice errors verbatim

    except Exception as exc:
        raise RuntimeError(f"PDF→DOCX conversion error: {exc}") from exc

    finally:
        _safe_remove(tmp_pdf)
        _safe_remove(tmp_docx)


def _safe_remove(path: str | None) -> None:
    if path is None:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Could not remove temp file %s: %s", path, exc)
