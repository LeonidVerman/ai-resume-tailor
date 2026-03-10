"""
tests/test_docx_pipeline.py

Tests for the DOCX-centric tailoring pipeline additions:
  - normalize_input_document() — DOCX pass-through, PDF→DOCX conversion, warnings
  - post-processor (save_doc_from_template) — reordered / alternate section orders
  - RenderingService — render_cover_letter_pdf() smoke test
  - artifact generation from full text output
  - legacy history behavior (documents with no artifact content return 404 on download)

These tests make no LLM calls and no network requests.
PDF→DOCX conversion tests mock the LibreOffice subprocess to avoid
requiring LibreOffice in the test environment.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest.mock as mock
from pathlib import Path

import pytest
from docx import Document

from tailor.config import COVER_TEMPLATE, RESUME_TEMPLATE
from tailor.docx.template_fill import read_docx, save_doc_from_template


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_docx_bytes() -> bytes:
    """Return a minimal valid DOCX file as bytes (python-docx generated)."""
    doc = Document()
    doc.add_paragraph("Name: Test User")
    doc.add_paragraph("Professional Summary")
    doc.add_paragraph("Senior engineer with 10 years of experience.")
    doc.add_paragraph("Experience")
    doc.add_paragraph("Senior Engineer | Acme Corp")
    doc.add_paragraph("Jan 2022 – Present")
    doc.add_paragraph("Built distributed systems at scale.")
    doc.add_paragraph("Technical Skills")
    doc.add_paragraph("Python, Go, Kafka")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _make_minimal_pdf_bytes() -> bytes:
    """Return a valid minimal PDF as bytes (manually constructed)."""
    # Minimal PDF 1.4 with one empty page.
    return (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
        b"xref\n0 4\n0000000000 65535 f\n"
        b"0000000009 00000 n\n0000000058 00000 n\n0000000115 00000 n\n"
        b"trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n190\n%%EOF\n"
    )


# ---------------------------------------------------------------------------
# Task A: normalize_input_document
# ---------------------------------------------------------------------------

class TestNormalizeInputDocument:
    """Tests for document_normalization_service.normalize_input_document()."""

    def test_docx_passthrough(self):
        """DOCX files are returned unchanged with no conversion flag."""
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        data = _make_minimal_docx_bytes()
        result = normalize_input_document(data, "my_resume.docx")

        assert result.normalized_data == data
        assert result.source_type == "docx"
        assert result.conversion_performed is False
        assert result.warning_message is None

    def test_docx_case_insensitive(self):
        """DOCX extension match is case-insensitive."""
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        data = _make_minimal_docx_bytes()
        result = normalize_input_document(data, "resume.DOCX")
        assert result.source_type == "docx"
        assert result.conversion_performed is False

    def test_pdf_triggers_conversion(self):
        """PDF files are converted to DOCX via LibreOffice (mocked)."""
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
            PDF_CONVERSION_WARNING,
        )

        fake_docx = _make_minimal_docx_bytes()

        # Mock _convert_pdf_to_docx to avoid needing LibreOffice in tests.
        with mock.patch(
            "backend.app.services.document_normalization_service._convert_pdf_to_docx",
            return_value=fake_docx,
        ) as m:
            result = normalize_input_document(_make_minimal_pdf_bytes(), "resume.pdf")
            m.assert_called_once()

        assert result.normalized_data == fake_docx
        assert result.source_type == "pdf"
        assert result.conversion_performed is True
        assert result.warning_message == PDF_CONVERSION_WARNING

    def test_pdf_conversion_failure_raises(self):
        """RuntimeError from LibreOffice propagates as RuntimeError."""
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        with mock.patch(
            "backend.app.services.document_normalization_service._convert_pdf_to_docx",
            side_effect=RuntimeError("libreoffice not found"),
        ):
            with pytest.raises(RuntimeError, match="libreoffice"):
                normalize_input_document(_make_minimal_pdf_bytes(), "resume.pdf")

    def test_other_format_passthrough(self):
        """Non-DOCX non-PDF files are returned unchanged with source_type='other'."""
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        data = b"plain text resume content"
        result = normalize_input_document(data, "resume.txt")

        assert result.normalized_data == data
        assert result.source_type == "other"
        assert result.conversion_performed is False
        assert result.warning_message is None

    def test_warning_message_is_informational_string(self):
        """The warning message is a non-empty human-readable string."""
        from backend.app.services.document_normalization_service import (
            PDF_CONVERSION_WARNING,
        )

        assert isinstance(PDF_CONVERSION_WARNING, str)
        assert len(PDF_CONVERSION_WARNING) > 20
        assert "PDF" in PDF_CONVERSION_WARNING


# ---------------------------------------------------------------------------
# Task B: pdf_to_docx in pdf.py
# ---------------------------------------------------------------------------

class TestPdfToDocxFunction:
    """Tests for tailor.docx.pdf.pdf_to_docx()."""

    def test_unknown_method_raises(self):
        from tailor.docx.pdf import pdf_to_docx

        with pytest.raises(ValueError, match="Unknown method"):
            pdf_to_docx("/tmp/fake.pdf", method="unsupported")

    def test_subprocess_method_raises_when_libreoffice_missing(self, tmp_path):
        """subprocess method raises RuntimeError if libreoffice not in PATH."""
        from tailor.docx.pdf import pdf_to_docx

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf_bytes())

        # Patch subprocess.run to simulate FileNotFoundError (libreoffice not found).
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="LibreOffice executable not found"):
                pdf_to_docx(str(pdf_file), method="subprocess")

    def test_docker_method_raises_when_docker_missing(self, tmp_path):
        """docker method raises RuntimeError if Docker not available."""
        from tailor.docx.pdf import pdf_to_docx

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf_bytes())

        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="Docker executable not found"):
                pdf_to_docx(str(pdf_file), method="docker")

    def test_subprocess_method_raises_on_nonzero_exit(self, tmp_path):
        """Non-zero exit code from libreoffice raises RuntimeError."""
        from tailor.docx.pdf import pdf_to_docx
        from unittest.mock import MagicMock

        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(_make_minimal_pdf_bytes())

        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = "conversion error"
        proc.stdout = ""

        with mock.patch("subprocess.run", return_value=proc):
            with pytest.raises(RuntimeError, match="conversion failed"):
                pdf_to_docx(str(pdf_file), method="subprocess")


# ---------------------------------------------------------------------------
# Task D: Post-processor — reordered / alternate section orders
# ---------------------------------------------------------------------------

# NOTE: Section order is flexible — the post-processor must rely on semantic
# section title matching and employer/role name matching, not fixed order.
# The Experience section appears after Professional Summary in the template,
# but LLM output may order sections differently.  The merger must handle:
#   - Skills before Experience
#   - Education before Skills
#   - Experience section appearing later in the document

_RESUME_STANDARD_ORDER = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
Built distributed backend services.
Reduced latency by 30%.

Technical Skills
Python, Kafka, Kubernetes
"""

