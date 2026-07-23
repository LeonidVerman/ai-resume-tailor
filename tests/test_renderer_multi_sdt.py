"""Tests for multi-SDT paragraph handling in _set_para_text (Phase 3 fixes).

Previously _set_para_text unconditionally skipped paragraphs with two or more
direct w:sdt children, so updater-injected text (e.g. sample 7 skills lines in
tab-column SDT rows) was silently discarded while the renderer still logged
PARAGRAPH_BLOCK_XML_PATCHED.  Now:

1. Unchanged text → structure preserved (returns False), so multi-column
   contact rows keep their tab-stop layout.
2. Changed text → SDT/content children stripped and the new text written into
   a fresh run (returns True), mirroring the _ext_-slot handling.
3. PARAGRAPH_BLOCK_XML_PATCHED is logged only on actual writes; preserved
   rows log PARAGRAPH_BLOCK_PRESERVED_MULTI_SDT instead.
"""
from __future__ import annotations

import logging

from lxml import etree

from tailor.compiler.docx_renderer import _set_para_text

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _sdt(text: str) -> str:
    return (
        "<w:sdt><w:sdtPr/><w:sdtContent>"
        f"<w:r><w:t>{text}</w:t></w:r>"
        "</w:sdtContent></w:sdt>"
    )


def _multi_sdt_xml() -> str:
    """Three-column SDT contact row: email TAB phone TAB LinkedIn."""
    return (
        f'<w:p xmlns:w="{_W}"><w:pPr/>'
        + _sdt("hello@example.com")
        + "<w:r><w:tab/></w:r>"
        + _sdt("+123-456-7890")
        + "<w:r><w:tab/></w:r>"
        + _sdt("linkedin.com/in/xyz")
        + "</w:p>"
    )


def _para_text(elem) -> str:
    return "".join(t.text or "" for t in elem.iter(f"{{{_W}}}t"))


def _sdt_count(elem) -> int:
    return sum(1 for child in elem if child.tag == f"{{{_W}}}sdt")


class TestMultiSdtGuard:
    def test_unchanged_text_preserves_structure(self):
        elem = etree.fromstring(_multi_sdt_xml())
        written = _set_para_text(
            elem, "hello@example.com +123-456-7890 linkedin.com/in/xyz"
        )
        assert written is False
        assert _sdt_count(elem) == 3
        assert "hello@example.com" in _para_text(elem)
        assert "+123-456-7890" in _para_text(elem)

    def test_changed_text_written_not_discarded(self):
        # Sample 7 skills: LLM text assigned to a tab-column SDT row must render.
        elem = etree.fromstring(_multi_sdt_xml())
        written = _set_para_text(elem, "Rust, Go, Python, Kubernetes")
        assert written is True
        assert _sdt_count(elem) == 0
        assert _para_text(elem) == "Rust, Go, Python, Kubernetes"

    def test_blanked_text_clears_content(self):
        elem = etree.fromstring(_multi_sdt_xml())
        written = _set_para_text(elem, "")
        assert written is True
        assert _para_text(elem) == ""

    def test_single_sdt_paragraph_still_rewritten(self):
        xml = f'<w:p xmlns:w="{_W}"><w:pPr/>' + _sdt("Old text") + "</w:p>"
        elem = etree.fromstring(xml)
        written = _set_para_text(elem, "New text")
        assert written is True
        assert _para_text(elem) == "New text"

    def test_plain_paragraph_returns_true(self):
        xml = f'<w:p xmlns:w="{_W}"><w:r><w:t>Old</w:t></w:r></w:p>'
        elem = etree.fromstring(xml)
        assert _set_para_text(elem, "New") is True
        assert _para_text(elem) == "New"


class TestPatchedLogTruthfulness:
    def _doc(self, para_id: str, text: str, xml: str):
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutProfile,
            ParaModel,
            ParaStyle,
            ResumeDocument,
        )
        pm = ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
        pm.para_id = para_id
        doc = ResumeDocument(
            header_paras=[pm],
            sections=[],
            layout=LayoutProfile(
                page_width_pt=612, page_height_pt=792,
                margin_top_pt=72, margin_bottom_pt=72,
                margin_left_pt=72, margin_right_pt=72,
                default_font_name="Calibri", default_font_size_pt=11,
            ),
            all_paras=[pm],
        )
        doc.layout_blocks = [
            LayoutParagraphBlock(para_id=para_id, xml_proto_xml=xml)
        ]
        return doc

    def test_preserved_multi_sdt_not_logged_as_patched(
        self, tmp_path, monkeypatch, caplog
    ):
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)
        from pathlib import Path

        from tailor.compiler.docx_renderer import render_docx

        template = str(
            Path(__file__).parent / "samples" / "resume" / "docx"
            / "1-Leonid_Verman_Resume_Template.docx"
        )
        doc = self._doc(
            "para_1",
            "hello@example.com +123-456-7890 linkedin.com/in/xyz",
            _multi_sdt_xml(),
        )
        out = str(tmp_path / "out.docx")
        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            render_docx(doc, template, out)
        patched = [m for m in caplog.messages
                   if "PARAGRAPH_BLOCK_XML_PATCHED" in m and "para_1" in m]
        preserved = [m for m in caplog.messages
                     if "PARAGRAPH_BLOCK_PRESERVED_MULTI_SDT" in m and "para_1" in m]
        assert not patched
        assert preserved
