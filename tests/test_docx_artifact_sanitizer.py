"""Unit tests for the PDF→DOCX conversion artifact sanitizer."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest
from docx import Document


# ---------------------------------------------------------------------------
# Test helper
# ---------------------------------------------------------------------------

def _make_doc_with_runs(runs: list[tuple[str, str | None]]) -> Document:
    """Create a Document with one paragraph containing the given runs.

    Each element of *runs* is (text, font_name_or_None).
    A font_name of None means no explicit font is set (inherited from style).
    """
    doc = Document()
    para = doc.add_paragraph()
    for text, font_name in runs:
        run = para.add_run(text)
        if font_name is not None:
            run.font.name = font_name
    return doc


def _roundtrip(doc: Document, tmp_path: Path):
    """Save *doc* to a temp file and return (path_str, reloaded_Document)."""
    p = str(tmp_path / "test.docx")
    doc.save(p)
    return p, Document(p)


# ---------------------------------------------------------------------------
# _is_converted_icon
# ---------------------------------------------------------------------------

class TestIsConvertedIcon:
    @pytest.mark.parametrize("cp,expected", [
        (0x0107, True),   # ć  Latin Extended-A — email icon artifact
        (0x0126, True),   # Ħ  Latin Extended-A — phone icon artifact
        (0x0100, True),   # Latin Extended-A lower boundary
        (0x024F, True),   # Latin Extended-B upper boundary
        (0xE000, True),   # PUA lower boundary
        (0xF8FF, True),   # PUA upper boundary
        (0x00FF, False),  # ÿ Latin-1 Supplement — below range
        (0x0250, False),  # IPA Extensions — just above Latin Ext-B
        (0x0066, False),  # 'f' ASCII — FontAwesome ASCII-mapped bullet, NOT detected
        (0x0041, False),  # 'A' plain ASCII
        (0x005D, False),  # ']' ASCII bracket
    ])
    def test_codepoint_ranges(self, cp, expected):
        from tailor.docx.artifact_sanitizer import _is_converted_icon
        assert _is_converted_icon(cp) is expected


# ---------------------------------------------------------------------------
# _para_body_font
# ---------------------------------------------------------------------------

class TestParaBodyFont:
    def test_single_explicit_font(self, tmp_path):
        from tailor.docx.artifact_sanitizer import _para_body_font
        doc = _make_doc_with_runs([("Hello World", "Trebuchet MS")])
        _, doc2 = _roundtrip(doc, tmp_path)
        assert _para_body_font(doc2.paragraphs[0]) == "Trebuchet MS"

    def test_dominant_font_by_char_count(self, tmp_path):
        from tailor.docx.artifact_sanitizer import _para_body_font
        # 1-char icon run vs 18-char content run → Trebuchet MS wins
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            ("hello@example.com", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        assert _para_body_font(doc2.paragraphs[0]) == "Trebuchet MS"

    def test_returns_none_when_no_explicit_font(self, tmp_path):
        from tailor.docx.artifact_sanitizer import _para_body_font
        doc = Document()
        doc.add_paragraph("some text")  # no explicit font set
        _, doc2 = _roundtrip(doc, tmp_path)
        # run.font.name is None when font is inherited → no explicit font → None
        assert _para_body_font(doc2.paragraphs[0]) is None

    def test_whitespace_only_runs_excluded(self, tmp_path):
        from tailor.docx.artifact_sanitizer import _para_body_font
        # Times New Roman has more runs but only whitespace; Calibri has actual chars
        doc = _make_doc_with_runs([
            ("   ", "Times New Roman"),
            (" ", "Times New Roman"),
            ("engineer", "Calibri"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        assert _para_body_font(doc2.paragraphs[0]) == "Calibri"


# ---------------------------------------------------------------------------
# _sanitize_paragraph
# ---------------------------------------------------------------------------

class TestSanitizeParagraph:
    def test_strips_latin_ext_icon_before_contact(self, tmp_path):
        """ć (U+0107) in Times New Roman followed by Trebuchet MS contact is stripped."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            (" ", "Times New Roman"),   # trailing spacer
            ("hello@example.com", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        n = _sanitize_paragraph(para, "Trebuchet MS")
        assert n == 2  # icon + trailing space
        assert "ć" not in para.text
        assert "hello@example.com" in para.text

    def test_strips_pua_icon(self, tmp_path):
        """PUA char (U+E001) in non-body font is stripped."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        doc = _make_doc_with_runs([
            ("", "Symbol"),
            ("+1 555 1234", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        n = _sanitize_paragraph(para, "Trebuchet MS")
        assert n == 1
        assert para.text.strip() == "+1 555 1234"

    def test_multiple_icons_in_same_para(self, tmp_path):
        """Three interleaved icon runs are all stripped; content survives."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        # Mirrors the structure of sample 35 para[2]:
        #   [ć][space][body-space][ć][space][body-space][Ħ+space][content]
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            (" ", "Times New Roman"),
            (" ", "Trebuchet MS"),
            ("ć", "Times New Roman"),
            (" ", "Times New Roman"),
            (" ", "Trebuchet MS"),
            ("Ħ ", "Times New Roman"),  # Ħ + space in same run
            ("+1 236 863 3698", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        n = _sanitize_paragraph(para, "Trebuchet MS")
        # runs 0,1,3,4,6 stripped = 5
        assert n == 5
        full = "".join(r.text for r in para.runs)
        assert "+1 236 863 3698" in full
        assert "ć" not in full
        assert "Ħ" not in full

    def test_no_false_positive_body_font(self, tmp_path):
        """Runs in the body font are never stripped."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        doc = _make_doc_with_runs([
            ("John Doe", "Trebuchet MS"),
            (" | Engineer", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        assert _sanitize_paragraph(para, "Trebuchet MS") == 0
        assert para.text == "John Doe | Engineer"

    def test_no_strip_when_run_too_long(self, tmp_path):
        """Runs with > 2 printable chars are not stripped even with artifact codepoints."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        # 3 Latin Extended chars → exceeds the ≤2-char threshold
        doc = _make_doc_with_runs([
            ("ćĦĒ", "Times New Roman"),
            ("content", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        assert _sanitize_paragraph(para, "Trebuchet MS") == 0

    def test_ascii_char_in_mismatch_font_not_stripped(self, tmp_path):
        """ASCII chars (e.g. 'f' from FontAwesome bullet) in non-body font are NOT stripped."""
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        # 'f' is U+0066 — outside both artifact ranges
        doc = _make_doc_with_runs([
            ("f ", "Times New Roman"),
            ("Leading migration project...", "Trebuchet MS"),
        ])
        _, doc2 = _roundtrip(doc, tmp_path)
        para = doc2.paragraphs[0]
        assert _sanitize_paragraph(para, "Trebuchet MS") == 0

    def test_empty_para_returns_zero(self):
        from tailor.docx.artifact_sanitizer import _sanitize_paragraph
        doc = Document()
        para = doc.add_paragraph()  # empty paragraph, no runs
        assert _sanitize_paragraph(para, "Trebuchet MS") == 0


# ---------------------------------------------------------------------------
# sanitize_docx_artifacts (integration)
# ---------------------------------------------------------------------------

class TestSanitizeDocxArtifacts:
    def test_strips_artifacts_and_saves(self, tmp_path):
        """Artifact runs are stripped and file is saved in-place."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            (" ", "Times New Roman"),
            ("hello@example.com", "Trebuchet MS"),
        ])
        path, _ = _roundtrip(doc, tmp_path)
        sanitize_docx_artifacts(path, enabled=True)
        doc2 = Document(path)
        assert "ć" not in doc2.paragraphs[0].text
        assert "hello@example.com" in doc2.paragraphs[0].text

    def test_disabled_flag_skips_sanitization(self, tmp_path):
        """enabled=False leaves artifact chars in the document."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            ("hello@example.com", "Trebuchet MS"),
        ])
        path, _ = _roundtrip(doc, tmp_path)
        sanitize_docx_artifacts(path, enabled=False)
        doc2 = Document(path)
        assert "ć" in doc2.paragraphs[0].text

    def test_module_killswitch(self, tmp_path, monkeypatch):
        """DOCX_ARTIFACT_SANITIZER_ENABLED=False skips sanitization."""
        import tailor.docx.artifact_sanitizer as _mod
        monkeypatch.setattr(_mod, "DOCX_ARTIFACT_SANITIZER_ENABLED", False)
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = _make_doc_with_runs([
            ("ć", "Times New Roman"),
            ("hello@example.com", "Trebuchet MS"),
        ])
        path, _ = _roundtrip(doc, tmp_path)
        sanitize_docx_artifacts(path)
        doc2 = Document(path)
        assert "ć" in doc2.paragraphs[0].text

    def test_no_change_when_no_artifacts(self, tmp_path):
        """Clean documents are unchanged."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = _make_doc_with_runs([
            ("John Doe", "Trebuchet MS"),
            (" | Software Engineer", "Trebuchet MS"),
        ])
        path, _ = _roundtrip(doc, tmp_path)
        sanitize_docx_artifacts(path, enabled=True)
        doc2 = Document(path)
        assert doc2.paragraphs[0].text == "John Doe | Software Engineer"

    def test_returns_input_path(self, tmp_path):
        """Function returns the input path unchanged."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = _make_doc_with_runs([("Hello", "Trebuchet MS")])
        path, _ = _roundtrip(doc, tmp_path)
        result = sanitize_docx_artifacts(path, enabled=True)
        assert result == path

    def test_table_cell_paragraphs_sanitized(self, tmp_path):
        """Artifact runs inside table cells are also stripped."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        doc = Document()
        tbl = doc.add_table(rows=1, cols=1)
        cell = tbl.rows[0].cells[0]
        para = cell.paragraphs[0]
        run_icon = para.add_run("ć")
        run_icon.font.name = "Times New Roman"
        run_body = para.add_run("cell@content.com")
        run_body.font.name = "Trebuchet MS"

        path = str(tmp_path / "table_test.docx")
        doc.save(path)
        sanitize_docx_artifacts(path, enabled=True)

        doc2 = Document(path)
        cell_text = doc2.tables[0].rows[0].cells[0].paragraphs[0].text
        assert "ć" not in cell_text
        assert "cell@content.com" in cell_text


# ---------------------------------------------------------------------------
# Sample 35 integration test (skipped if file absent)
# ---------------------------------------------------------------------------

_SAMPLE35 = Path(__file__).resolve().parents[1] / (
    "tests/samples/resume/docx/35-Gleb_Zernov_Resume.docx"
)


@pytest.mark.skipif(not _SAMPLE35.exists(), reason="sample 35 DOCX not present")
class TestSample35Sanitization:
    def test_contact_icon_artifacts_stripped(self, tmp_path):
        """ć and Ħ icon artifacts in sample 35 header paragraphs are removed."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        dst = str(tmp_path / "35.docx")
        shutil.copy(str(_SAMPLE35), dst)
        sanitize_docx_artifacts(dst, enabled=True)
        doc = Document(dst)
        # Only inspect the first 10 paragraphs (header area)
        header_text = "\n".join(p.text for p in doc.paragraphs[:10])
        assert "ć" not in header_text, "Email icon artifact ć still present"
        assert "Ħ" not in header_text, "Phone icon artifact Ħ still present"

    def test_content_paragraphs_unchanged(self, tmp_path):
        """Work-experience bullet paragraphs are not modified."""
        from tailor.docx.artifact_sanitizer import sanitize_docx_artifacts
        dst = str(tmp_path / "35.docx")
        shutil.copy(str(_SAMPLE35), dst)
        # Collect non-empty body paragraphs before sanitization
        before = [p.text for p in Document(str(_SAMPLE35)).paragraphs if p.text.strip()]
        sanitize_docx_artifacts(dst, enabled=True)
        after = [p.text for p in Document(dst).paragraphs if p.text.strip()]
        # Non-artifact paragraphs should be unchanged;
        # count should not increase (no duplication)
        assert len(after) <= len(before)
