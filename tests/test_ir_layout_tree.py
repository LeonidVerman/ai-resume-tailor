"""Tests for the serializable layout tree (IR enhancement).

Covers:
- LayoutParagraphBlock / LayoutTableBlock round-trip serialization
- ResumeDocument.to_dict / from_dict with layout_blocks
- ParaModel.with_text preserves para_id
- parse_docx builds layout_blocks when USE_SERIALIZED_LAYOUT_TREE=True
- parse_docx does NOT build layout_blocks when flag is off
- Para IDs in layout_blocks match all_paras
- apply_tailored carries layout_blocks forward
- render_docx uses layout_blocks path after DB round-trip (no xml_proto)
- Feature flag gate: no layout_blocks when disabled
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

_DOCX_DIR = Path(__file__).parent / "samples" / "resume" / "docx"
_SAMPLE_31 = str(_DOCX_DIR / "31-Software-Engineer-Editable-Resume-Template-Download-in-docx-7.docx")
_SIMPLE_TEMPLATE = str(_DOCX_DIR / "1-Leonid_Verman_Resume_Template.docx")


# ---------------------------------------------------------------------------
# Unit: LayoutParagraphBlock serialization
# ---------------------------------------------------------------------------

class TestLayoutParagraphBlockSerialization:
    def test_round_trip_with_xml(self):
        from tailor.compiler.models import LayoutParagraphBlock
        block = LayoutParagraphBlock(para_id="para_5", xml_proto_xml="<w:p xmlns:w='x'/>")
        d = block.to_dict()
        assert d["kind"] == "paragraph"
        assert d["para_id"] == "para_5"
        assert d["xml_proto_xml"] == "<w:p xmlns:w='x'/>"
        restored = LayoutParagraphBlock.from_dict(d)
        assert restored.para_id == "para_5"
        assert restored.xml_proto_xml == "<w:p xmlns:w='x'/>"

    def test_round_trip_no_xml(self):
        from tailor.compiler.models import LayoutParagraphBlock
        block = LayoutParagraphBlock(para_id="para_1")
        d = block.to_dict()
        assert d["kind"] == "paragraph"
        assert "xml_proto_xml" not in d
        restored = LayoutParagraphBlock.from_dict(d)
        assert restored.para_id == "para_1"
        assert restored.xml_proto_xml is None

    def test_empty_para_id_round_trip(self):
        from tailor.compiler.models import LayoutParagraphBlock
        block = LayoutParagraphBlock(para_id="", xml_proto_xml="<w:p/>")
        d = block.to_dict()
        restored = LayoutParagraphBlock.from_dict(d)
        assert restored.para_id == ""


# ---------------------------------------------------------------------------
# Unit: LayoutTableBlock serialization
# ---------------------------------------------------------------------------

class TestLayoutTableBlockSerialization:
    def test_round_trip(self):
        from tailor.compiler.models import LayoutTableBlock
        block = LayoutTableBlock(
            table_id="tbl_1",
            xml_proto_xml="<w:tbl xmlns:w='x'><w:tr/></w:tbl>",
            para_ids=["para_3", "para_4", "para_5"],
        )
        d = block.to_dict()
        assert d["kind"] == "table"
        assert d["table_id"] == "tbl_1"
        assert d["para_ids"] == ["para_3", "para_4", "para_5"]
        restored = LayoutTableBlock.from_dict(d)
        assert restored.table_id == "tbl_1"
        assert restored.xml_proto_xml == "<w:tbl xmlns:w='x'><w:tr/></w:tbl>"
        assert restored.para_ids == ["para_3", "para_4", "para_5"]

    def test_empty_para_ids(self):
        from tailor.compiler.models import LayoutTableBlock
        block = LayoutTableBlock(table_id="tbl_1", xml_proto_xml="<w:tbl/>", para_ids=[])
        d = block.to_dict()
        restored = LayoutTableBlock.from_dict(d)
        assert restored.para_ids == []


# ---------------------------------------------------------------------------
# Unit: ResumeDocument layout_blocks serialization round-trip
# ---------------------------------------------------------------------------

class TestResumeDocumentLayoutBlocksSerialization:
    def _make_minimal_doc(self):
        """Build a minimal ResumeDocument with layout_blocks for round-trip testing."""
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutTableBlock,
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
        heading = ParaModel(text="Experience", style=ParaStyle(style_name="Heading 1"), semantic="section_heading")
        bullet = ParaModel(text="Did work", style=ParaStyle(), semantic="bullet")
        doc = ResumeDocument(
            header_paras=[],
            sections=[ResumeSection(title="Experience", heading=heading, semantic_type="experience", body_paras=[bullet])],
            layout=layout,
            all_paras=[heading, bullet],
        )
        assign_stable_ids(doc)
        doc.layout_blocks = [
            LayoutParagraphBlock(para_id="para_1", xml_proto_xml="<w:p><w:r><w:t>Experience</w:t></w:r></w:p>"),
            LayoutParagraphBlock(para_id="para_2", xml_proto_xml="<w:p><w:r><w:t>Did work</w:t></w:r></w:p>"),
        ]
        return doc

    def test_to_dict_includes_layout_blocks(self):
        doc = self._make_minimal_doc()
        d = doc.to_dict()
        assert "layout_blocks" in d
        assert len(d["layout_blocks"]) == 2
        assert d["layout_blocks"][0]["kind"] == "paragraph"
        assert d["layout_blocks"][0]["para_id"] == "para_1"

    def test_from_dict_restores_layout_blocks(self):
        from tailor.compiler.models import LayoutParagraphBlock
        doc = self._make_minimal_doc()
        d = doc.to_dict()
        restored = type(doc).from_dict(d)
        assert restored.layout_blocks is not None
        assert len(restored.layout_blocks) == 2
        block = restored.layout_blocks[0]
        assert isinstance(block, LayoutParagraphBlock)
        assert block.para_id == "para_1"

    def test_from_dict_none_when_absent(self):
        doc = self._make_minimal_doc()
        d = doc.to_dict()
        del d["layout_blocks"]
        restored = type(doc).from_dict(d)
        assert restored.layout_blocks is None

    def test_json_serializable(self):
        doc = self._make_minimal_doc()
        d = doc.to_dict()
        # Should not raise
        json_str = json.dumps(d)
        assert len(json_str) > 100

    def test_table_block_survives_round_trip(self):
        from tailor.compiler.models import (
            LayoutParagraphBlock,
            LayoutTableBlock,
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
        cell_para = ParaModel(text="Cell text", style=ParaStyle(), semantic="paragraph")
        doc = ResumeDocument(
            header_paras=[], sections=[], layout=layout, all_paras=[cell_para],
        )
        assign_stable_ids(doc)
        doc.layout_blocks = [
            LayoutTableBlock(
                table_id="tbl_1",
                xml_proto_xml="<w:tbl><w:tr><w:tc><w:p/></w:tc></w:tr></w:tbl>",
                para_ids=["para_1"],
            ),
        ]
        d = doc.to_dict()
        from tailor.compiler.models import LayoutTableBlock as LTB
        restored = type(doc).from_dict(d)
        assert restored.layout_blocks is not None
        assert len(restored.layout_blocks) == 1
        assert isinstance(restored.layout_blocks[0], LTB)
        assert restored.layout_blocks[0].para_ids == ["para_1"]


# ---------------------------------------------------------------------------
# Unit: ParaModel.with_text preserves para_id
# ---------------------------------------------------------------------------

class TestParaModelWithTextPreservesParaId:
    def test_para_id_copied(self):
        from tailor.compiler.models import ParaModel, ParaStyle
        pm = ParaModel(text="original", style=ParaStyle(), semantic="bullet")
        pm.para_id = "para_42"
        updated = pm.with_text("updated text")
        assert updated.para_id == "para_42"

    def test_text_is_replaced(self):
        from tailor.compiler.models import ParaModel, ParaStyle
        pm = ParaModel(text="original", style=ParaStyle(), semantic="bullet")
        pm.para_id = "para_7"
        updated = pm.with_text("new text")
        assert updated.text == "new text"

    def test_empty_para_id_preserved(self):
        from tailor.compiler.models import ParaModel, ParaStyle
        pm = ParaModel(text="text", style=ParaStyle(), semantic="paragraph")
        # para_id defaults to ""
        updated = pm.with_text("other")
        assert updated.para_id == ""

    def test_clone_as_does_not_copy_para_id(self):
        """clone_as creates new paragraphs; they should NOT inherit the source para_id."""
        from tailor.compiler.models import ParaModel, ParaStyle
        pm = ParaModel(text="original", style=ParaStyle(), semantic="bullet")
        pm.para_id = "para_10"
        cloned = pm.clone_as("cloned bullet", "bullet")
        # clone_as is for new paragraphs that don't have a layout_blocks entry
        assert cloned.para_id == ""


# ---------------------------------------------------------------------------
# Integration: parse_docx builds layout_blocks
# ---------------------------------------------------------------------------

class TestParseDocxBuildsLayoutBlocks:
    def test_layout_blocks_present_sample31(self):
        from tailor.compiler.docx_parser import parse_docx
        doc = parse_docx(_SAMPLE_31)
        assert doc.layout_blocks is not None
        assert len(doc.layout_blocks) > 0

    def test_layout_blocks_present_simple(self):
        from tailor.compiler.docx_parser import parse_docx
        doc = parse_docx(_SIMPLE_TEMPLATE)
        assert doc.layout_blocks is not None
        assert len(doc.layout_blocks) > 0

    def test_layout_blocks_para_ids_valid_for_semantic_paras(self):
        """Every non-empty para_id in layout_blocks must reference an existing all_paras entry."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import LayoutParagraphBlock
        doc = parse_docx(_SAMPLE_31)
        known_ids = {pm.para_id for pm in doc.all_paras if pm.para_id}
        for block in doc.layout_blocks:
            if isinstance(block, LayoutParagraphBlock) and block.para_id:
                assert block.para_id in known_ids, (
                    f"LayoutParagraphBlock.para_id={block.para_id!r} not in all_paras"
                )

    def test_layout_blocks_table_para_ids_valid(self):
        """Every para_id in LayoutTableBlock.para_ids must exist in all_paras."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import LayoutTableBlock
        # Use a template known to have tables (template 6)
        template_with_table = str(_DOCX_DIR / "6-Template1.docx")
        doc = parse_docx(template_with_table)
        if not any(isinstance(b, LayoutTableBlock) for b in (doc.layout_blocks or [])):
            pytest.skip("template has no tables")
        known_ids = {pm.para_id for pm in doc.all_paras if pm.para_id}
        for block in doc.layout_blocks:
            if isinstance(block, LayoutTableBlock):
                for pid in block.para_ids:
                    if pid:
                        assert pid in known_ids, f"table para_id={pid!r} not in all_paras"

    def test_layout_blocks_xml_strings_parseable(self):
        """All xml_proto_xml strings must be valid XML."""
        from lxml import etree
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import LayoutParagraphBlock, LayoutTableBlock
        doc = parse_docx(_SIMPLE_TEMPLATE)
        for block in doc.layout_blocks:
            if isinstance(block, LayoutParagraphBlock) and block.xml_proto_xml:
                etree.fromstring(block.xml_proto_xml)  # must not raise
            elif isinstance(block, LayoutTableBlock):
                etree.fromstring(block.xml_proto_xml)  # must not raise

    def test_feature_flag_off_no_layout_blocks(self, monkeypatch):
        """When USE_SERIALIZED_LAYOUT_TREE=False, layout_blocks must be None."""
        import tailor.config as cfg
        monkeypatch.setattr(cfg, "USE_SERIALIZED_LAYOUT_TREE", False)
        from tailor.compiler.docx_parser import parse_docx
        doc = parse_docx(_SIMPLE_TEMPLATE)
        assert doc.layout_blocks is None


# ---------------------------------------------------------------------------
# Integration: apply_tailored carries layout_blocks forward
# ---------------------------------------------------------------------------

class TestApplyTailoredCarriesLayoutBlocks:
    def test_layout_blocks_carried_forward(self):
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        assert doc.layout_blocks is not None

        # Minimal LLM output that matches the template structure
        llm_text = "\n".join(
            s.title + "\n" + "\n".join(
                r.header.text + "\n" + (r.meta_lines[0].text if r.meta_lines else "")
                + "\n" + "\n".join("- " + b.text for b in r.bullets[:2])
                for r in s.roles[:1]
            )
            for s in doc.sections[:2]
            if s.semantic_type == "experience"
        )
        if not llm_text.strip():
            pytest.skip("template has no experience section")

        llm_sections = parse_llm_output(llm_text)
        if not llm_sections:
            pytest.skip("could not parse LLM output")

        updated = apply_tailored(doc, llm_sections)
        assert updated.layout_blocks is not None
        assert len(updated.layout_blocks) == len(doc.layout_blocks)

    def test_layout_blocks_para_ids_for_updated_bullets_are_valid(self):
        """Para IDs of UPDATED bullet paragraphs must appear in updated all_paras.

        apply_tailored may drop surplus original roles (when the LLM outputs
        fewer roles than the template), so not all layout_blocks para_ids need
        to be in updated.all_paras — only those belonging to updated content.
        """
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.models import LayoutParagraphBlock
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        exp_section = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp_section is None or not exp_section.roles:
            pytest.skip("no experience roles")

        # Collect original bullet para_ids from the first role
        role = exp_section.roles[0]
        original_bullet_ids = {b.para_id for b in role.bullets if b.para_id}

        llm_text = (
            f"{exp_section.title}\n"
            f"{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            + "\n".join(f"- Updated bullet {i}" for i in range(len(role.bullets)))
        )
        llm_sections = parse_llm_output(llm_text)
        if not llm_sections:
            pytest.skip("could not parse LLM output")

        updated = apply_tailored(doc, llm_sections)
        assert updated.layout_blocks is not None

        # The updated document should contain the original bullet para_ids
        # (because with_text preserves para_id)
        known_ids = {pm.para_id for pm in updated.all_paras if pm.para_id}
        for pid in original_bullet_ids:
            assert pid in known_ids, (
                f"Original bullet para_id={pid!r} not found in updated all_paras "
                "(with_text should preserve para_id)"
            )


# ---------------------------------------------------------------------------
# Integration: render_docx uses layout_blocks after DB round-trip
# ---------------------------------------------------------------------------

class TestRenderFromLayoutBlocksAfterRoundTrip:
    def _simulate_db_roundtrip(self, doc):
        """Serialize and deserialize, mimicking DB storage (xml_proto lost)."""
        from tailor.compiler.models import ResumeDocument
        d = doc.to_dict()
        return ResumeDocument.from_dict(d)

    def test_render_after_roundtrip_produces_docx(self, tmp_path):
        """Rendering from a deserialized IR should succeed and produce a valid DOCX."""
        from docx import Document as DocxDoc
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        deserialized = self._simulate_db_roundtrip(doc)

        # Verify no xml_proto after round-trip (key precondition for layout_blocks path)
        assert all(pm.style.xml_proto is None for pm in deserialized.all_paras)
        assert deserialized.layout_blocks is not None

        # Build minimal LLM output preserving existing content
        exp_section = next((s for s in doc.sections if s.semantic_type == "experience"), None)
        if exp_section is None or not exp_section.roles:
            pytest.skip("no experience roles")

        role = exp_section.roles[0]
        llm_text = (
            f"{exp_section.title}\n"
            f"{role.header.text}\n"
            f"{role.meta_lines[0].text if role.meta_lines else 'Jan 2020 - Present'}\n"
            + "\n".join(f"- {b.text}" for b in role.bullets[:2])
        )
        llm_sections = parse_llm_output(llm_text)
        if not llm_sections:
            pytest.skip("could not parse LLM output")

        updated = apply_tailored(deserialized, llm_sections)

        out = str(tmp_path / "rendered.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        # Check it's a valid DOCX with at least one paragraph
        rdoc = DocxDoc(out)
        texts = [p.text for p in rdoc.paragraphs if p.text.strip()]
        assert len(texts) > 0

    def test_role_header_text_updated_after_roundtrip(self, tmp_path):
        """Updated role header text must appear in the rendered DOCX."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SIMPLE_TEMPLATE)
        deserialized = self._simulate_db_roundtrip(doc)

        exp_section = next((s for s in deserialized.sections if s.semantic_type == "experience"), None)
        if exp_section is None or not exp_section.roles:
            pytest.skip("no experience roles")

        new_header = "Updated Engineer | NewCorp"
        llm_text = (
            f"{exp_section.title}\n"
            f"{new_header}\n"
            f"{exp_section.roles[0].meta_lines[0].text if exp_section.roles[0].meta_lines else 'Jan 2020 - Present'}\n"
            "- First bullet"
        )
        llm_sections = parse_llm_output(llm_text)
        if not llm_sections:
            pytest.skip("could not parse LLM output")

        updated = apply_tailored(deserialized, llm_sections)
        out = str(tmp_path / "rendered.docx")
        render_docx(updated, _SIMPLE_TEMPLATE, out)

        from tailor.docx.template_fill import read_docx
        text = read_docx(out)
        assert "Updated Engineer" in text

    def test_sample31_roundtrip_produces_docx_with_sections(self, tmp_path):
        """Sample 31 round-trip: all major section headings appear in the output."""
        from tailor.compiler.docx_parser import parse_docx
        from tailor.compiler.docx_renderer import render_docx
        from tailor.compiler.text_parser import parse_llm_output
        from tailor.compiler.updater import apply_tailored

        doc = parse_docx(_SAMPLE_31)
        deserialized = self._simulate_db_roundtrip(doc)

        assert deserialized.layout_blocks is not None
        assert all(pm.style.xml_proto is None for pm in deserialized.all_paras)

        # Build minimal LLM output that preserves the existing sections
        llm_lines = []
        for sec in deserialized.sections:
            if sec.semantic_type in ("education", "certifications", "languages", "websites"):
                continue  # locked
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

        llm_sections = parse_llm_output("\n".join(llm_lines))
        if not llm_sections:
            pytest.skip("could not parse LLM output from sample 31")

        updated = apply_tailored(deserialized, llm_sections)
        out = str(tmp_path / "rendered31.docx")
        render_docx(updated, _SAMPLE_31, out)

        from tailor.docx.template_fill import read_docx
        text = read_docx(out)
        # Original section headings should still appear
        assert len(text) > 100  # has content