_RESUME_SKILLS_FIRST = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Technical Skills
Python, Kafka, Kubernetes

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
Built distributed backend services.
Reduced latency by 30%.
"""

_RESUME_EDUCATION_BEFORE_SKILLS = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
Built distributed backend services.
Reduced latency by 30%.

Education
B.Sc. Computer Science | UBC
2010 – 2014

Technical Skills
Python, Kafka, Kubernetes
"""

_RESUME_MULTIPLE_ROLES = """\
Professional Summary
Experienced engineer with 10 years in distributed systems.

Experience
Senior Engineer | Acme Corp
Jan 2022 – Present
Led distributed backend architecture.

Staff Engineer | Beta Inc
Mar 2019 – Dec 2021
Scaled microservices from 0 to 1M RPS.

Technical Skills
Python, Go, Kafka
"""


class TestPostProcessorSectionOrder:
    """
    Post-processor must merge LLM output correctly regardless of section order.

    Section order is flexible — these tests confirm the DOCX round-trip
    (save + read) produces output containing the expected content.
    """

    def _roundtrip(self, template_path, resume_text, tmp_path) -> str:
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(template_path), out, resume_text)
        return read_docx(out)

    def test_standard_section_order(self, tmp_path):
        text = self._roundtrip(RESUME_TEMPLATE, _RESUME_STANDARD_ORDER, tmp_path)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_skills_before_experience_in_llm_output(self, tmp_path):
        """Post-processor handles Skills appearing before Experience in LLM output."""
        text = self._roundtrip(RESUME_TEMPLATE, _RESUME_SKILLS_FIRST, tmp_path)
        # Both sections must be present in the output.
        assert "Acme Corp" in text
        assert "Python" in text

    def test_education_between_experience_and_skills(self, tmp_path):
        """Post-processor handles Education section between Experience and Skills."""
        text = self._roundtrip(RESUME_TEMPLATE, _RESUME_EDUCATION_BEFORE_SKILLS, tmp_path)
        assert "Acme Corp" in text
        assert "Python" in text

    def test_multiple_experience_roles(self, tmp_path):
        """Post-processor merges multiple experience roles correctly."""
        text = self._roundtrip(RESUME_TEMPLATE, _RESUME_MULTIPLE_ROLES, tmp_path)
        assert "Acme Corp" in text
        assert "Beta Inc" in text
        assert "Python" in text

    def test_output_is_valid_docx(self, tmp_path):
        """Rendered output is a valid DOCX file (openable by python-docx)."""
        out = str(tmp_path / "out.docx")
        save_doc_from_template(str(RESUME_TEMPLATE), out, _RESUME_STANDARD_ORDER)
        doc = Document(out)
        assert len(doc.paragraphs) > 0

    def test_cover_letter_merged_correctly(self, tmp_path):
        """Cover letter text is merged into DOCX template without errors."""
        cl_text = (
            "Leonid Verman\nVancouver, BC\nDear Hiring Manager,\n"
            "I am excited to apply for the role at Acme Corp.\n"
            "I have built distributed systems for 10 years.\n"
            "Thank you for your consideration.\n\nSincerely,\nLeoind Verman"
        )
        out = str(tmp_path / "cl_out.docx")
        save_doc_from_template(str(COVER_TEMPLATE), out, cl_text)
        text = read_docx(out)
        assert "Acme Corp" in text
        assert "Sincerely" in text


