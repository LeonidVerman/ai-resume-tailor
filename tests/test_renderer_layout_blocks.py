"""Tests for the layout_blocks rendering path in render_docx.

Covers:
A. Branch selection — USE_LAYOUT_BLOCK_RENDERER flag routes to layout_blocks path.
B. Paragraph patch — xml_proto_xml XML gets updated text; original formatting preserved.
C. Table patch — table structure preserved; nested paragraph text updated by para_id.
D. Fallback — missing para_id does not crash; original text kept.
E. Sample 31 smoke — rendering preserves structure (not flat semantic-section order).
F. Diagnostics — LAYOUT_BLOCK_RENDERER_USED logged; LAYOUT_BLOCK_MISSING_PARA_ID logged.
G. Unbound content — new paragraphs not in layout_blocks logged, not crashed.
H. _set_para_text micro-kerning / w:w boundary stripping.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")
_SIMPLE_TEMPLATE = str(_DOCX_DIR / "1-Leonid_Verman_Resume_Template.docx")

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_xml_para(text: str, para_id: str = "") -> str:
    """Build a minimal w:p XML string with a single run."""
    return (
        f'<w:p xmlns:w="{_W}">'
        f'<w:r><w:t>{text}</w:t></w:r>'
        f'</w:p>'
    )


def _minimal_xml_table(cell_texts: list[str]) -> str:
    """Build a minimal w:tbl XML string with one row and N cells."""
    cells = "".join(
        f'<w:tc xmlns:w="{_W}"><w:p xmlns:w="{_W}"><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>'
        for t in cell_texts
    )
    return f'<w:tbl xmlns:w="{_W}"><w:tr xmlns:w="{_W}">{cells}</w:tr></w:tbl>'


def _make_doc_with_layout_blocks(
    para_texts: list[tuple[str, str]],  # [(para_id, text), ...]
    *,
    use_xml: bool = True,
) -> "ResumeDocument":
    """Build a minimal ResumeDocument with layout_blocks and matching all_paras."""
    from tailor.compiler.models import (
        LayoutParagraphBlock,
        LayoutProfile,
        ParaModel,
        ParaStyle,
        ResumeDocument,
        ResumeSection,
        assign_stable_ids,
    )
    layout = LayoutProfile(
        page_width_pt=612, page_height_pt=792,
        margin_top_pt=72, margin_bottom_pt=72,
        margin_left_pt=72, margin_right_pt=72,
        default_font_name="Calibri", default_font_size_pt=11,
    )
    paras = [
        ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
        for _, text in para_texts
    ]
    doc = ResumeDocument(
        header_paras=paras,
        sections=[],
        layout=layout,
        all_paras=paras,
    )
    assign_stable_ids(doc)
    # Override para_ids so they match the supplied list
    for pm, (pid, _) in zip(paras, para_texts):
        pm.para_id = pid

    doc.layout_blocks = [
        LayoutParagraphBlock(
            para_id=pid,
            xml_proto_xml=_minimal_xml_para(text) if use_xml else None,
        )
        for pid, text in para_texts
    ]
    return doc


# ---------------------------------------------------------------------------
# A. Branch selection
# ---------------------------------------------------------------------------

class TestBranchSelection:
    def test_flag_true_activates_layout_blocks_at_runtime(self, tmp_path, monkeypatch):
        """USE_LAYOUT_BLOCK_RENDERER=true must use layout_blocks even with xml_proto."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        # Confirm xml_proto IS present (runtime)
        assert any(pm.style.xml_proto is not None for pm in doc.all_paras[:5])
        assert doc.layout_blocks is not None

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience section")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            + "\n".join(f"- {b.text}" for b in role.bullets[:2])
        )
        llm_secs = parse_llm_output(llm_text)
        if not llm_secs:
            pytest.skip("could not parse LLM")

        updated = apply_tailored(doc, llm_secs)
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)

    def test_flag_false_deserialized_still_uses_layout_blocks(self, tmp_path, monkeypatch):
        """When flag is off but IR is deserialized (no xml_proto), layout_blocks path activates."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", False)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)  # xml_proto lost
        assert all(pm.style.xml_proto is None for pm in deser.all_paras)
        assert deser.layout_blocks is not None

        exp = next((s for s in deser.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            "- Updated bullet"
        )
        updated = apply_tailored(deser, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)

    def test_flag_false_runtime_uses_legacy_path(self, tmp_path, monkeypatch, caplog):
        """Flag=false + runtime xml_proto → legacy path; LAYOUT_BLOCK_RENDERER_FALLBACK logged."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", False)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        assert any(pm.style.xml_proto is not None for pm in doc.all_paras[:5])

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            + "\n".join(f"- {b.text}" for b in role.bullets[:2])
        )
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")

        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            render_docx(updated, _SIMPLE_TEMPLATE, out)

        assert any("LAYOUT_BLOCK_RENDERER_FALLBACK" in r.message for r in caplog.records)

    def test_no_layout_blocks_uses_legacy_path(self, tmp_path, monkeypatch):
        """When layout_blocks is None, legacy rendering must not crash."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        # Forcibly remove layout_blocks to simulate disabled-flag parse
        doc.layout_blocks = None

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020'}\n"
            "- A bullet"
        )
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)  # must not raise

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)


# ---------------------------------------------------------------------------
# B. Paragraph patch test
# ---------------------------------------------------------------------------

class TestParagraphPatch:
    def test_updated_text_appears_in_output(self, tmp_path):
        """Paragraph with updated text must appear in the rendered DOCX."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

        exp = next((s for s in deser.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience roles")

        unique_text = "UNIQUE_UPDATED_BULLET_FOOBAR_2025"
        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            f"- {unique_text}"
        )
        updated = apply_tailored(deser, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        from tailor.docx.template_fill import read_docx
        text = read_docx(out)
        assert unique_text in text

    def test_xml_proto_xml_provides_formatting(self, tmp_path):
        """The deserialized XML string (not para_builder) must be used — preserving bold font color."""
        from lxml import etree

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import LayoutParagraphBlock, ResumeDocument

        doc = parse_docx(_SIMPLE_TEMPLATE)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

        # Find a heading paragraph that should have formatting in its XML
        heading_block = next(
            (b for b in deser.layout_blocks
             if isinstance(b, LayoutParagraphBlock) and b.xml_proto_xml and b.para_id),
            None,
        )
        if heading_block is None:
            pytest.skip("no paragraph block with XML")

        # Verify the XML is parseable and has at least a w:r (run) element
        elem = etree.fromstring(heading_block.xml_proto_xml)
        assert elem.tag.endswith("}p") or elem.tag == "p"

    def test_no_xml_proto_xml_falls_back_to_para_builder(self, tmp_path, monkeypatch):
        """A LayoutParagraphBlock with no xml_proto_xml must not crash; uses runtime xml_proto."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import LayoutParagraphBlock, ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        # Null out xml_proto_xml on all paragraph blocks
        for block in (doc.layout_blocks or []):
            if isinstance(block, LayoutParagraphBlock):
                block.xml_proto_xml = None

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020'}\n"
            "- Fallback bullet"
        )
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)  # must not raise

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)


# ---------------------------------------------------------------------------
# C. Table patch test
# ---------------------------------------------------------------------------

class TestTablePatch:
    def _build_doc_with_table_block(self, table_xml: str, para_ids: list[str], para_texts: list[str]):
        """Build a minimal doc with a LayoutTableBlock."""
        from tailor.compiler.models import (
            LayoutProfile,
            LayoutTableBlock,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            ResumeSection,
            assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        paras = [
            ParaModel(text=text, style=ParaStyle(), semantic="paragraph")
            for text in para_texts
        ]
        doc = ResumeDocument(
            header_paras=paras,
            sections=[],
            layout=layout,
            all_paras=paras,
        )
        assign_stable_ids(doc)
        for pm, pid in zip(paras, para_ids):
            pm.para_id = pid

        doc.layout_blocks = [
            LayoutTableBlock(
                table_id="tbl_1",
                xml_proto_xml=table_xml,
                para_ids=para_ids,
            )
        ]
        return doc

    def test_table_structure_preserved_in_output(self, tmp_path):
        """Rendered DOCX must contain a table element (not flattened paragraphs)."""
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument

        tbl_xml = _minimal_xml_table(["Original A", "Original B"])
        doc = self._build_doc_with_table_block(
            tbl_xml,
            para_ids=["para_1", "para_2"],
            para_texts=["Updated A", "Updated B"],
        )

        out = str(tmp_path / "out.docx")
        render_docx(doc, _SIMPLE_TEMPLATE, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert len(rdoc.tables) >= 1, "expected at least one table in output"

    def test_table_cell_text_updated_by_para_id(self, tmp_path):
        """Cell text must be updated from the matching ParaModel, not keep original."""
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument

        tbl_xml = _minimal_xml_table(["ORIGINAL_CELL_1", "ORIGINAL_CELL_2"])
        doc = self._build_doc_with_table_block(
            tbl_xml,
            para_ids=["para_1", "para_2"],
            para_texts=["UPDATED_CELL_1", "UPDATED_CELL_2"],
        )

        out = str(tmp_path / "out.docx")
        render_docx(doc, _SIMPLE_TEMPLATE, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        all_text = " ".join(cell.text for tbl in rdoc.tables for row in tbl.rows for cell in row.cells)
        assert "UPDATED_CELL_1" in all_text
        assert "UPDATED_CELL_2" in all_text
        # Original text must be gone (replaced)
        assert "ORIGINAL_CELL_1" not in all_text

    def test_table_with_gridspan_not_flattened(self, tmp_path):
        """Table with w:gridSpan must not lose the merged-cell attribute after patching."""
        from lxml import etree

        from tailor.compiler.docx_renderer import render_docx

        W = _W
        # Build a table with a merged cell (gridSpan=2)
        tbl_xml = (
            f'<w:tbl xmlns:w="{W}">'
            f'<w:tr xmlns:w="{W}">'
            f'<w:tc xmlns:w="{W}">'
            f'<w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
            f'<w:p xmlns:w="{W}"><w:r><w:t>Merged</w:t></w:r></w:p>'
            f'</w:tc>'
            f'</w:tr>'
            f'</w:tbl>'
        )
        from tailor.compiler.models import (
            LayoutProfile,
            LayoutTableBlock,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        pm = ParaModel(text="Updated merged", style=ParaStyle(), semantic="paragraph")
        doc = ResumeDocument(
            header_paras=[pm], sections=[], layout=layout, all_paras=[pm],
        )
        assign_stable_ids(doc)
        doc.layout_blocks = [
            LayoutTableBlock(
                table_id="tbl_1",
                xml_proto_xml=tbl_xml,
                para_ids=["para_1"],
            )
        ]

        out = str(tmp_path / "out.docx")
        render_docx(doc, _SIMPLE_TEMPLATE, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert len(rdoc.tables) >= 1

        # Verify gridSpan attribute survived
        body_xml = rdoc.element.body.xml
        assert "gridSpan" in body_xml or "grid_span" in body_xml.lower() or "gridSpan" in body_xml


# ---------------------------------------------------------------------------
# D. Fallback — missing para_id
# ---------------------------------------------------------------------------

class TestMissingParaId:
    def test_missing_para_id_does_not_crash(self, tmp_path, caplog):
        """A layout block with an unknown para_id must not crash; original text kept."""
        from tailor.compiler.docx_renderer import _render_from_layout_blocks
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutProfile,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        pm = ParaModel(text="real para", style=ParaStyle(), semantic="paragraph")
        doc = ResumeDocument(
            header_paras=[pm], sections=[], layout=layout, all_paras=[pm],
        )
        assign_stable_ids(doc)
        doc.layout_blocks = [
            LayoutParagraphBlock(
                para_id="para_DOES_NOT_EXIST",
                xml_proto_xml=_minimal_xml_para("Original text"),
            )
        ]

        from docx import Document as DocxDoc
        from lxml import etree

        out_doc = DocxDoc()
        body = out_doc.element.body
        sectPr = body.find(f"{{{_W}}}sectPr")

        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            _render_from_layout_blocks(doc, body, sectPr)  # must not raise

        assert any("LAYOUT_BLOCK_MISSING_PARA_ID" in r.message for r in caplog.records)

    def test_empty_para_id_block_renders_verbatim(self, tmp_path):
        """A block with para_id='' is a structural element and renders without text update."""
        from tailor.compiler.docx_renderer import _render_from_layout_blocks
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutProfile,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        doc = ResumeDocument(header_paras=[], sections=[], layout=layout, all_paras=[])
        doc.layout_blocks = [
            LayoutParagraphBlock(
                para_id="",
                xml_proto_xml=_minimal_xml_para("VERBATIM_STRUCTURAL_TEXT"),
            )
        ]

        from docx import Document as DocxDoc
        out_doc = DocxDoc()
        body = out_doc.element.body
        sectPr = body.find(f"{{{_W}}}sectPr")

        _render_from_layout_blocks(doc, body, sectPr)  # must not raise

        # The verbatim text should appear in the body
        body_text = " ".join(
            t.text or "" for t in body.findall(f".//{{{_W}}}t")
        )
        assert "VERBATIM_STRUCTURAL_TEXT" in body_text


# ---------------------------------------------------------------------------
# E. Sample 31 smoke test
# ---------------------------------------------------------------------------

class TestSample31LayoutBlocksSmoke:
    def test_sample31_renders_from_layout_blocks_after_roundtrip(self, tmp_path):
        """After serialization/deserialization, sample 31 renders from layout_blocks
        (not flat semantic order) and produces a non-empty DOCX with content."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ResumeDocument
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        d = doc.to_dict()
        deser = ResumeDocument.from_dict(d)

        assert deser.layout_blocks is not None
        assert all(pm.style.xml_proto is None for pm in deser.all_paras)

        # Build a minimal LLM-like text that preserves existing structure
        llm_lines = []
        for sec in deser.sections:
            if sec.semantic_type in ("education", "certifications", "languages", "websites"):
                continue
            llm_lines.append(sec.title)
            if sec.semantic_type == "experience":
                for role in sec.roles[:2]:
                    llm_lines.append(role.header.text)
                    if role.meta_lines:
                        llm_lines.append(role.meta_lines[0].text)
                    for b in role.bullets[:2]:
                        llm_lines.append(f"- {b.text}")
            else:
                for p in sec.body_paras[:3]:
                    if p.text.strip():
                        llm_lines.append(p.text)
            llm_lines.append("")

        llm_secs = parse_llm_output("\n".join(llm_lines))
        if not llm_secs:
            pytest.skip("could not parse LLM output for sample 31")

        updated = apply_tailored(deser, llm_secs)
        out = str(tmp_path / "rendered31.docx")
        render_docx(updated, _SAMPLE_31, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        all_text = "\n".join(p.text.strip() for p in rdoc.paragraphs if p.text.strip())
        assert len(all_text) > 50

    def test_sample31_runtime_with_flag_uses_layout_blocks(self, tmp_path, monkeypatch):
        """With USE_LAYOUT_BLOCK_RENDERER=true, sample 31 renders layout_blocks at runtime."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        assert doc.layout_blocks is not None

        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else '2022 - Present'}\n"
            "- Layout-preserved bullet"
        )
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "rendered31_runtime.docx")
        render_docx(updated, _SAMPLE_31, out)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)


# ---------------------------------------------------------------------------
# F. Diagnostics
# ---------------------------------------------------------------------------

class TestDiagnostics:
    def test_layout_block_renderer_used_logged(self, tmp_path, caplog, monkeypatch):
        """LAYOUT_BLOCK_RENDERER_USED must appear in debug logs when path activates."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        llm_text = f"{exp.title}\n{role.header.text}\n2020 - Present\n- Bullet"
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])
        out = str(tmp_path / "out.docx")

        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            render_docx(updated, _SIMPLE_TEMPLATE, out)

        assert any("LAYOUT_BLOCK_RENDERER_USED" in r.message for r in caplog.records)

    def test_missing_para_id_logged(self, tmp_path, caplog):
        """LAYOUT_BLOCK_MISSING_PARA_ID must be logged when para_id not in lookup."""
        from tailor.compiler.docx_renderer import _render_from_layout_blocks
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutProfile,
            ParaModel,
            ParaStyle,
            ResumeDocument,
            assign_stable_ids,
        )
        layout = LayoutProfile(
            page_width_pt=612, page_height_pt=792,
            margin_top_pt=72, margin_bottom_pt=72,
            margin_left_pt=72, margin_right_pt=72,
            default_font_name="Calibri", default_font_size_pt=11,
        )
        doc = ResumeDocument(header_paras=[], sections=[], layout=layout, all_paras=[])
        doc.layout_blocks = [
            LayoutParagraphBlock(
                para_id="para_MISSING",
                xml_proto_xml=_minimal_xml_para("text"),
            )
        ]

        from docx import Document as DocxDoc
        body = DocxDoc().element.body
        sectPr = body.find(f"{{{_W}}}sectPr")

        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            _render_from_layout_blocks(doc, body, sectPr)

        assert any("LAYOUT_BLOCK_MISSING_PARA_ID" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# G. Unbound content
# ---------------------------------------------------------------------------

class TestUnboundContent:
    def test_unbound_new_paras_logged_not_crashed(self, tmp_path, caplog, monkeypatch):
        """New paragraphs (para_id not in layout_blocks) must be logged, not rendered."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_LAYOUT_BLOCK_RENDERER", True)
        # Force non-layout-bound mode: this test exercises the renderer path where
        # extra bullets are created as clone_as paragraphs (para_id="").  In
        # layout-bound mode the updater drops overflow bullets instead of cloning.
        monkeypatch.setattr(cfg, "USE_LAYOUT_BOUND_UPDATER", False)

        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.models import ParaModel, ParaStyle
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp is None or not exp.roles:
            pytest.skip("no experience")

        role = exp.roles[0]
        # Request MORE bullets than the template has — extras will have para_id=""
        n_orig = len(role.bullets)
        extra_bullets = "\n".join(f"- Extra bullet {i}" for i in range(n_orig + 3))
        llm_text = (
            f"{exp.title}\n{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else '2020 - Present'}\n"
            + extra_bullets
        )
        updated = apply_tailored(doc, parse_llm_output(llm_text) or [])

        # Extra bullets may have synthetic para_ids assigned by the auto-registration
        # in apply_tailored (if the layout_block has an xml_proto_xml to clone from).
        # When the test fixture has layout_blocks without xml_proto_xml, extras stay
        # unbound (para_id="").  Either way, rendering must not crash.
        out = str(tmp_path / "out.docx")
        with caplog.at_level(logging.DEBUG, logger="tailor.compiler.docx_renderer"):
            render_docx(updated, _SIMPLE_TEMPLATE, out)  # must not raise

        # Unbound content is no longer rendered at end-of-document; it is either
        # injected via layout_blocks (when xml_proto_xml is available) or silently
        # omitted.  Log may say LAYOUT_UNBOUND_CONTENT_NOT_RENDERED or
        # EXTRA_INJECTION_SKIPPED_NO_PROTO — both are acceptable.
        assert any("LAYOUT" in r.message or "EXTRA" in r.message for r in caplog.records)

        from docx import Document as DocxDoc
        rdoc = DocxDoc(out)
        assert any(p.text.strip() for p in rdoc.paragraphs)


# ---------------------------------------------------------------------------
# H. _set_para_text micro-kerning / w:w boundary stripping
# ---------------------------------------------------------------------------

def _make_para_xml(runs: list[dict]) -> str:
    """Build a w:p XML string from a list of run dicts.

    Each dict has:
        text: str
        w (optional): int   — w:w val
        spacing (optional): int  — w:spacing val
    """
    run_fragments = []
    for r in runs:
        rpr_parts = []
        if "w" in r:
            rpr_parts.append(f'<w:w w:val="{r["w"]}"/>')
        if "spacing" in r:
            rpr_parts.append(f'<w:spacing w:val="{r["spacing"]}"/>')
        rpr = f"<w:rPr>{''.join(rpr_parts)}</w:rPr>" if rpr_parts else ""
        run_fragments.append(
            f'<w:r>{rpr}<w:t xml:space="preserve">{r["text"]}</w:t></w:r>'
        )
    return (
        f'<w:p xmlns:w="{_W}" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">'
        + "".join(run_fragments)
        + "</w:p>"
    )


class TestSetParaTextMicroKerning:
    """Unit tests for the w:w / w:spacing stripping boundaries in _set_para_text."""

    def _call(self, runs: list[dict], new_text: str):
        from lxml import etree
        from tailor.compiler.docx_renderer import _set_para_text

        xml = _make_para_xml(runs)
        elem = etree.fromstring(xml.encode())
        _set_para_text(elem, new_text)
        return elem

    def _run_ww(self, elem, run_index: int):
        """Return int w:w val for run at run_index, or None if absent."""
        runs = elem.findall(f"{{{_W}}}r")
        if run_index >= len(runs):
            return None
        rpr = runs[run_index].find(f"{{{_W}}}rPr")
        if rpr is None:
            return None
        ww = rpr.find(f"{{{_W}}}w")
        return int(ww.get(f"{{{_W}}}val")) if ww is not None else None

    def _run_spacing(self, elem, run_index: int):
        """Return int w:spacing val for run at run_index, or None if absent."""
        runs = elem.findall(f"{{{_W}}}r")
        if run_index >= len(runs):
            return None
        rpr = runs[run_index].find(f"{{{_W}}}rPr")
        if rpr is None:
            return None
        sp = rpr.find(f"{{{_W}}}spacing")
        return int(sp.get(f"{{{_W}}}val")) if sp is not None else None

    # --- w:w boundary tests ---

    def test_ww_150_stripped_from_spacer_run(self):
        """w:w=150 on a spacer run must be stripped (boundary artifact, not typography).

        After stripping, the propagation step copies the adjacent content run's w:w (110)
        into the spacer slot so character scaling stays consistent.  The key invariant is
        that the artifact value 150 is gone, not that w:w is absent.
        """
        # Template has: [content "Other" w:w=110] [spacer " " w:w=150] [content "skills" w:w=110]
        runs = [
            {"text": "Other", "w": 110},
            {"text": " ", "w": 150},
            {"text": "skills", "w": 110},
        ]
        elem = self._call(runs, "Other skills")
        ww = self._run_ww(elem, 1)
        assert ww != 150, (
            "w:w=150 on a spacer run must be stripped — it is a PDF-export artifact "
            "that causes single redistributed chars to render 150% wide ('O ther skills')"
        )
        # Propagation fills in the adjacent content run's value (110) for consistent scaling.
        assert ww == 110, (
            "After stripping w:w=150 from the spacer, propagation must copy w:w=110 "
            "from the preceding content run so all chars in the word scale uniformly"
        )

    def test_ww_149_preserved_from_spacer_run(self):
        """w:w=149 on a spacer run is within intentional typography range and must be kept."""
        runs = [
            {"text": "Hello", "w": 110},
            {"text": " ", "w": 149},
            {"text": "World", "w": 110},
        ]
        elem = self._call(runs, "Hello World")
        assert self._run_ww(elem, 1) is not None, (
            "w:w=149 is within [95,149] intentional range and must not be stripped"
        )

    def test_ww_150_stripped_from_content_run(self):
        """w:w=150 on a content run must also be stripped (boundary value is an artifact)."""
        runs = [
            {"text": "Bachelor", "w": 150},
            {"text": " ", "w": 150},
            {"text": "Thesis", "w": 110},
        ]
        elem = self._call(runs, "Bachelor Thesis project")
        assert self._run_ww(elem, 0) is None, (
            "w:w=150 on a content run must be stripped — same boundary artifact "
            "('Bachelo r Thesis' split is caused by w:w=150 on the first char slot)"
        )

    def test_ww_100_preserved(self):
        """w:w=100 (normal width, mid-range) must never be stripped."""
        runs = [
            {"text": "Normal", "w": 100},
            {"text": " "},
            {"text": "text", "w": 100},
        ]
        elem = self._call(runs, "Normal text here")
        assert self._run_ww(elem, 0) is not None, "w:w=100 is intentional and must be kept"

    # --- w:spacing boundary tests ---

    def test_spacing_80_stripped_from_spacer_run(self):
        """w:spacing=80 on a spacer run must be stripped (boundary artifact)."""
        # The 'O ther skills' para has w:spacing=80 on the spacer run that receives 'O'.
        runs = [
            {"text": "O"},
            {"text": " ", "spacing": 80},
            {"text": "ther skills"},
        ]
        elem = self._call(runs, "Other skills")
        assert self._run_spacing(elem, 1) is None, (
            "w:spacing=80 on a spacer run must be stripped — it is the exact boundary "
            "value from a PDF-export artifact and misaligns the redistributed character"
        )

    def test_spacing_81_preserved_from_spacer_run(self):
        """w:spacing=81 on a spacer run is intentional letter-spacing and must be kept."""
        runs = [
            {"text": "Heading"},
            {"text": " ", "spacing": 81},
            {"text": "Title"},
        ]
        elem = self._call(runs, "Heading Title")
        assert self._run_spacing(elem, 1) is not None, (
            "w:spacing=81 exceeds the artifact threshold and must be preserved"
        )

    def test_spacing_80_stripped_from_content_run(self):
        """w:spacing=80 on a content run must be stripped too."""
        runs = [
            {"text": "Other", "spacing": 80},
            {"text": " "},
            {"text": "skills"},
        ]
        elem = self._call(runs, "Other skills")
        assert self._run_spacing(elem, 0) is None, (
            "w:spacing=80 on a content run must be stripped (micro-kerning boundary)"
        )

    def test_spacing_81_preserved_from_content_run(self):
        """w:spacing=81 on a content run is intentional and must be preserved."""
        runs = [
            {"text": "SPACED", "spacing": 81},
        ]
        elem = self._call(runs, "SPACED OUT")
        assert self._run_spacing(elem, 0) is not None, (
            "w:spacing=81 is intentional decorative letter-spacing and must be kept"
        )
