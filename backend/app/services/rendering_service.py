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

    def render_resume_docx(
        self,
        resume_text: str,
        template_bytes: bytes | None = None,
        template_ir_dict: dict | None = None,
        classification_dict: dict | None = None,
    ) -> bytes:
        """
        Fill the resume DOCX template with LLM-generated text.

        Parameters
        ----------
        resume_text:
            LLM-generated resume text.
        template_bytes:
            The user's original resume DOCX as raw bytes.  Used for DOCX
            uploads.  Mutually exclusive with template_ir_dict.
        template_ir_dict:
            Serialized ResumeDocument IR dict for PDF-sourced resumes.
            When provided, the compiler pipeline uses the IR directly and
            renders via para_builder.
        classification_dict:
            Optional serialized ClassificationOutput dict (the "classification"
            key from the stored classification envelope).  When provided, the
            updater constrains section updates using rewrite_policy,
            preserve_heading, and preserve_body_structure.  None → no change.

        Returns raw DOCX bytes.
        """
        from tailor.config import RESUME_TEMPLATE

        classification = _load_classification(classification_dict)

        if template_ir_dict is not None:
            return self._render_from_ir(resume_text, template_ir_dict, classification)

        from tailor.docx.template_fill import save_doc_from_template

        tmp_template_path = None
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            if template_bytes is not None:
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_tpl:
                    tmp_tpl.write(template_bytes)
                    tmp_template_path = tmp_tpl.name
                template_path = tmp_template_path
            else:
                template_path = str(RESUME_TEMPLATE)

            save_doc_from_template(template_path, tmp_path, resume_text, classification=classification)
            with open(tmp_path, "rb") as f:
                return f.read()
        finally:
            _safe_remove(tmp_path)
            if tmp_template_path:
                _safe_remove(tmp_template_path)

    def _render_from_ir(self, resume_text: str, template_ir_dict: dict, classification=None) -> bytes:
        """Render a PDF-sourced resume by applying LLM text to the stored IR."""
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.pipeline import compile_resume_from_ir
        from tailor.config import RESUME_TEMPLATE

        template_ir = ResumeDocument.from_dict(template_ir_dict)

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            output_path = tmp.name
        try:
            compile_resume_from_ir(
                template_ir=template_ir,
                llm_text=resume_text,
                output_path=output_path,
                style_template_path=str(RESUME_TEMPLATE),
                classification=classification,
            )
            with open(output_path, "rb") as f:
                return f.read()
        finally:
            _safe_remove(output_path)

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

    def render_resume_pdf(self, resume_docx_bytes: bytes, method: str = "subprocess") -> bytes:
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
        return self._docx_bytes_to_pdf(resume_docx_bytes, method=method)

    def render_cover_letter_pdf(self, cover_letter_docx_bytes: bytes, method: str = "local") -> bytes:
        """
        Convert a cover letter DOCX (bytes) to PDF bytes.

        Parameters
        ----------
        cover_letter_docx_bytes:
            DOCX content as bytes (typically from render_cover_letter_docx).
        method:
            'docker' — LibreOffice via Docker (high fidelity, requires Docker).
            'local'  — xhtml2pdf fallback (lower fidelity, no external deps).
        """
        return self._docx_bytes_to_pdf(cover_letter_docx_bytes, method=method)

    def _docx_bytes_to_pdf(self, docx_bytes: bytes, method: str = "local") -> bytes:
        """Convert DOCX bytes to PDF bytes via the specified method."""
        from tailor.docx.pdf import docx_to_pdf

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_docx:
            tmp_docx.write(docx_bytes)
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


def _load_classification(classification_dict: dict | None):
    """Parse a classification dict into a ClassificationOutput, or return None.

    Failures are caught and logged so a bad/missing classification never
    blocks rendering.
    """
    if not classification_dict:
        return None
    try:
        from tailor.compiler.classification_models import ClassificationOutput
        return ClassificationOutput.from_dict(classification_dict)
    except Exception as exc:
        logger.warning("Failed to parse classification for rendering: %s", exc)
        return None