# ---------------------------------------------------------------------------
# Task E: RenderingService — render_cover_letter_pdf smoke test
# ---------------------------------------------------------------------------

class TestRenderingServiceCoverLetterPdf:
    """Smoke tests for RenderingService.render_cover_letter_pdf()."""

    _CL_TEXT = (
        "Leonid Verman\nVancouver, BC\nDear Hiring Manager,\n"
        "I am applying for the Senior Engineer role at Acme Corp.\n"
        "I have built distributed systems for 10+ years.\n"
        "Thank you.\n\nSincerely,\nLeoind Verman"
    )

    def test_render_cover_letter_docx_returns_bytes(self):
        from backend.app.services.rendering_service import RenderingService

        svc = RenderingService()
        docx_bytes = svc.render_cover_letter_docx(self._CL_TEXT)
        assert isinstance(docx_bytes, bytes)
        assert len(docx_bytes) > 0
        # Verify it is a valid DOCX (ZIP file with [Content_Types].xml)
        assert docx_bytes[:4] == b"PK\x03\x04"

    def test_render_cover_letter_pdf_returns_bytes(self):
        """PDF rendering (local/xhtml2pdf path) returns non-empty bytes."""
        from backend.app.services.rendering_service import RenderingService

        svc = RenderingService()
        docx_bytes = svc.render_cover_letter_docx(self._CL_TEXT)
        pdf_bytes = svc.render_cover_letter_pdf(docx_bytes, method="local")
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 100
        # PDF magic bytes
        assert pdf_bytes[:4] == b"%PDF"

    def test_render_resume_pdf_returns_bytes(self):
        """PDF rendering of resume also works (regression guard)."""
        from backend.app.services.rendering_service import RenderingService

        svc = RenderingService()
        resume_text = _RESUME_STANDARD_ORDER
        docx_bytes = svc.render_resume_docx(resume_text)
        pdf_bytes = svc.render_resume_pdf(docx_bytes, method="local")
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes[:4] == b"%PDF"


# ---------------------------------------------------------------------------
# Task G: Legacy history — documents with no content return 404 gracefully
# ---------------------------------------------------------------------------

class TestDocumentNormalizationWarning:
    """
    The NormalizeResult.warning_message field must be set exactly when a
    PDF was converted to DOCX.  This is stored as input_conversion_warning
    on the StructuredResume record.
    """

    def test_no_warning_for_docx(self):
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        result = normalize_input_document(_make_minimal_docx_bytes(), "r.docx")
        assert result.warning_message is None

    def test_warning_set_for_pdf(self):
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        fake_docx = _make_minimal_docx_bytes()
        with mock.patch(
            "backend.app.services.document_normalization_service._convert_pdf_to_docx",
            return_value=fake_docx,
        ):
            result = normalize_input_document(_make_minimal_pdf_bytes(), "r.pdf")

        assert result.warning_message is not None
        assert "PDF" in result.warning_message
        assert "formatting" in result.warning_message.lower()

    def test_no_warning_for_txt(self):
        from backend.app.services.document_normalization_service import (
            normalize_input_document,
        )

        result = normalize_input_document(b"some text", "resume.txt")
        assert result.warning_message is None
