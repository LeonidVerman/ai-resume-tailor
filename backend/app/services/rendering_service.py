"""
backend/app/services/rendering_service.py

Rendering service — converts LLM text output into DOCX and PDF files.

Wraps the existing generator's:
  - tailor.docx.template_fill.save_doc_from_template()
  - tailor.docx.template_fill.normalize_cover_letter()
  - tailor.docx.pdf.docx_to_pdf()

The generator code is NOT modified; this service is a thin adapter that
manages temp files and returns raw bytes suitable for storage upload.
"""

from __future__ import annotations

import logging
import os
import tempfile

logger = logging.getLogger(__name__)


class RenderingService:
    """
    Render LLM-generated text into DOCX and optionally PDF bytes.

    The generator's save_doc_from_template() requires file paths, so this
    service creates temporary files, calls the generator functions, then
    reads the results back as bytes.
    """

    def render_resume_docx(self, resume_text: str) -> bytes:
        """
        Fill the resume DOCX template with LLM-generated text.

        Returns raw DOCX bytes.
        """
        from tailor.config import RESUME_TEMPLATE
        from tailor.docx.template_fill import save_doc_from_template

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_doc_from_template(str(RESUME_TEMPLATE), tmp_path, resume_text)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            _safe_remove(tmp_path)

    def render_cover_letter_docx(self, cover_letter_text: str) -> bytes:
        """
        Fill the cover letter DOCX template with LLM-generated text.

        Normalizes blank lines before template fill (matches CLI behaviour).
        Returns raw DOCX bytes.
        """
        from tailor.config import COVER_TEMPLATE
        from tailor.docx.template_fill import normalize_cover_letter, save_doc_from_template

        normalized = normalize_cover_letter(cover_letter_text)

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            save_doc_from_template(str(COVER_TEMPLATE), tmp_path, normalized)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            _safe_remove(tmp_path)

    def render_resume_pdf(self, resume_docx_bytes: bytes, method: str = "local") -> bytes:
        """
        Convert a resume DOCX (bytes) to PDF bytes.

        Parameters
        ----------
        resume_docx_bytes:
            DOCX content as bytes (typically from render_resume_docx).
        method:
            'docker' — LibreOffice via Docker (high fidelity, requires Docker).
            'local'  — xhtml2pdf fallback (lower fidelity, no external deps).
        """
        from tailor.docx.pdf import docx_to_pdf

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_docx:
            tmp_docx.write(resume_docx_bytes)
            docx_path = tmp_docx.name

        pdf_path = os.path.splitext(docx_path)[0] + ".pdf"
        try:
            docx_to_pdf(docx_path, method=method)
            with open(pdf_path, "rb") as f:
                return f.read()
        finally:
            _safe_remove(docx_path)
            _safe_remove(pdf_path)


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Could not remove temp file %s: %s", path, exc)
